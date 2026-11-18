"""Read destination-scoped transfer metadata without scanning business rows."""

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.transfer_plans import TransferAttemptRow, TransferRunRow
from app.web.api.models.transfer_schedules import TransferScheduleJobRow
from app.web.api.models.transfer_timeline import (
    TimelineKind, TimelineScheduledTrigger, TimelineView, TransferTimelineDetail, TransferTimelineEntry,
    TransferTimelineJob, TransferTimelinePage,
)
from app.web.api.models.transfer_upstream import (
    TransferCommitEventRow, UpstreamTriggerEvent, UpstreamTriggerJobEventRow, UpstreamTriggerJobRow,
)
from app.web.services.transfer_observed_lineage import observed_receipt_physical_proof, visible_observed_table_identity
from app.web.services.upstream_mixed_rules import exact_mixed_job
from app.web.services.upstream_rules import canonical_group, exact_nested_job, positive_nested_events
from app.web.services.transfer_runs import (
    SANDBOX, SCHEDULED_SYSTEM_ACTOR, UPSTREAM_SYSTEM_ACTOR, _run_response, observed_lineage_enabled, transfer_target_identity,
)

Record = TransferAttemptRow | TransferRunRow
Job = TransferScheduleJobRow | UpstreamTriggerJobRow


_FILTER_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:[.][0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)
_SCOPE_FIELDS = ("plan_id", "request_id", "plan_updated_at", "target_table", "source_table")
_SCHEDULE_SCOPES: dict[str, TimelineScheduledTrigger] = {"daily_utc": "daily_utc", "week_utc": "weekly_utc", "month_utc": "monthly_utc"}


@dataclass(frozen=True, slots=True)
class TimelineFilters:
    view: TimelineView | None
    start: datetime | None
    before: datetime | None

    def lens(self) -> list[str | None]:
        return [self.view, self.start.isoformat() if self.start else None, self.before.isoformat() if self.before else None]


def parse_timeline_filters(view: TimelineView | None, start: str | None, before: str | None) -> TimelineFilters | None:
    if start is None and before is None:
        return TimelineFilters(view, None, None) if view else None
    try:
        if start is None or before is None or not _FILTER_TIMESTAMP.fullmatch(start) or not _FILTER_TIMESTAMP.fullmatch(before):
            raise ValueError("Paired explicit timestamps required")
        lower, upper = (datetime.fromisoformat(value).astimezone(timezone.utc) for value in (start, before))
        if not timedelta(0) < upper - lower <= timedelta(days=31):
            raise ValueError("Invalid timeline window")
    except (ValueError, OverflowError):
        raise HTTPException(422, "Provide paired timezone-aware timeline bounds spanning at most 31 days") from None
    return TimelineFilters(view, lower, upper)


def _kind(row: Record) -> TimelineKind:
    return "attempt" if isinstance(row, TransferAttemptRow) else "receipt"


def _encode_cursor(table: str, entry: TransferTimelineEntry, filters: TimelineFilters | None = None) -> str:
    position = [entry.occurredAt.isoformat(), str(entry.id), entry.kind]
    payload = [table, *position]
    if filters is not None:
        position[0] = entry.occurredAt.astimezone(timezone.utc).isoformat()
        payload = [2, table, *filters.lens(), *position]
    body = json.dumps(payload, separators=(",", ":"))
    return base64.urlsafe_b64encode(body.encode()).decode()


def _decode_cursor(table: str, token: str, filters: TimelineFilters | None = None) -> tuple[datetime, UUID, TimelineKind]:
    try:
        raw = base64.b64decode(token, altchars=b"-_", validate=True)
        data = json.loads(raw)
        if filters is None:
            if not isinstance(data, list) or len(data) != 4:
                raise ValueError("Invalid legacy position")
            scope, stamp, identity, kind = data
        else:
            if (not isinstance(data, list) or len(data) != 8 or type(data[0]) is not int or data[0] != 2
                or data[2:5] != filters.lens() or base64.urlsafe_b64encode(raw).decode() != token
                or json.dumps(data, separators=(",", ":")).encode() != raw):
                raise ValueError("Invalid filtered scope")
            scope, stamp, identity, kind = data[1], *data[5:]
        parsed = datetime.fromisoformat(stamp)
        if scope != table or not parsed.tzinfo or kind not in ("attempt", "receipt"):
            raise ValueError("Invalid scope or data")
        cursor_id = UUID(identity)
        if filters is not None and (
            parsed.astimezone(timezone.utc).isoformat() != stamp or str(cursor_id) != identity
            or (filters.view == "receipts" and kind != "receipt")
            or (filters.view in ("rejected_attempts", "unconfirmed_attempts") and kind != "attempt")
            or (filters.start is not None and not filters.start <= parsed < filters.before)
        ):
            raise ValueError("Invalid filtered position")
        return parsed, cursor_id, kind
    except (TypeError, ValueError, AttributeError, OverflowError, binascii.Error):
        raise HTTPException(422, "Invalid transfer timeline cursor for this destination") from None


async def _asset_identity(session: AsyncSession, layer: str, table: str) -> tuple[int, int]:
    physical = await transfer_target_identity(session, table) if layer == SANDBOX else None
    if physical is None:
        raise HTTPException(404, "Sandbox transfer destination does not exist")
    return physical


def _receipt_query(table: str):
    return select(TransferRunRow).where(TransferRunRow.target_schema == SANDBOX, TransferRunRow.target_table == table)


def _attempt_query(table: str):
    return select(TransferAttemptRow).where(TransferAttemptRow.target_table == table)


def _matching_receipt(record: TransferRunRow, job: Job) -> bool:
    return job.state == "committed" and job.receipt_id == record.id


def _scheduled_kind(job: TransferScheduleJobRow) -> TimelineScheduledTrigger | None:
    """Read cadence from immutable job metadata, not the mutable draft or a feature flag."""
    weekday, month_day = job.schedule_weekday_utc, job.schedule_day_of_month_utc
    if month_day is not None:
        if (weekday is None and 1 <= month_day <= 28 and job.slot.utcoffset() is not None
            and job.slot.astimezone(timezone.utc).day == month_day):
            return "monthly_utc"
        return None
    if weekday is None:
        return "daily_utc"
    if (1 <= weekday <= 7 and job.slot.utcoffset() is not None
        and job.slot.astimezone(timezone.utc).isoweekday() == weekday):
        return "weekly_utc"
    return None


async def _linked_jobs(session: AsyncSession, rows: list[Record]) -> dict[tuple[TimelineKind, UUID], Job]:
    scheduled_ids = set()
    upstream_ids = set()
    for row in rows:
        if isinstance(row, TransferAttemptRow):
            if row.scope_trigger in _SCHEDULE_SCOPES and row.schedule_job_id == row.request_id:
                scheduled_ids.add(row.schedule_job_id)
            if row.scope_trigger == "upstream" and row.upstream_job_id == row.request_id:
                upstream_ids.add(row.upstream_job_id)
        elif row.initiated_by == SCHEDULED_SYSTEM_ACTOR:
            scheduled_ids.add(row.request_id)
        elif row.initiated_by == UPSTREAM_SYSTEM_ACTOR:
            upstream_ids.add(row.request_id)
    scheduled = {job.id: job for job in (await session.scalars(select(TransferScheduleJobRow).where(
        TransferScheduleJobRow.id.in_(scheduled_ids),
    ))).all()} if scheduled_ids else {}
    upstream = {job.id: job for job in (await session.scalars(select(UpstreamTriggerJobRow).where(
        UpstreamTriggerJobRow.id.in_(upstream_ids),
    ))).all()} if upstream_ids else {}
    linked, checked_nested = {}, {}
    for row in rows:
        if isinstance(row, TransferAttemptRow):
            job = (scheduled.get(row.schedule_job_id) if row.scope_trigger in _SCHEDULE_SCOPES else
                   upstream.get(row.upstream_job_id) if row.scope_trigger == "upstream" else None)
        else:
            job = (scheduled.get(row.request_id) if row.initiated_by == SCHEDULED_SYSTEM_ACTOR else
                   upstream.get(row.request_id) if row.initiated_by == UPSTREAM_SYSTEM_ACTOR else None)
            if job is not None and not _matching_receipt(row, job):
                continue
        if job is None or job.id != row.request_id or job.plan_id != row.plan_id:
            continue
        revision = job.plan_updated_at if isinstance(job, TransferScheduleJobRow) else job.child_updated_at
        if revision != row.plan_updated_at:
            continue
        if isinstance(job, TransferScheduleJobRow):
            cadence = _scheduled_kind(job)
            if cadence is None or (
                isinstance(row, TransferAttemptRow) and _SCHEDULE_SCOPES.get(row.scope_trigger) != cadence
            ):
                continue
        if isinstance(job, TransferScheduleJobRow) and job.state == "canceled" and (
            not isinstance(row, TransferAttemptRow) or row.target_oid is None or row.target_filenode is None
            or row.status != "rejected" or row.receipt_id is not None or row.finished_at is None or job.canceled_at is None
            or not row.started_at <= row.finished_at <= job.canceled_at
        ):
            continue
        if isinstance(job, UpstreamTriggerJobRow) and job.group_operator is not None:
            if job.id not in checked_nested:
                checked_nested[job.id] = await _job_detail(session, job) is not None
            if not checked_nested[job.id]:
                continue
        linked[_kind(row), row.id] = job
    return linked


async def _manual_receipts(session: AsyncSession, rows: list[Record]) -> set[UUID]:
    receipts = {row.id: row for row in rows if isinstance(row, TransferRunRow)
                and row.initiated_by not in (SCHEDULED_SYSTEM_ACTOR, UPSTREAM_SYSTEM_ACTOR)}
    if not receipts:
        return set()
    attempts = (await session.scalars(select(TransferAttemptRow).where(
        TransferAttemptRow.receipt_id.in_(receipts),
        text("scope_trigger = 'manual' AND status = 'committed' AND receipt_id IS NOT NULL"),
    ))).all()
    manual = set()
    for attempt in attempts:
        receipt = receipts[attempt.receipt_id]
        if _same_attempt_scope(attempt, receipt):
            manual.add(receipt.id)
    return manual


def _same_attempt_scope(attempt: TransferAttemptRow, receipt: TransferRunRow) -> bool:
    return all(getattr(attempt, name) == getattr(receipt, name) for name in _SCOPE_FIELDS)


async def _checked_attempt_receipts(session: AsyncSession, table: str, rows: list[Record]) -> dict[UUID, UUID]:
    linked = {row.id: row for row in rows if isinstance(row, TransferAttemptRow) and row.receipt_id is not None
              and row.status in ("committed", "replayed")}
    if not linked:
        return {}
    receipts = {receipt.id: receipt for receipt in (await session.scalars(_receipt_query(table).where(
        TransferRunRow.id.in_({row.receipt_id for row in linked.values()}),
    ))).all()}
    return {attempt_id: row.receipt_id for attempt_id, row in linked.items()
            if (receipt := receipts.get(row.receipt_id)) is not None and _same_attempt_scope(row, receipt)}


def _entry(
    row: Record, physical: tuple[int, int], job: Job | None, manual_receipts: set[UUID],
    attempt_receipts: dict[UUID, UUID],
) -> TransferTimelineEntry:
    attempt = isinstance(row, TransferAttemptRow)
    scope = ("unknown" if row.target_oid is None or row.target_filenode is None else
             "current" if (row.target_oid, row.target_filenode) == physical else "historical")
    manual = row.scope_trigger == "manual" if attempt else row.id in manual_receipts
    trigger = (_scheduled_kind(job) if isinstance(job, TransferScheduleJobRow) else "upstream" if job is not None
               else "manual" if manual else None)
    receipt_id = attempt_receipts.get(row.id) if attempt else row.id
    status = row.status if attempt else "committed"
    if attempt and status in ("committed", "replayed") and receipt_id is None:
        status = "outcome_unknown"
    return TransferTimelineEntry(
        id=row.id, kind="attempt" if attempt else "receipt", occurredAt=row.started_at if attempt else row.recorded_at,
        planId=row.plan_id, planUpdatedAt=row.plan_updated_at, status=status,
        receiptId=receipt_id, rowsAffected=None if attempt else row.rows_affected,
        physicalScope=scope, trigger=trigger, sourceSchema=SANDBOX if attempt else row.source_schema,
        sourceTable=row.source_table, finishedAt=row.finished_at if attempt else None,
        message=row.message if attempt else None,
    )


def _keyset(query, stamp, identity, kind: TimelineKind, cursor: tuple[datetime, UUID, TimelineKind] | None):
    if cursor is not None:
        before_at, before_id, before_kind = cursor
        key, bound = tuple_(stamp, identity), tuple_(before_at, before_id)
        query = query.where(key <= bound if kind < before_kind else key < bound)
    return query.order_by(stamp.desc(), identity.desc())


def _timeline_scopes(table: str, filters: TimelineFilters | None, position):
    attempts, receipts = TransferAttemptRow, TransferRunRow
    view = filters.view if filters else None
    if view != "receipts":
        attempt_query = _attempt_query(table)
        if view == "rejected_attempts":
            attempt_query = attempt_query.where(attempts.status == "rejected")
        elif view == "unconfirmed_attempts":
            # Use the same destination and immutable attribution fields that
            # classify the returned attempt: never rely on a claimed status alone.
            proof = _receipt_query(table).with_only_columns(receipts.id).where(
                receipts.id == attempts.receipt_id,
                *(getattr(receipts, field) == getattr(attempts, field) for field in _SCOPE_FIELDS),
            ).correlate(attempts).exists()
            attempt_query = attempt_query.where(or_(
                attempts.status == "outcome_unknown",
                and_(attempts.status.in_(("committed", "replayed")), ~proof),
            ))
        if filters and filters.start is not None:
            attempt_query = attempt_query.where(attempts.started_at >= filters.start, attempts.started_at < filters.before)
        yield _keyset(attempt_query, attempts.started_at, attempts.id, "attempt", position)
    if view not in ("rejected_attempts", "unconfirmed_attempts"):
        receipt_query = _receipt_query(table)
        if filters and filters.start is not None:
            receipt_query = receipt_query.where(receipts.recorded_at >= filters.start, receipts.recorded_at < filters.before)
        yield _keyset(receipt_query, receipts.recorded_at, receipts.id, "receipt", position)


async def list_transfer_timeline(
    session: AsyncSession, layer: str, table: str, limit: int, cursor: str | None, filters: TimelineFilters | None = None,
) -> TransferTimelinePage:
    """Page immutable destination receipts and independently scoped attempts."""
    position = _decode_cursor(table, cursor, filters) if cursor else None
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
    physical = await _asset_identity(session, layer, table)
    receipts = TransferRunRow
    scopes = _timeline_scopes(table, filters, position)
    rows: list[Record] = []
    for query in scopes:
        rows.extend((await session.scalars(query.limit(limit + 1))).all())
    rows.sort(key=lambda row: (row.started_at, row.id, "attempt") if isinstance(row, TransferAttemptRow)
              else (row.recorded_at, row.id, "receipt"), reverse=True)
    page = rows[:limit]
    jobs = await _linked_jobs(session, page)
    manual = await _manual_receipts(session, page)
    att_receipts = await _checked_attempt_receipts(session, table, page)
    entries = [_entry(row, physical, jobs.get((_kind(row), row.id)), manual, att_receipts) for row in page]
    # The literal predicate matches the partial index and permits a
    # destination/physical key lookup independently of older replacements.
    latest = await session.scalar(_receipt_query(table).where(
        receipts.target_oid == physical[0], receipts.target_filenode == physical[1], text("rows_affected > 0"),
    ).order_by(receipts.recorded_at.desc(), receipts.id.desc()).limit(1))
    # Confirm identity again after querying history, so a racing DDL rewrite
    # cannot make a previously observed file look current to this response.
    final = await _asset_identity(session, layer, table)
    if final != physical:
        entries = [_entry(row, final, jobs.get((_kind(row), row.id)), manual, att_receipts) for row in page]
        latest = None
    return TransferTimelinePage(entries=entries,
                                nextCursor=_encode_cursor(table, entries[-1], filters) if len(rows) > limit else None,
                                latestCurrentReceipt=_run_response(latest) if latest is not None else None)


async def _job_detail(session: AsyncSession, job: Job) -> TransferTimelineJob | None:
    if isinstance(job, TransferScheduleJobRow):
        cadence = _scheduled_kind(job)
        return (TransferTimelineJob(id=job.id, kind=cadence, dueAt=job.slot, state=job.state,
                                    scheduleWeekdayUtc=job.schedule_weekday_utc, scheduleDayOfMonthUtc=job.schedule_day_of_month_utc,
                                    canceledAt=job.canceled_at,
                                    parentEvents=[]) if cadence is not None else None)
    events = (await session.scalars(select(TransferCommitEventRow).join(
        UpstreamTriggerJobEventRow, UpstreamTriggerJobEventRow.event_id == TransferCommitEventRow.id,
    ).where(UpstreamTriggerJobEventRow.job_id == job.id, UpstreamTriggerJobEventRow.plan_id == job.plan_id,
            UpstreamTriggerJobEventRow.generation == job.generation).order_by(TransferCommitEventRow.id).limit(4))).all()
    nested = job.group_operator is not None
    observed = await exact_mixed_job(session, job, events) if job.mixed_slot is not None else None
    if job.mixed_slot is not None:
        if observed is None:
            return None
    elif nested:
        if not exact_nested_job(job, events) or not await positive_nested_events(session, events):
            return None
    elif (not 1 <= len(events) <= (3 if job.operator == "AND" else 1)
          or max(event.id for event in events) != job.event_id
          or len({event.parent_plan_id for event in events}) != len(events)):
        return None
    return TransferTimelineJob(
        id=job.id, kind="upstream", dueAt=job.mixed_slot or max(event.recorded_at for event in events), state=job.state,
        witnessObservedAt=observed.observed_at if observed else None, operator=job.operator if nested or observed else None,
        group=canonical_group(job.operator, job.group_operator, job.group_parent_ids, job.rule_parent_ids) if nested else None,
        ruleParentPlanIds=observed.rule_parent_ids if observed else job.rule_parent_ids if nested else None,
        parentEvents=[UpstreamTriggerEvent(eventId=str(event.id), eventRecordedAt=event.recorded_at,
                                         parentPlanId=event.parent_plan_id, parentReceiptId=event.parent_receipt_id)
                      for event in events],
    )


async def read_transfer_timeline_entry(
    session: AsyncSession, layer: str, table: str, kind: TimelineKind, identity: UUID, *, require_visible: bool = False,
) -> TransferTimelineDetail:
    """Read one exact destination entry, optionally only for a visible receipt link."""
    unavailable = HTTPException(404, "Transfer timeline entry is not present for this destination")
    physical = None
    if require_visible:
        if kind != "receipt" or not observed_lineage_enabled():
            raise unavailable
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
        physical = await visible_observed_table_identity(session, layer, table)
        if physical is None:
            raise unavailable
    else:
        await _asset_identity(session, layer, table)
    query = (_attempt_query(table).where(TransferAttemptRow.id == identity) if kind == "attempt" else
             _receipt_query(table).where(TransferRunRow.id == identity))
    row = await session.scalar(query)
    if row is None:
        raise unavailable
    if require_visible:
        proof = await observed_receipt_physical_proof(session, table, identity)
        if proof is None:
            raise unavailable
        source = await visible_observed_table_identity(session, row.source_schema, row.source_table)
        if source is None:
            raise unavailable
        job = await session.get(TransferScheduleJobRow, proof.job_id) if proof.job_id is not None else None
        report = await _job_detail(session, job) if job is not None else None
        if proof.trigger is not None and (report is None or report.kind != proof.trigger):
            raise unavailable
        entry = _entry(row, physical, job, {row.id} if proof.trigger is None else set(), {})
        if source != (proof.source_oid, proof.source_node) or physical != (proof.target_oid, proof.target_node):
            entry.physicalScope = "historical"
        return TransferTimelineDetail(**entry.model_dump(), job=report)

    link = (await _linked_jobs(session, [row])).get((kind, row.id))
    report = await _job_detail(session, link) if link is not None else None
    if physical is None:
        physical = await _asset_identity(session, layer, table)
    manual = await _manual_receipts(session, [row])
    att_receipts = await _checked_attempt_receipts(session, table, [row])
    entry = _entry(row, physical, link if report is not None else None, manual, att_receipts)
    return TransferTimelineDetail(**entry.model_dump(), job=report)
