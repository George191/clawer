"""Read destination-scoped transfer metadata without scanning business rows."""

import base64
import binascii
from datetime import datetime
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.transfer_plans import TransferAttemptRow, TransferRunRow
from app.web.api.models.transfer_schedules import TransferScheduleJobRow
from app.web.api.models.transfer_timeline import (
    TimelineKind, TransferTimelineDetail, TransferTimelineEntry, TransferTimelineJob, TransferTimelinePage,
)
from app.web.api.models.transfer_upstream import (
    TransferCommitEventRow, UpstreamTriggerEvent, UpstreamTriggerJobEventRow, UpstreamTriggerJobRow,
)
from app.web.services.upstream_rules import canonical_group, exact_nested_job, positive_nested_events
from app.web.services.transfer_runs import (
    SANDBOX, SCHEDULED_SYSTEM_ACTOR, UPSTREAM_SYSTEM_ACTOR, _run_response, transfer_target_identity,
)

Record = TransferAttemptRow | TransferRunRow
Job = TransferScheduleJobRow | UpstreamTriggerJobRow


def _kind(row: Record) -> TimelineKind:
    return "attempt" if isinstance(row, TransferAttemptRow) else "receipt"


def _encode_cursor(table: str, entry: TransferTimelineEntry) -> str:
    body = json.dumps([table, entry.occurredAt.isoformat(), str(entry.id), entry.kind], separators=(",", ":"))
    return base64.urlsafe_b64encode(body.encode()).decode()


def _decode_cursor(table: str, token: str) -> tuple[datetime, UUID, TimelineKind]:
    try:
        scope, stamp, identity, kind = json.loads(base64.b64decode(token, altchars=b"-_", validate=True))
        parsed = datetime.fromisoformat(stamp)
        if scope != table or not parsed.tzinfo or kind not in ("attempt", "receipt"):
            raise ValueError("Invalid scope or data")
        return parsed, UUID(identity), kind
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


async def _linked_jobs(session: AsyncSession, rows: list[Record]) -> dict[tuple[TimelineKind, UUID], Job]:
    daily_ids = set()
    upstream_ids = set()
    for row in rows:
        if isinstance(row, TransferAttemptRow):
            if row.scope_trigger == "daily_utc" and row.schedule_job_id == row.request_id:
                daily_ids.add(row.schedule_job_id)
            if row.scope_trigger == "upstream" and row.upstream_job_id == row.request_id:
                upstream_ids.add(row.upstream_job_id)
        elif row.initiated_by == SCHEDULED_SYSTEM_ACTOR:
            daily_ids.add(row.request_id)
        elif row.initiated_by == UPSTREAM_SYSTEM_ACTOR:
            upstream_ids.add(row.request_id)
    daily = {job.id: job for job in (await session.scalars(select(TransferScheduleJobRow).where(
        TransferScheduleJobRow.id.in_(daily_ids),
    ))).all()} if daily_ids else {}
    upstream = {job.id: job for job in (await session.scalars(select(UpstreamTriggerJobRow).where(
        UpstreamTriggerJobRow.id.in_(upstream_ids),
    ))).all()} if upstream_ids else {}
    linked, checked_nested = {}, {}
    for row in rows:
        if isinstance(row, TransferAttemptRow):
            job = (daily.get(row.schedule_job_id) if row.scope_trigger == "daily_utc" else
                   upstream.get(row.upstream_job_id) if row.scope_trigger == "upstream" else None)
        else:
            job = (daily.get(row.request_id) if row.initiated_by == SCHEDULED_SYSTEM_ACTOR else
                   upstream.get(row.request_id) if row.initiated_by == UPSTREAM_SYSTEM_ACTOR else None)
            if job is not None and not _matching_receipt(row, job):
                continue
        if job is None or job.id != row.request_id or job.plan_id != row.plan_id:
            continue
        revision = job.plan_updated_at if isinstance(job, TransferScheduleJobRow) else job.child_updated_at
        if revision != row.plan_updated_at:
            continue
        if isinstance(job, UpstreamTriggerJobRow) and job.group_operator is not None:
            if job.id not in checked_nested:
                checked_nested[job.id] = await _job_detail(session, job) is not None
            if not checked_nested[job.id]:
                continue
        linked[_kind(row), row.id] = job
    return linked


async def _manual_receipts(session: AsyncSession, rows: list[Record]) -> set[UUID]:
    receipts = {row.id: row for row in rows if isinstance(row, TransferRunRow)}
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
    return (attempt.plan_id == receipt.plan_id and attempt.request_id == receipt.request_id
            and attempt.plan_updated_at == receipt.plan_updated_at and attempt.target_table == receipt.target_table
            and attempt.source_table == receipt.source_table)


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
    trigger = ("daily_utc" if isinstance(job, TransferScheduleJobRow) else "upstream" if job is not None
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


async def list_transfer_timeline(
    session: AsyncSession, layer: str, table: str, limit: int, cursor: str | None,
) -> TransferTimelinePage:
    """Page immutable destination receipts and independently scoped attempts."""
    position = _decode_cursor(table, cursor) if cursor else None
    physical = await _asset_identity(session, layer, table)
    attempts = TransferAttemptRow
    receipts = TransferRunRow
    scopes = (
        _keyset(_attempt_query(table), attempts.started_at, attempts.id, "attempt", position),
        _keyset(_receipt_query(table), receipts.recorded_at, receipts.id, "receipt", position),
    )
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
                                nextCursor=_encode_cursor(table, entries[-1]) if len(rows) > limit else None,
                                latestCurrentReceipt=_run_response(latest) if latest is not None else None)


async def _job_detail(session: AsyncSession, job: Job) -> TransferTimelineJob | None:
    if isinstance(job, TransferScheduleJobRow):
        return TransferTimelineJob(id=job.id, kind="daily_utc", dueAt=job.slot, state=job.state, parentEvents=[])
    events = (await session.scalars(select(TransferCommitEventRow).join(
        UpstreamTriggerJobEventRow, UpstreamTriggerJobEventRow.event_id == TransferCommitEventRow.id,
    ).where(UpstreamTriggerJobEventRow.job_id == job.id, UpstreamTriggerJobEventRow.plan_id == job.plan_id,
            UpstreamTriggerJobEventRow.generation == job.generation).order_by(TransferCommitEventRow.id).limit(4))).all()
    nested = job.group_operator is not None
    if nested:
        if not exact_nested_job(job, events) or not await positive_nested_events(session, events):
            return None
    elif (not 1 <= len(events) <= (3 if job.operator == "AND" else 1)
          or max(event.id for event in events) != job.event_id
          or len({event.parent_plan_id for event in events}) != len(events)):
        return None
    return TransferTimelineJob(
        id=job.id, kind="upstream", dueAt=max(event.recorded_at for event in events), state=job.state,
        operator=job.operator if nested else None,
        group=canonical_group(job.operator, job.group_operator, job.group_parent_ids, job.rule_parent_ids) if nested else None,
        ruleParentPlanIds=job.rule_parent_ids if nested else None,
        parentEvents=[UpstreamTriggerEvent(eventId=str(event.id), eventRecordedAt=event.recorded_at,
                                         parentPlanId=event.parent_plan_id, parentReceiptId=event.parent_receipt_id)
                      for event in events],
    )


async def read_transfer_timeline_entry(
    session: AsyncSession, layer: str, table: str, kind: TimelineKind, identity: UUID,
) -> TransferTimelineDetail:
    """Read one exact destination entry and its verified scheduler relation."""
    await _asset_identity(session, layer, table)
    query = (_attempt_query(table).where(TransferAttemptRow.id == identity) if kind == "attempt" else
             _receipt_query(table).where(TransferRunRow.id == identity))
    row = await session.scalar(query)
    if row is None:
        raise HTTPException(404, "Transfer timeline entry is not present for this destination")
    link = (await _linked_jobs(session, [row])).get((kind, row.id))
    report = await _job_detail(session, link) if link is not None else None
    physical = await _asset_identity(session, layer, table)
    manual = await _manual_receipts(session, [row])
    att_receipts = await _checked_attempt_receipts(session, table, [row])
    entry = _entry(row, physical, link if report is not None else None, manual, att_receipts)
    return TransferTimelineDetail(**entry.model_dump(), job=report)
