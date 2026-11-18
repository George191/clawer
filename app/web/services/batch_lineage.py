"""Read the exact local committed-batch evidence. No source access or target data reads."""

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, text, tuple_
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.batch_lineage import (
    BatchLineageDetail, BatchLineageEdge, BatchLineageEntry, BatchLineageJob, BatchLineageKind, BatchLineagePage,
)
from app.web.api.models.batch_syncs import (
    INCREMENTAL_TARGET, TARGET_SCHEMA, BatchSyncAttemptRow, BatchSyncReceiptRow, BatchSyncRequestRow, BatchSyncWatermarkRow,
    ManagedBatchLineageAsset,
)
from app.web.api.models.batch_sync_schedules import BatchSyncScheduleJobRow
from app.web.services.batch_sync_incremental import batch_lineage_enabled, require_target_shape, watermark, watermark_scope
from app.web.services.batch_syncs import _SCHEDULE_ACTOR, _target_metadata

_ATTEMPT, _RECEIPT, _REQUEST, _JOB = BatchSyncAttemptRow, BatchSyncReceiptRow, BatchSyncRequestRow, BatchSyncScheduleJobRow
_RECEIPT_SCOPE = """from_exclusive IS NOT NULL AND source_identity_verified IS TRUE
                     AND source_schema = 'batch_sync_stream' AND source_table = 'batches'
                     AND target_table = 'committed_batch_fixture'"""
_POSITIVE = "outcome = 'committed' AND rows_affected > 0"
_ASSET = f"{TARGET_SCHEMA}.{INCREMENTAL_TARGET}"
_SETTLED = ("committed", "no_new_data", "replayed")
_JOB_STATES = ("pending", "dispatched", "committed", "no_new_data", "rejected", "outcome_unknown", "expired")
_ATTEMPT_CODES = frozenset({
    "incremental_disabled", "incremental_scope_changed", "incremental_binding_changed",
    "source_identity_mismatch", "source_identity_review_required", "source_revision_changed", "source_timeout",
    "source_unavailable", "source_changed", "source_limit", "source_rejected", "target_busy", "target_changed",
    "target_rewritten", "target_rejected", "plan_changed", "schedule_actor_revoked", "schedule_expired",
    "schedule_stale", "schedule_disabled", "schedule_scope_mismatch", "schedule_not_due", "definition_rejected",
    "execution_unavailable", "outcome_unknown",
})


@dataclass(frozen=True)
class _Record:
    row: BatchSyncAttemptRow | BatchSyncReceiptRow
    revision: datetime

    @property
    def kind(self) -> BatchLineageKind:
        return "attempt" if isinstance(self.row, BatchSyncAttemptRow) else "receipt"

    @property
    def stamp(self) -> datetime:
        return self.row.started_at if isinstance(self.row, BatchSyncAttemptRow) else self.row.recorded_at


def _timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("timezone is required")
    return result.astimezone(timezone.utc)


def _window(
    raw_from: datetime | None, raw_to: datetime | None, cursor: str | None,
) -> tuple[datetime, datetime, tuple[datetime, UUID, BatchLineageKind] | None]:
    try:
        if (raw_from is None) != (raw_to is None):
            raise ValueError("Both endpoints are required")
        position = None
        if cursor:
            scope, left, right, stamp, identity, kind = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            start, end = _timestamp(left), _timestamp(right)
            if scope != _ASSET or kind not in ("attempt", "receipt"):
                raise ValueError("cursor scope")
            if raw_from is not None and (start != raw_from or end != raw_to):
                raise ValueError("cursor window")
            position = _timestamp(stamp), UUID(identity), kind
        elif raw_from is not None:
            start, end = raw_from.astimezone(timezone.utc), raw_to.astimezone(timezone.utc)
        else:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=30)
        if not timedelta(0) < end - start <= timedelta(days=31) or (position and not start <= position[0] < end):
            raise ValueError("window")
        return start, end, position
    except (TypeError, ValueError, AttributeError, OverflowError, binascii.Error):
        raise HTTPException(422, "Use a valid timeline cursor and a window of no more than 31 days") from None


def _cursor(item: _Record, start: datetime, end: datetime) -> str:
    data = [_ASSET, start.isoformat(), end.isoformat(), item.stamp.isoformat(), str(item.row.id), item.kind]
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode("ascii")).decode("ascii")


def _require_asset(layer: str, table: str) -> None:
    if not batch_lineage_enabled() or (layer, table) != (TARGET_SCHEMA, INCREMENTAL_TARGET):
        raise HTTPException(404, "Managed incremental timeline is unavailable for this destination")


async def _limits(session: AsyncSession) -> None:
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL lock_timeout = '750ms'"))
    await session.execute(text("SET LOCAL statement_timeout = '2s'"))


async def available_batch_lineage_asset(session: AsyncSession) -> ManagedBatchLineageAsset | None:
    if not batch_lineage_enabled():
        return None
    await _limits(session)
    if await watermark(session) is None:
        try:
            target, columns = await _target_metadata(session, INCREMENTAL_TARGET)
            require_target_shape(target, columns)
        except HTTPException as error:
            if error.status_code != 422:
                raise
            return None
    return ManagedBatchLineageAsset(schema_name=TARGET_SCHEMA, table=INCREMENTAL_TARGET)


def _receipt_filters(binding: BatchSyncWatermarkRow):
    # Literal predicate exactly matches the partial indexes, including under prepared generic plans.
    return (text(_RECEIPT_SCOPE), _RECEIPT.connection_id == binding.connection_id,
            _RECEIPT.through_inclusive <= binding.applied_through)


def _receipts(binding: BatchSyncWatermarkRow):
    return select(_RECEIPT).where(*_receipt_filters(binding))


def _attempts():
    return select(_ATTEMPT, _REQUEST.plan_updated_at).join(_REQUEST, and_(
        _REQUEST.plan_id == _ATTEMPT.plan_id, _REQUEST.request_id == _ATTEMPT.request_id,
    ))


def _linked_attempt_filter():
    return and_(
        _ATTEMPT.receipt_id == _RECEIPT.id, _ATTEMPT.plan_id == _RECEIPT.plan_id,
        _ATTEMPT.request_id == _RECEIPT.request_id, _REQUEST.plan_updated_at == _RECEIPT.plan_updated_at,
        _ATTEMPT.initiated_by == _RECEIPT.initiated_by,
        (_ATTEMPT.status == _RECEIPT.outcome) | (_ATTEMPT.status == "replayed"),
    )


def _attempt_sources(binding: BatchSyncWatermarkRow):
    return (
        _attempts().where(_ATTEMPT.incremental_scope == watermark_scope(binding)),
        _attempts().join(_RECEIPT, _linked_attempt_filter()).where(
            text("incremental_scope IS NULL AND receipt_id IS NOT NULL"), *_receipt_filters(binding),
        ),
    )


def _ordered(query, stamp, identity, kind, start, end, position):
    query = query.where(stamp >= start, stamp < end)
    if position is not None:
        when, item, before_kind = position
        pair, boundary = tuple_(stamp, identity), tuple_(when, item)
        query = query.where(pair <= boundary if kind < before_kind else pair < boundary)
    return query.order_by(stamp.desc(), identity.desc())


async def _is_current(session: AsyncSession, binding: BatchSyncWatermarkRow) -> bool:
    """Use only current catalog identity and shape; not live source health."""
    try:
        # A catalog snapshot alone cannot guarantee the fixed name still opens the old
        # relation after concurrent DDL. Pin that exact name; ordinary row writers are compatible.
        async with session.begin_nested():
            await session.execute(text(
                f'LOCK TABLE ONLY "{TARGET_SCHEMA}"."{INCREMENTAL_TARGET}" IN ACCESS SHARE MODE NOWAIT',
            ))
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) in {"42P01", "3F000", "42809"}:
            return False
        raise
    try:
        current, columns = await _target_metadata(session, INCREMENTAL_TARGET)
        require_target_shape(current, columns)
    except HTTPException:
        return False
    if (current["table_oid"], current["table_node"]) != (binding.target_oid, binding.target_file_node):
        return False
    return bool(await session.scalar(text("""
        SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_locks WHERE pid=pg_catalog.pg_backend_pid()
                      AND locktype='relation' AND relation=CAST(:identity AS oid)
                      AND mode='AccessShareLock' AND granted)
    """), {"identity": binding.target_oid}))


def _attempt_receipt(record: _Record, receipt: BatchSyncReceiptRow | None) -> BatchSyncReceiptRow | None:
    attempt = record.row
    if (not isinstance(attempt, BatchSyncAttemptRow) or attempt.status not in _SETTLED or receipt is None
        or attempt.receipt_id != receipt.id or attempt.plan_id != receipt.plan_id
        or attempt.request_id != receipt.request_id or record.revision != receipt.plan_updated_at
        or attempt.initiated_by != receipt.initiated_by
        or attempt.status not in (receipt.outcome, "replayed")):
        return None
    return receipt


def _receipt_job(receipt: BatchSyncReceiptRow, job: BatchSyncScheduleJobRow | None) -> bool:
    return bool(job and receipt.initiated_by == _SCHEDULE_ACTOR and job.id == receipt.request_id
                and job.plan_id == receipt.plan_id and job.plan_updated_at == receipt.plan_updated_at
                and job.source_updated_at == receipt.source_updated_at and job.state == receipt.outcome
                and job.receipt_id == receipt.id)


def _attempt_job(record: _Record, job: BatchSyncScheduleJobRow | None, receipts) -> bool:
    attempt = record.row
    return bool(job and isinstance(attempt, BatchSyncAttemptRow) and attempt.initiated_by == _SCHEDULE_ACTOR
                and job.id == attempt.scheduled_job_id == attempt.request_id and job.plan_id == attempt.plan_id
                and job.plan_updated_at == record.revision and job.state in _JOB_STATES
                and (job.receipt_id is None and attempt.status not in _SETTLED
                     or ((receipt := receipts.get(job.receipt_id)) is not None and _receipt_job(receipt, job)
                         and (attempt.status not in _SETTLED or _attempt_receipt(record, receipt) is not None))))


async def _provenance(session: AsyncSession, binding: BatchSyncWatermarkRow, records: list[_Record]):
    receipts = {item.row.id: item.row for item in records if isinstance(item.row, BatchSyncReceiptRow)}
    attempts = [item for item in records if isinstance(item.row, BatchSyncAttemptRow)]
    job_ids = {item.row.scheduled_job_id for item in attempts if item.row.scheduled_job_id is not None}
    job_ids.update(receipt.request_id for receipt in receipts.values() if receipt.initiated_by == _SCHEDULE_ACTOR)
    jobs = {job.id: job for job in (await session.scalars(select(_JOB).where(_JOB.id.in_(job_ids)))).all()} if job_ids else {}

    linked = {item.row.receipt_id for item in attempts if item.row.receipt_id is not None}
    linked.update(job.receipt_id for job in jobs.values() if job.receipt_id is not None)
    linked -= receipts.keys()
    if linked:
        receipts.update({row.id: row for row in (await session.scalars(_receipts(binding).where(_RECEIPT.id.in_(linked)))).all()})

    # Exists (rather than fetching arbitrary numbers of replay attempts) provides exact causal proof.
    receipt_ids = {item.row.id for item in records if isinstance(item.row, BatchSyncReceiptRow)}
    manual, scheduled = set(), set()
    if receipt_ids:
        original = select(_ATTEMPT.id).join(_REQUEST, and_(
            _REQUEST.plan_id == _ATTEMPT.plan_id, _REQUEST.request_id == _ATTEMPT.request_id,
        )).where(_linked_attempt_filter(), _ATTEMPT.status == _RECEIPT.outcome,
                 _ATTEMPT.incremental_scope.is_(None) | (_ATTEMPT.incremental_scope == watermark_scope(binding)),
                 ).correlate(_RECEIPT)
        proof = select(
            _RECEIPT.id,
            and_(_RECEIPT.initiated_by != _SCHEDULE_ACTOR, original.where(_ATTEMPT.scheduled_job_id.is_(None)).exists()),
            and_(_RECEIPT.initiated_by == _SCHEDULE_ACTOR,
                 original.where(_ATTEMPT.scheduled_job_id == _RECEIPT.request_id).exists()),
        ).where(_RECEIPT.id.in_(receipt_ids))
        for identity, is_manual, is_scheduled in (await session.execute(proof)).all():
            if is_manual:
                manual.add(identity)
            if is_scheduled:
                scheduled.add(identity)
    return receipts, jobs, manual, scheduled


def _entry(record: _Record, current: bool, evidence) -> tuple[BatchLineageEntry, BatchLineageJob | None]:
    receipts, jobs, manual, scheduled = evidence
    row = record.row
    if isinstance(row, BatchSyncAttemptRow):
        valid = _attempt_receipt(record, receipts.get(row.receipt_id))
        job = jobs.get(row.scheduled_job_id)
        daily = _attempt_job(record, job, receipts)
        was_manual = row.scheduled_job_id is None and row.initiated_by != _SCHEDULE_ACTOR
        entry = BatchLineageEntry(
            id=row.id, kind="attempt", occurredAt=record.stamp, planUpdatedAt=record.revision,
            status=("outcome_unknown" if row.status in _SETTLED and valid is None else row.status),
            finishedAt=row.finished_at, receiptId=valid.id if valid is not None else None,
            physicalScope="current" if current else "archived",
            trigger="daily_utc" if daily else "manual" if was_manual else None,
            code=row.code if row.code in _ATTEMPT_CODES else None,
        )
    else:
        job = jobs.get(row.request_id)
        daily = row.id in scheduled and _receipt_job(row, job)
        entry = BatchLineageEntry(
            id=row.id, kind="receipt", occurredAt=record.stamp, planUpdatedAt=record.revision,
            status=row.outcome, receiptId=row.id, physicalScope="current" if current else "archived",
            trigger="daily_utc" if daily else "manual" if row.id in manual else None,
            rowsAffected=row.rows_affected, fromExclusive=str(row.from_exclusive), throughInclusive=str(row.through_inclusive),
            sourceHighWater=str(row.source_high_water), eventsRead=row.events_read,
        )
    details = (BatchLineageJob(slot=job.slot, state=job.state, dispatchedAt=job.dispatched_at, finishedAt=job.finished_at)
               if daily else None)
    return entry, details


async def list_batch_lineage(
    session: AsyncSession, layer: str, table: str, raw_from: datetime | None, raw_to: datetime | None,
    limit: int, cursor: str | None,
) -> BatchLineagePage:
    _require_asset(layer, table)
    start, end, position = _window(raw_from, raw_to, cursor)
    await _limits(session)
    binding = await watermark(session)
    if binding is None:
        return BatchLineagePage(lineage=BatchLineageEdge(state="unreviewed"), entries=[], from_at=start, to=end)
    order = _ordered(_receipts(binding), _RECEIPT.recorded_at, _RECEIPT.id, "receipt", start, end, position)
    receipts = (await session.scalars(order.limit(limit + 1))).all()
    candidates = [_Record(row, row.plan_updated_at) for row in receipts]
    for query in _attempt_sources(binding):
        order = _ordered(query, _ATTEMPT.started_at, _ATTEMPT.id, "attempt", start, end, position)
        candidates.extend(_Record(row, revision) for row, revision in (await session.execute(order.limit(limit + 1))).all())
    candidates.sort(key=lambda item: (item.stamp, item.row.id, item.kind), reverse=True)
    page = candidates[:limit]
    evidence = await _provenance(session, binding, page)
    current = await _is_current(session, binding)
    newest = (await session.scalar(_receipts(binding).where(text(_POSITIVE)).order_by(
        _RECEIPT.recorded_at.desc(), _RECEIPT.id.desc(),
    ).limit(1))) if current else None
    return BatchLineagePage(
        lineage=BatchLineageEdge(
            state="archived" if not current else "observed" if newest is not None else "reviewed",
            appliedThrough=str(binding.applied_through) if current else None, latestPositiveReceiptId=newest.id if newest is not None else None,
            latestPositiveRecordedAt=newest.recorded_at if newest is not None else None,
        ),
        entries=[_entry(item, current, evidence)[0] for item in page],
        nextCursor=_cursor(page[-1], start, end) if len(candidates) > limit else None,
        from_at=start, to=end,
    )


async def read_batch_lineage(
    session: AsyncSession, layer: str, table: str, kind: BatchLineageKind, identity: UUID,
) -> BatchLineageDetail:
    _require_asset(layer, table)
    await _limits(session)
    binding = await watermark(session)
    record = None
    if binding is not None and kind == "receipt":
        row = await session.scalar(_receipts(binding).where(_RECEIPT.id == identity))
        if row is not None:
            record = _Record(row, row.plan_updated_at)
    elif binding is not None:
        for query in _attempt_sources(binding):
            match = (await session.execute(query.where(_ATTEMPT.id == identity))).one_or_none()
            if match is not None:
                record = _Record(*match)
                break
    if record is None:
        raise HTTPException(404, "No timeline evidence exists for this destination and record")
    evidence = await _provenance(session, binding, [record])
    entry, job = _entry(record, await _is_current(session, binding), evidence)
    return BatchLineageDetail(**entry.model_dump(), job=job)
