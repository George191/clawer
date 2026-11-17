"""Bounded PostgreSQL sandbox transfers. No external destinations."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import and_, or_, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.transfer_plans import (
    TransferAttempt, TransferAttemptRow, TransferCheckpointRow, TransferPlanRow, TransferRun,
    TransferRunCapabilities, TransferRunRequest, TransferRunRow,
)
from app.web.api.models.transfer_schedules import TransferScheduleJobRow, TransferScheduleRow
from app.web.api.models.transfer_upstream import (
    TransferCommitEventRow, UpstreamTriggerJobEventRow, UpstreamTriggerJobRow, UpstreamTriggerParentRow, UpstreamTriggerRow,
)
from app.web.api.models.user import User
from app.web.api.utils.models import StatusTypes
from app.web.core.config import settings
from app.web.services.etl_layers import get_etl_layers, quote_identifier
from app.web.services.transfer_plans import _response, compare_transfer_endpoints
from app.web.services.upstream_rules import exact_nested_job, positive_nested_events, valid_witness

SANDBOX = "ts_transfer_sandbox"
MAX_ROWS = 100
HISTORY_LIMIT = 25
SCHEDULED_SYSTEM_ACTOR = uuid5(NAMESPACE_URL, "asiral:transfer-scheduler:local")
UPSTREAM_SYSTEM_ACTOR = uuid5(NAMESPACE_URL, "asiral:transfer-upstream:local")
logger = logging.getLogger(__name__)


def transfer_run_capabilities() -> TransferRunCapabilities:
    enabled = settings.TRANSFER_SANDBOX_ENABLED and settings.ENVIRONMENT == "local"
    return TransferRunCapabilities(enabled=enabled, sourceSchema=SANDBOX, targetSchema=SANDBOX,
                                   maxRows=MAX_ROWS, incrementalAppend=enabled,
                                   scheduledUpsert=scheduled_transfer_enabled(), upstreamUpsert=upstream_transfer_enabled())


def scheduled_transfer_enabled() -> bool:
    return ("ENVIRONMENT" in settings.model_fields_set and settings.ENVIRONMENT == "local"
            and settings.TRANSFER_SANDBOX_ENABLED and settings.TRANSFER_SANDBOX_SCHEDULER_ENABLED)


def upstream_transfer_enabled() -> bool:
    return ("ENVIRONMENT" in settings.model_fields_set and settings.ENVIRONMENT == "local"
            and settings.TRANSFER_SANDBOX_ENABLED and settings.TRANSFER_SANDBOX_UPSTREAM_ENABLED)


def is_sandbox_upsert(plan: TransferPlanRow, trigger: str) -> bool:
    return (plan.trigger == trigger and plan.write_mode == "upsert" and plan.incremental_cursor is None
            and plan.source_schema == plan.target_schema == SANDBOX)


def upstream_pair_eligible(child: TransferPlanRow, parent: TransferPlanRow) -> bool:
    return (child.id != parent.id and is_sandbox_upsert(child, "upstream") and is_sandbox_upsert(parent, "manual")
            and parent.target_table == child.source_table
            and len({parent.source_table, parent.target_table, child.target_table}) == 3)


async def lock_transfer_plans(session: AsyncSession, *ids: UUID) -> dict[UUID, TransferPlanRow]:
    # Same canonical ordering in arming and execution, independent of graph direction.
    return {row.id: row for row in (await session.scalars(select(TransferPlanRow).where(
        TransferPlanRow.id.in_(ids),
    ).order_by(TransferPlanRow.id).with_for_update().execution_options(populate_existing=True))).all()}


def _run_response(row: TransferRunRow) -> TransferRun:
    return TransferRun(
        id=row.id, planId=row.plan_id, rowsAffected=row.rows_affected, writeMode=row.write_mode,
        sourceSchema=row.source_schema, sourceTable=row.source_table, targetSchema=row.target_schema,
        targetTable=row.target_table, recordedAt=row.recorded_at, hasMore=row.has_more,
        cursorThrough=None if row.cursor_through is None else str(row.cursor_through),
    )


async def list_transfer_runs(session: AsyncSession, *, plan_id: UUID | None = None,
                             table: str | None = None) -> list[TransferRun]:
    if plan_id is not None:
        if await session.get(TransferPlanRow, plan_id) is None:
            raise HTTPException(404, "Transfer draft does not exist")
        filter_by = TransferRunRow.plan_id == plan_id
    elif table is not None:
        filter_by = or_(and_(TransferRunRow.source_schema == SANDBOX, TransferRunRow.source_table == table),
                        and_(TransferRunRow.target_schema == SANDBOX, TransferRunRow.target_table == table))
    else:
        return []
    statement = select(TransferRunRow).where(filter_by).order_by(
        TransferRunRow.recorded_at.desc(), TransferRunRow.id.desc(),
    ).limit(HISTORY_LIMIT)
    return [_run_response(row) for row in (await session.scalars(statement)).all()]


async def list_transfer_attempts(session: AsyncSession, plan_id: UUID) -> list[TransferAttempt]:
    if await session.get(TransferPlanRow, plan_id) is None:
        raise HTTPException(404, "Transfer draft does not exist")
    statement = select(TransferAttemptRow).where(TransferAttemptRow.plan_id == plan_id).order_by(
        TransferAttemptRow.started_at.desc(), TransferAttemptRow.id.desc(),
    ).limit(HISTORY_LIMIT)
    return [TransferAttempt(id=row.id, planId=row.plan_id, status=row.status, startedAt=row.started_at,
                            finishedAt=row.finished_at, receiptId=row.receipt_id, message=row.message,
                            upstreamJobId=row.upstream_job_id)
            for row in (await session.scalars(statement)).all()]


async def transfer_target_identity(session: AsyncSession, target_table: str) -> tuple[int, int] | None:
    """Read only the catalog identity of an ordinary sandbox heap."""
    row = (await session.execute(text("""
        SELECT c.oid, pg_catalog.pg_relation_filenode(c.oid)
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_am a ON a.oid = c.relam AND a.amname = 'heap'
        WHERE n.nspname = :schema AND c.relname = :target AND c.relkind = 'r' AND NOT c.relispartition
    """), {"schema": SANDBOX, "target": target_table})).one_or_none()
    return (row[0], row[1]) if row and row[1] is not None else None


async def _pin_attempt_scope(
    session: AsyncSession, plan: TransferPlanRow, request: TransferRunRequest,
    *, scheduled_job_id: UUID | None, upstream_job_id: UUID | None,
) -> dict:
    # Save only the target of the exact admitted draft revision. Execution
    # rechecks the revision and physical relation under its existing write locks.
    if plan.updated_at != request.expectedUpdatedAt or plan.source_schema != SANDBOX or plan.target_schema != SANDBOX:
        return {}
    trigger, expected_mode = "manual", "manual"
    if scheduled_job_id is not None:
        job = await session.get(TransferScheduleJobRow, scheduled_job_id)
        if (job is None or job.id != request.requestId or job.plan_id != plan.id
            or job.plan_updated_at != request.expectedUpdatedAt):
            return {}
        trigger, expected_mode = "daily_utc", "scheduled"
    elif upstream_job_id is not None:
        job = await session.get(UpstreamTriggerJobRow, upstream_job_id)
        if (job is None or job.id != request.requestId or job.plan_id != plan.id
            or job.child_updated_at != request.expectedUpdatedAt):
            return {}
        trigger = expected_mode = "upstream"
    if plan.trigger != expected_mode:
        return {}

    # A known replay is a new attempt at the same immutable receipt, never an
    # apparent fresh attempt against a replacement heap with the same name.
    prior = (await session.scalars(select(TransferRunRow).where(
        TransferRunRow.plan_id == plan.id, TransferRunRow.request_id == request.requestId,
        TransferRunRow.plan_updated_at == request.expectedUpdatedAt, TransferRunRow.target_schema == SANDBOX,
        TransferRunRow.target_table == plan.target_table,
    ))).one_or_none()
    target = ((prior.target_oid, prior.target_filenode) if prior else
              await transfer_target_identity(session, plan.target_table)) or (None, None)
    return dict(plan_updated_at=plan.updated_at, source_table=plan.source_table, target_table=plan.target_table,
                target_oid=target[0], target_filenode=target[1], scope_trigger=trigger, schedule_job_id=scheduled_job_id)


async def _finish_attempt(session: AsyncSession, attempt_id: UUID, status: str, *,
                          receipt_id: UUID | None = None, message: str | None = None) -> None:
    await session.execute(update(TransferAttemptRow).where(
        TransferAttemptRow.id == attempt_id, TransferAttemptRow.status == "started",
    ).values(status=status, finished_at=datetime.now(timezone.utc), receipt_id=receipt_id, message=message))


async def _record_attempt_error(
    session: AsyncSession, attempt_id: UUID, failure: HTTPException, *,
    scheduled_job_id: UUID | None = None, upstream_job_id: UUID | None = None,
    retryable: bool = False, scheduled_now: datetime | None = None,
) -> None:
    # A failed receipt commit may instead be an acknowledged database write with
    # an interrupted connection. Never overwrite a committed attempt with failure.
    message = failure.detail if isinstance(failure.detail, str) else failure.detail.get("message", "Transfer rejected")
    status = "outcome_unknown" if failure.status_code >= 500 else "rejected"
    try:
        await _finish_attempt(session, attempt_id, status, message=message[:240])
        job_id = upstream_job_id or scheduled_job_id
        model = UpstreamTriggerJobRow if upstream_job_id is not None else TransferScheduleJobRow
        if job_id is not None and not retryable:
            job_state = ("expired" if isinstance(failure.detail, dict) and failure.detail.get("code") == "schedule_expired"
                         else status)
            finished = scheduled_now or datetime.now(timezone.utc)
            await session.execute(update(model).where(
                model.id == job_id, model.state.in_(("pending", "dispatched")),
            ).values(state=job_state, finished_at=finished, message=message[:240]))
        await session.commit()
    except Exception:
        await session.rollback()
        logger.warning("Could not update sandbox transfer attempt status for %s", attempt_id)


async def _sandbox_tables(session: AsyncSession, source: str, target: str, *, schema: str = SANDBOX):
    # The schemas were already checked and both table locks are held. Exclude
    # features that can cause hidden writes or user-defined SQL in this pilot.
    return (await session.execute(text("""
        SELECT c.relname AS name, c.oid AS table_oid, pg_relation_filenode(c.oid) AS table_node,
               p.conname AS primary_name,
               ARRAY(SELECT a.attname::text FROM unnest(p.conkey) WITH ORDINALITY AS k(attnum, position)
                     JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
                     ORDER BY k.position) AS key_columns
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_am table_am ON table_am.oid = c.relam AND table_am.amname = 'heap'
        LEFT JOIN pg_catalog.pg_constraint p ON p.conrelid = c.oid AND p.contype = 'p' AND NOT p.condeferrable
        WHERE n.nspname = :schema AND c.relname IN (:source, :target)
          AND c.relkind = 'r' AND NOT c.relispartition AND NOT c.relrowsecurity AND NOT c.relhasrules
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i
                          WHERE i.inhrelid = c.oid OR i.inhparent = c.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid = c.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint ct
                          WHERE ct.conrelid = c.oid AND ct.contype IN ('c', 'f', 'x'))
          AND NOT EXISTS (
              SELECT 1 FROM pg_catalog.pg_index i
              JOIN pg_catalog.pg_class idx ON idx.oid = i.indexrelid
              JOIN pg_catalog.pg_am index_am ON index_am.oid = idx.relam
              WHERE i.indrelid = c.oid AND (
                  i.indexprs IS NOT NULL OR i.indpred IS NOT NULL OR index_am.amname <> 'btree'
                  OR EXISTS (
                      SELECT 1 FROM unnest(i.indclass::oid[]) AS opclass_ids(id)
                      JOIN pg_catalog.pg_opclass opc ON opc.oid = opclass_ids.id
                      JOIN pg_catalog.pg_namespace ns ON ns.oid = opc.opcnamespace
                      WHERE ns.nspname <> 'pg_catalog'
                  )
                  OR EXISTS (
                      SELECT 1 FROM unnest(i.indcollation::oid[]) AS collation_ids(id)
                      JOIN pg_catalog.pg_collation co ON co.oid = collation_ids.id
                      JOIN pg_catalog.pg_namespace ns ON ns.oid = co.collnamespace
                      WHERE ns.nspname <> 'pg_catalog'
                  )
              )
          )
          AND NOT EXISTS (
              SELECT 1 FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
              JOIN pg_catalog.pg_namespace tn ON tn.oid = t.typnamespace
              WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                AND (tn.nspname <> 'pg_catalog' OR t.typname NOT IN (
                    'int2', 'int4', 'int8', 'text', 'varchar', 'bpchar', 'bool', 'numeric', 'float4', 'float8',
                    'date', 'time', 'timestamp', 'timestamptz', 'uuid', 'jsonb', 'bytea'
                ))
          )
    """), {"schema": schema, "source": source, "target": target})).mappings().all()


async def _require_index_range_plan(
    session: AsyncSession, source_sql: str, params: dict[str, int], index_name: str, *, ranged: bool,
) -> None:
    raw_plan = (await session.execute(text("EXPLAIN (FORMAT JSON) " + source_sql), params)).scalar_one()
    if isinstance(raw_plan, str):
        raw_plan = json.loads(raw_plan)
    root = raw_plan[0]["Plan"]
    children = root.get("Plans", [])
    if (root.get("Node Type") != "Limit" or len(children) != 1
        or children[0].get("Node Type") not in ("Index Scan", "Index Only Scan")
        or children[0].get("Index Name") != index_name or children[0].get("Filter")
        or (ranged and not children[0].get("Index Cond"))):
        raise HTTPException(422, "A bounded source primary-key index range plan is unavailable")


async def _incremental_batch(
    session: AsyncSession, source: str, cursor_name: str, index_name: str, cursor_through: int | None,
) -> tuple[list[int], bool]:
    cursor = quote_identifier(cursor_name)
    predicate = "" if cursor_through is None else f"WHERE {cursor} > CAST(:watermark AS bigint)"
    source_sql = f"SELECT {cursor} FROM ONLY {source} {predicate} ORDER BY {cursor} LIMIT :limit"
    params = {"limit": MAX_ROWS + 1}
    if cursor_through is not None:
        params["watermark"] = cursor_through
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    await session.execute(text("SET LOCAL enable_bitmapscan = off"))
    await _require_index_range_plan(session, source_sql, params, index_name, ranged=cursor_through is not None)
    keys = (await session.scalars(text(source_sql), params)).all()
    return keys[:MAX_ROWS], len(keys) > MAX_ROWS


async def _lock_schedule_job(
    session: AsyncSession, plan: TransferPlanRow, request: TransferRunRequest,
    job_id: UUID, actor_id: UUID, now: datetime | None,
) -> TransferScheduleJobRow:
    # The plan is already locked. Schedule mutation follows the same plan-first order.
    job = (await session.scalars(select(TransferScheduleJobRow).where(TransferScheduleJobRow.id == job_id)
                                 .with_for_update().execution_options(populate_existing=True))).one_or_none()
    if (job is None or job.plan_id != plan.id or job.id != request.requestId or job.armed_by != actor_id
        or job.plan_updated_at != request.expectedUpdatedAt):
        raise HTTPException(409, "The scheduled dispatch does not match this transfer request")
    if not scheduled_transfer_enabled():
        raise HTTPException(403, "Automatic sandbox transfer execution is disabled")
    if job.state == "committed":
        return job
    if job.state not in ("pending", "dispatched"):
        raise HTTPException(409, "This UTC schedule slot already has a terminal outcome")
    current = now or datetime.now(timezone.utc)
    if current < job.slot:
        raise HTTPException(409, {"code": "schedule_not_due", "message": "UTC dispatch window has not opened"})
    if current >= job.expires_at:
        raise HTTPException(409, {"code": "schedule_expired", "message": "UTC dispatch window has closed"})
    binding = await session.get(TransferScheduleRow, plan.id, populate_existing=True)
    if (binding is None or not binding.enabled or binding.generation != job.generation
        or binding.armed_for_updated_at != plan.updated_at or binding.armed_by != actor_id
        or plan.updated_at != job.plan_updated_at or plan.trigger != "scheduled" or plan.write_mode != "upsert"
        or plan.incremental_cursor is not None or plan.schedule_time != job.slot.astimezone(timezone.utc).strftime("%H:%M")
        or plan.source_schema != SANDBOX or plan.target_schema != SANDBOX):
        raise HTTPException(409, "The scheduled transfer was paused or its draft binding changed")
    actor = await session.scalar(select(User.id).where(
        User.id == actor_id, User.status == StatusTypes.ENABLE, User.is_superuser.is_(True),
    ).with_for_update(read=True))
    if actor is None:
        raise HTTPException(403, "The administrator who armed this schedule is no longer eligible")
    if job.dispatched_at is None:
        job.dispatched_at = current
    return job


async def upstream_catalog(session: AsyncSession, child: TransferPlanRow, parents: list[TransferPlanRow]) -> dict:
    rows = await _sandbox_tables(session, child.source_table, child.target_table)
    for parent in parents:
        rows += await _sandbox_tables(session, parent.source_table, parent.target_table)
    catalog = {item["name"]: item for item in rows}
    if set(catalog) != {child.source_table, child.target_table, *(parent.source_table for parent in parents)}:
        raise HTTPException(422, "The upstream route must use ordinary sandbox tables")
    return catalog


def upstream_catalog_digest(catalog: dict) -> str:
    parts = [(name, meta["table_oid"], meta["table_node"]) for name, meta in sorted(catalog.items())]
    return sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


async def _lock_upstream_job(
    session: AsyncSession, child: TransferPlanRow, locked_plans: dict[UUID, TransferPlanRow], request: TransferRunRequest,
    job_id: UUID, actor_id: UUID, now: datetime | None,
) -> tuple[UpstreamTriggerJobRow, list[TransferCommitEventRow], UpstreamTriggerRow | None, list[TransferPlanRow]]:
    job = (await session.scalars(select(UpstreamTriggerJobRow).where(UpstreamTriggerJobRow.id == job_id)
                                 .with_for_update().execution_options(populate_existing=True))).one_or_none()
    if (job is None or job.plan_id != child.id or job.id != request.requestId or job.armed_by != actor_id
        or job.child_updated_at != request.expectedUpdatedAt):
        raise HTTPException(409, "The upstream job is not bound to this request")
    events = list((await session.scalars(select(TransferCommitEventRow)
                  .join(UpstreamTriggerJobEventRow, UpstreamTriggerJobEventRow.event_id == TransferCommitEventRow.id)
                  .where(UpstreamTriggerJobEventRow.job_id == job_id, UpstreamTriggerJobEventRow.plan_id == job.plan_id,
                         UpstreamTriggerJobEventRow.generation == job.generation).limit(4))).all())
    if not events or max(event.id for event in events) != job.event_id:
        raise HTTPException(409, "The upstream job has no matching parent commit event set")
    if job.group_operator is not None and (
        not exact_nested_job(job, events) or not await positive_nested_events(session, events)
    ):
        raise HTTPException(409, "The immutable upstream witness is not valid for this nested rule")
    if job.state == "committed":
        return job, events, None, []
    if job.state not in ("pending", "dispatched"):
        raise HTTPException(409, "This upstream job already has a terminal outcome")
    current = now or datetime.now(timezone.utc)
    if current < max(event.recorded_at for event in events):
        raise HTTPException(409, {"code": "schedule_not_due", "message": "Upstream event time has not arrived"})
    if current >= job.expires_at:
        raise HTTPException(409, {"code": "schedule_expired", "message": "Upstream event dispatch window expired"})
    binding = await session.get(UpstreamTriggerRow, child.id, populate_existing=True)
    reviewed = {member.parent_plan_id: member for member in (await session.scalars(
        select(UpstreamTriggerParentRow).where(UpstreamTriggerParentRow.plan_id == child.id).limit(4),
    )).all()}
    parents = [locked_plans.get(parent_id) for parent_id in sorted(reviewed)]
    if (binding is None or not binding.enabled or binding.generation != job.generation
        or binding.armed_by != actor_id or binding.operator != job.operator or not 1 <= len(reviewed) <= 3
        or binding.group_operator != job.group_operator or binding.group_parent_ids != job.group_parent_ids
        or (job.group_operator is not None and job.rule_parent_ids != sorted(reviewed))
        or binding.child_updated_at != child.updated_at or child.updated_at != job.child_updated_at
        or not valid_witness(job.operator, job.group_operator, job.group_parent_ids, reviewed.keys(),
                             [event.parent_plan_id for event in events])
        or any(parent is None or not upstream_pair_eligible(child, parent)
               or reviewed[parent.id].parent_updated_at != parent.updated_at for parent in parents)
        or len({parent.source_table for parent in parents}) != len(parents)
        or any(event.parent_plan_id not in reviewed
               or event.id <= reviewed[event.parent_plan_id].cutoff_event_id
               or event.parent_updated_at != reviewed[event.parent_plan_id].parent_updated_at for event in events)):
        raise HTTPException(409, "The upstream trigger was paused or its reviewed plan versions changed")
    actor = await session.scalar(select(User.id).where(
        User.id == actor_id, User.status == StatusTypes.ENABLE, User.is_superuser.is_(True),
    ).with_for_update(read=True))
    if actor is None:
        raise HTTPException(403, "The administrator who armed this upstream trigger is no longer eligible")
    if job.dispatched_at is None:
        job.dispatched_at = current
    return job, events, binding, parents


async def run_sandbox_transfer(
    session: AsyncSession, plan_id: UUID, request: TransferRunRequest, actor_id: UUID, *,
    scheduled_job_id: UUID | None = None, scheduled_now: datetime | None = None,
    upstream_job_id: UUID | None = None,
) -> TransferRun:
    if not transfer_run_capabilities().enabled:
        raise HTTPException(403, "Sandbox transfer execution is disabled")
    if upstream_job_id is not None and (scheduled_job_id is not None or not upstream_transfer_enabled()):
        raise HTTPException(403, "Upstream sandbox transfer execution is disabled")
    if upstream_job_id is None and scheduled_job_id is None and request.requestId.version != 4:
        raise HTTPException(422, "Manual sandbox runs require a UUIDv4 request ID")
    draft = await session.get(TransferPlanRow, plan_id, populate_existing=True)
    if draft is None:
        raise HTTPException(404, "Transfer draft does not exist")
    scope = await _pin_attempt_scope(session, draft, request, scheduled_job_id=scheduled_job_id,
                                    upstream_job_id=upstream_job_id)
    attempt = TransferAttemptRow(plan_id=plan_id, request_id=request.requestId, started_at=datetime.now(timezone.utc),
                                 upstream_job_id=upstream_job_id, **scope)
    attempt_id = attempt.id
    session.add(attempt)
    await session.commit()
    try:
        await session.execute(text("SET LOCAL search_path = pg_catalog, public, pg_temp"))
        await session.execute(text("SET LOCAL lock_timeout = '2s'"))
        await session.execute(text("SET LOCAL statement_timeout = '5s'"))
        parents = []
        upstream_events = []
        upstream_binding = None
        if upstream_job_id is not None:
            parent_ids = list((await session.scalars(select(UpstreamTriggerParentRow.parent_plan_id)
                               .where(UpstreamTriggerParentRow.plan_id == plan_id).limit(4))).all())
            if len(parent_ids) > 3:
                raise HTTPException(409, "The upstream trigger has an invalid reviewed parent set")
            historical_parent_ids = await session.scalar(select(UpstreamTriggerJobRow.rule_parent_ids).where(
                UpstreamTriggerJobRow.id == upstream_job_id, UpstreamTriggerJobRow.plan_id == plan_id,
            ))
            locked = await lock_transfer_plans(session, plan_id, *set(parent_ids).union(historical_parent_ids or []))
            plan = locked.get(plan_id)
        else:
            plan = (await lock_transfer_plans(session, plan_id)).get(plan_id)
        if plan is None:
            raise HTTPException(404, "Transfer draft does not exist")
        job = (await _lock_schedule_job(session, plan, request, scheduled_job_id, actor_id, scheduled_now)
               if scheduled_job_id is not None else None)
        if upstream_job_id is not None:
            job, upstream_events, upstream_binding, parents = await _lock_upstream_job(
                session, plan, locked, request, upstream_job_id, actor_id, scheduled_now,
            )
        existing = (await session.scalars(select(TransferRunRow).where(
            TransferRunRow.plan_id == plan_id, TransferRunRow.request_id == request.requestId,
        ))).one_or_none()
        if existing is not None:
            if existing.plan_updated_at != request.expectedUpdatedAt:
                raise HTTPException(409, "This request ID was used with another draft version")
            if job is not None and (job.state != "committed" or job.receipt_id != existing.id
                                    or (upstream_job_id is not None and existing.initiated_by != UPSTREAM_SYSTEM_ACTOR)):
                raise HTTPException(409, "The request identifier already has an unrelated transfer receipt")
            result = _run_response(existing)
            await _finish_attempt(session, attempt_id, "replayed", receipt_id=existing.id)
            await session.commit()
            return result

        if plan.updated_at != request.expectedUpdatedAt:
            raise HTTPException(409, "The draft changed. Reload it before starting a transfer")
        if plan.source_schema != SANDBOX or plan.target_schema != SANDBOX:
            raise HTTPException(422, "Both tables must be in the dedicated transfer sandbox schema")
        if job is None and plan.trigger != "manual":
            raise HTTPException(422, "Only manual transfer plans can run in the sandbox pilot")
        if job is not None and job.state != "pending" and job.state != "dispatched":
            raise HTTPException(409, "This UTC schedule slot cannot start a new transfer")

        source = ".".join(map(quote_identifier, (SANDBOX, plan.source_table)))
        target = ".".join(map(quote_identifier, (SANDBOX, plan.target_table)))
        names = {plan.source_table, plan.target_table}
        unsupported = "The sandbox pilot supports only plain tables with built-in types and no triggers, rules, inheritance, or custom constraints or indexes"
        if {row["name"] for row in await _sandbox_tables(session, plan.source_table, plan.target_table)} != names:
            raise HTTPException(422, unsupported)
        # Lock table names in a stable order. SHARE excludes source mutations;
        # SHARE ROW EXCLUSIVE serializes all writes/append checks to the target.
        table_locks = [(source, "SHARE"), (target, "SHARE ROW EXCLUSIVE")]
        if upstream_binding is not None:
            await upstream_catalog(session, plan, parents)  # Refuse nonordinary tables before taking an actual lock.
            parent_sources = {".".join(map(quote_identifier, (SANDBOX, parent.source_table))) for parent in parents}
            table_locks.extend((name, "ACCESS SHARE") for name in parent_sources)
        for name, mode in sorted(table_locks):
            await session.execute(text(f"LOCK TABLE ONLY {name} IN {mode} MODE"))
        if upstream_binding is not None:
            physical = await upstream_catalog(session, plan, parents)
            if upstream_catalog_digest(physical) != upstream_binding.physical_digest:
                raise HTTPException(409, "An approved physical upstream table changed; review and arm again")
            for event in upstream_events:
                parent = locked[event.parent_plan_id]
                p_source = physical[parent.source_table]
                p_target = physical[parent.target_table]
                if (p_source["table_oid"], p_source["table_node"], p_target["table_oid"], p_target["table_node"]) != (
                    event.source_oid, event.source_node, event.target_oid, event.target_node,
                ):
                    raise HTTPException(409, "An approved physical upstream table changed; review and arm again")
            if (scheduled_now or datetime.now(timezone.utc)) >= job.expires_at:
                raise HTTPException(409, {"code": "schedule_expired", "message": "Upstream dispatch window closed before reading"})

        incremental = plan.incremental_cursor is not None
        checkpoint = (await session.scalars(select(TransferCheckpointRow).where(or_(
            TransferCheckpointRow.plan_id == plan_id, and_(
                TransferCheckpointRow.source_table == plan.source_table, TransferCheckpointRow.target_table == plan.target_table,
            ),
        )).limit(1))).first()
        if checkpoint is not None and (
            not incremental or checkpoint.plan_id != plan_id or checkpoint.source_table != plan.source_table
            or checkpoint.target_table != plan.target_table or checkpoint.cursor_column != plan.incremental_cursor
        ):
            raise HTTPException(409, "This route is bound to a different incremental configuration")
        if plan.write_mode == "append":
            prior = (await session.scalars(select(TransferRunRow.id).where(
                TransferRunRow.has_more.is_(None),
                (True if incremental else TransferRunRow.write_mode == "append"),
                or_(TransferRunRow.plan_id == plan_id, and_(
                    TransferRunRow.source_schema == SANDBOX, TransferRunRow.source_table == plan.source_table,
                    TransferRunRow.target_schema == SANDBOX, TransferRunRow.target_table == plan.target_table,
                )),
            ).limit(1))).first()
            if prior is not None:
                raise HTTPException(409, "This route already has a non-incremental run; a new Append cannot proceed")

        source_ref = (SANDBOX, plan.source_table)
        target_ref = (SANDBOX, plan.target_table)
        endpoints = await get_etl_layers().transfer_endpoint_columns_in_session(session, source_ref, target_ref)
        preflight = compare_transfer_endpoints(_response(plan), endpoints)
        if preflight.status == "blocked":
            raise HTTPException(422, {"message": "The current table structure failed preflight",
                                      "issues": [issue.model_dump(exclude_none=True) for issue in preflight.issues]})

        catalog = {row["name"]: row for row in await _sandbox_tables(session, plan.source_table, plan.target_table)}
        if set(catalog) != names:
            raise HTTPException(422, unsupported)

        admitted_target = catalog[plan.target_table]
        if (scope.get("target_table") != plan.target_table
            or (scope.get("target_oid"), scope.get("target_filenode")) !=
               (admitted_target["table_oid"], admitted_target["table_node"])):
            raise HTTPException(409, "The admitted transfer destination changed before execution")
        limit_params = {"limit": MAX_ROWS}
        selection = ""
        has_more = None
        keys = None
        if incremental:
            identity = dict(
                source_oid=catalog[plan.source_table]["table_oid"], source_node=catalog[plan.source_table]["table_node"],
                target_oid=catalog[plan.target_table]["table_oid"], target_node=catalog[plan.target_table]["table_node"],
            )
            if checkpoint is None:
                checkpoint = TransferCheckpointRow(plan_id=plan_id, source_table=plan.source_table,
                                                   target_table=plan.target_table, cursor_column=plan.incremental_cursor,
                                                   **identity)
                session.add(checkpoint)
            elif any(getattr(checkpoint, key) != value for key, value in identity.items()):
                raise HTTPException(409, "A checkpoint table was replaced or rewritten; this route needs review")
            keys, has_more = await _incremental_batch(
                session, source, plan.incremental_cursor, endpoints[source_ref]["index_name"], checkpoint.cursor_through,
            )
            if keys:
                key = quote_identifier(plan.incremental_cursor)
                selection = f"WHERE {key} BETWEEN CAST(:first_key AS bigint) AND CAST(:last_key AS bigint) ORDER BY {key}"
                limit_params.update(first_key=keys[0], last_key=keys[-1])
        else:
            count = (await session.execute(text(
                f"SELECT count(*) FROM (SELECT 1 FROM ONLY {source} LIMIT :limit) AS bounded_source",
            ), {"limit": MAX_ROWS + 1})).scalar_one()
            if count > MAX_ROWS:
                raise HTTPException(422, f"The source exceeds the sandbox limit of {MAX_ROWS} rows")

        columns = [quote_identifier(name) for name in endpoints[target_ref]["columns"]]
        column_list = ", ".join(columns)
        conflict = ""
        if plan.write_mode == "upsert":
            target_meta = catalog[plan.target_table]
            constraint = quote_identifier(target_meta["primary_name"])
            conflict_keys = [quote_identifier(key) for key in target_meta["key_columns"]]
            assignments = ", ".join(f"{name} = EXCLUDED.{name}" for name in ([
                name for name in columns if name not in conflict_keys
            ] or conflict_keys[:1]))
            conflict = f"ON CONFLICT ON CONSTRAINT {constraint} DO UPDATE SET {assignments}"

        source_sql = f"SELECT {column_list} FROM ONLY {source} {selection} LIMIT :limit"
        if keys:
            await _require_index_range_plan(
                session, source_sql, limit_params, endpoints[source_ref]["index_name"], ranged=True,
            )
        if job is not None and (scheduled_now or datetime.now(timezone.utc)) >= job.expires_at:
            raise HTTPException(409, {"code": "schedule_expired", "message": "UTC dispatch window closed before writing"})
        written = 0 if keys == [] else (await session.execute(text(f"""
            WITH inserted AS (
                INSERT INTO {target} ({column_list})
                {source_sql}
                {conflict}
                RETURNING 1
            )
            SELECT count(*) FROM inserted
        """), limit_params)).scalar_one()
        if keys:
            if written != len(keys):
                raise HTTPException(409, "Incremental result did not match the selected key batch")
            checkpoint.cursor_through = keys[-1]
        receipt = TransferRunRow(
            plan_id=plan_id, request_id=request.requestId, plan_updated_at=plan.updated_at,
            initiated_by=(UPSTREAM_SYSTEM_ACTOR if upstream_job_id is not None else
                          SCHEDULED_SYSTEM_ACTOR if job is not None else actor_id),
            source_schema=SANDBOX, source_table=plan.source_table,
            target_schema=SANDBOX, target_table=plan.target_table,
            target_oid=catalog[plan.target_table]["table_oid"],
            target_filenode=catalog[plan.target_table]["table_node"],
            write_mode=plan.write_mode, rows_affected=written, recorded_at=datetime.now(timezone.utc),
            cursor_through=checkpoint.cursor_through if incremental else None, has_more=has_more,
        )
        session.add(receipt)
        result = _run_response(receipt)
        await session.flush()
        await _finish_attempt(session, attempt_id, "committed", receipt_id=receipt.id)
        if job is None and written > 0 and upstream_transfer_enabled() and is_sandbox_upsert(plan, "manual"):
            # The same parent plan row stays locked across this event and
            # receipt's atomic commit, and across the arming cutoff query.
            event_source = catalog[plan.source_table]
            event_target = catalog[plan.target_table]
            session.add(TransferCommitEventRow(
                parent_plan_id=plan.id, parent_receipt_id=receipt.id, parent_updated_at=plan.updated_at,
                source_oid=event_source["table_oid"], source_node=event_source["table_node"],
                target_oid=event_target["table_oid"], target_node=event_target["table_node"], recorded_at=receipt.recorded_at,
            ))
        if job is not None:
            job.state = "committed"
            job.receipt_id = receipt.id
            job.finished_at = scheduled_now or datetime.now(timezone.utc)
        await session.commit()
        return result
    except DBAPIError as exc:
        await session.rollback()
        state = getattr(exc.orig, "sqlstate", None)
        if state == "21000":
            failure = HTTPException(422, "Transfer source contains duplicate projected primary keys; nothing was committed")
        elif state in {"23502", "23503", "23505", "23514"}:
            failure = HTTPException(422, "A target constraint rejected the transfer; nothing was committed")
        elif state == "42P01":
            failure = HTTPException(422, "A transfer table or required catalog is unavailable")
        elif state in {"55P03", "57014", "40P01"}:
            failure = HTTPException(409, "Sandbox is busy or the statement timed out; retry with the same request ID")
        else:
            failure = HTTPException(503, "Transfer outcome not confirmed; retry with the same request ID")
        await _record_attempt_error(session, attempt_id, failure, scheduled_job_id=scheduled_job_id, upstream_job_id=upstream_job_id,
                                    retryable=state in {"55P03", "57014", "40P01"}, scheduled_now=scheduled_now)
        raise failure from exc
    except HTTPException as exc:
        await session.rollback()
        early_delivery = isinstance(exc.detail, dict) and exc.detail.get("code") == "schedule_not_due"
        await _record_attempt_error(session, attempt_id, exc, scheduled_job_id=scheduled_job_id, upstream_job_id=upstream_job_id,
                                    retryable=early_delivery, scheduled_now=scheduled_now)
        raise
    except Exception as exc:
        await session.rollback()
        failure = HTTPException(503, "Transfer outcome not confirmed; retry with the same request ID")
        await _record_attempt_error(session, attempt_id, failure, scheduled_job_id=scheduled_job_id, upstream_job_id=upstream_job_id,
                                    scheduled_now=scheduled_now)
        raise failure from exc
