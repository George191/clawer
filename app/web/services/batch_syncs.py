"""Managed, manually triggered PostgreSQL snapshot Upsert in the local sandbox."""

from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
import logging
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import and_, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.batch_syncs import (
    BATCH_ROWS, MAX_MAPPINGS, TARGET_SCHEMA, BatchSyncAttempt, BatchSyncAttemptRow, BatchSyncCapabilities,
    BatchSyncPayload, BatchSyncPlan, BatchSyncPlanRow, BatchSyncReceipt, BatchSyncReceiptRow,
    BatchSyncRequestRow, BatchSyncRunRequest,
)
from app.web.api.models.batch_sync_schedules import BatchSyncScheduleJobRow, BatchSyncScheduleRow
from app.web.api.models.user import User
from app.web.api.utils.models import StatusTypes
from app.web.core.config import settings
from app.web.services.data_connections import get_data_connections
from app.web.services.batch_sync_scope import local_batch_sync_scope
from app.web.services.etl_layers import quote_identifier
from app.web.services.postgres_sync_source import read_batch_sync_snapshot
from app.web.services.transfer_runs import _sandbox_tables

_KEY_TYPES = {20, 21, 23, 2950}  # bigint, smallint, integer, UUID
_HISTORY_LIMIT = 25
_PLAN_LIMIT = 100
_SCHEDULE_ACTOR = uuid5(NAMESPACE_URL, "asiral:batch-sync-scheduler:local")
logger = logging.getLogger(__name__)


class _RunFailure(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, outcome: str = "rejected"):
        super().__init__(status_code, {"code": code, "message": message})
        self.code = code
        self.message = message
        self.outcome = outcome


def _batch_sync_enabled() -> bool:
    return (
        "ENVIRONMENT" in settings.model_fields_set and settings.ENVIRONMENT == "local"
        and settings.BATCH_SYNC_SANDBOX_ENABLED
    )


def scheduled_batch_sync_enabled() -> bool:
    return _batch_sync_enabled() and settings.BATCH_SYNC_SANDBOX_SCHEDULER_ENABLED and local_batch_sync_scope() is not None


def batch_sync_capabilities() -> BatchSyncCapabilities:
    return BatchSyncCapabilities(enabled=_batch_sync_enabled(), scheduledBatchSync=scheduled_batch_sync_enabled())


def _plan_response(row: BatchSyncPlanRow) -> BatchSyncPlan:
    return BatchSyncPlan(
        id=row.id, name=row.name, connectionId=row.connection_id, sourceSchema=row.source_schema,
        sourceTable=row.source_table, sourceKeyColumn=row.source_key_column, targetTable=row.target_table,
        mapping=row.mapping, sourceUpdatedAt=row.source_updated_at, updatedAt=row.updated_at,
    )


def _receipt_response(row: BatchSyncReceiptRow) -> BatchSyncReceipt:
    return BatchSyncReceipt(
        id=row.id, planId=row.plan_id, rowsAffected=row.rows_affected, recordedAt=row.recorded_at,
        sourceUpdatedAt=row.source_updated_at, targetTable=row.target_table,
    )


async def list_batch_syncs(
    session: AsyncSession, connection_id: UUID | None, plan_id: UUID | None = None,
) -> list[BatchSyncPlan]:
    statement = select(BatchSyncPlanRow)
    if connection_id is not None:
        statement = statement.where(BatchSyncPlanRow.connection_id == connection_id)
    if plan_id is not None:
        statement = statement.where(BatchSyncPlanRow.id == plan_id)
    rows = await session.scalars(statement.order_by(BatchSyncPlanRow.updated_at.desc(), BatchSyncPlanRow.id).limit(_PLAN_LIMIT))
    return [_plan_response(row) for row in rows]


async def list_batch_sync_runs(session: AsyncSession, plan_id: UUID) -> list[BatchSyncReceipt]:
    if await session.get(BatchSyncPlanRow, plan_id) is None:
        raise HTTPException(404, "Batch sync plan does not exist")
    rows = await session.scalars(select(BatchSyncReceiptRow).where(BatchSyncReceiptRow.plan_id == plan_id)
                                 .order_by(BatchSyncReceiptRow.recorded_at.desc(), BatchSyncReceiptRow.id)
                                 .limit(_HISTORY_LIMIT))
    return [_receipt_response(row) for row in rows]


async def list_batch_sync_attempts(session: AsyncSession, plan_id: UUID) -> list[BatchSyncAttempt]:
    if await session.get(BatchSyncPlanRow, plan_id) is None:
        raise HTTPException(404, "Batch sync plan does not exist")
    statement = (select(BatchSyncAttemptRow, BatchSyncRequestRow.plan_updated_at)
                 .join(BatchSyncRequestRow, and_(
                     BatchSyncRequestRow.plan_id == BatchSyncAttemptRow.plan_id,
                     BatchSyncRequestRow.request_id == BatchSyncAttemptRow.request_id,
                 ))
                 .where(BatchSyncAttemptRow.plan_id == plan_id)
                 .order_by(BatchSyncAttemptRow.started_at.desc(), BatchSyncAttemptRow.id.desc())
                 .limit(_HISTORY_LIMIT))
    return [BatchSyncAttempt(
        id=row.id, planId=row.plan_id, requestId=row.request_id, planUpdatedAt=version,
        status=row.status, startedAt=row.started_at, finishedAt=row.finished_at, receiptId=row.receipt_id,
        code=row.code, message=row.message, scheduledJobId=row.scheduled_job_id,
    ) for row, version in (await session.execute(statement)).all()]


async def _set_run_limits(session: AsyncSession) -> None:
    await session.execute(text("SET LOCAL search_path = pg_catalog, public, pg_temp"))
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    await session.execute(text("SET LOCAL idle_in_transaction_session_timeout = '12s'"))


async def _record_start(
    session: AsyncSession, plan_id: UUID, req: BatchSyncRunRequest, actor_id: UUID, scheduled_job_id: UUID | None,
) -> UUID:
    await _set_run_limits(session)
    if await session.scalar(select(BatchSyncPlanRow.id).where(BatchSyncPlanRow.id == plan_id)) is None:
        raise HTTPException(404, "Batch sync plan does not exist")
    # Existing receipts predate attempts. Prefer their exact version even if a
    # receipt was inserted by a previous application process during upgrade.
    legacy = await session.scalar(select(BatchSyncReceiptRow.plan_updated_at).where(
        BatchSyncReceiptRow.plan_id == plan_id, BatchSyncReceiptRow.request_id == req.requestId,
    ))
    await session.execute(insert(BatchSyncRequestRow).values(
        plan_id=plan_id, request_id=req.requestId, plan_updated_at=legacy or req.expectedUpdatedAt,
    ).on_conflict_do_nothing())
    bound = await session.get(BatchSyncRequestRow, (plan_id, req.requestId))
    if bound is None or bound.plan_updated_at != req.expectedUpdatedAt:
        raise _RunFailure(409, "request_revision_conflict", "This request ID belongs to a different plan version")
    attempt = BatchSyncAttemptRow(plan_id=plan_id, request_id=req.requestId, initiated_by=actor_id,
                                  started_at=datetime.now(timezone.utc), scheduled_job_id=scheduled_job_id)
    session.add(attempt)
    attempt_id = attempt.id
    await session.commit()
    return attempt_id


async def _finish_attempt(
    session: AsyncSession, attempt_id: UUID, state: str, *, receipt_id: UUID | None = None,
    code: str | None = None, message: str | None = None,
) -> int:
    result = await session.execute(update(BatchSyncAttemptRow).where(
        BatchSyncAttemptRow.id == attempt_id, BatchSyncAttemptRow.status == "started",
    ).values(status=state, finished_at=datetime.now(timezone.utc), receipt_id=receipt_id, code=code, message=message))
    return result.rowcount


async def _rollback(session: AsyncSession) -> bool:
    try:
        await session.rollback()
    except Exception:
        return False
    return True


async def _finish_after_rollback(
    session: AsyncSession, attempt_id: UUID, state: str, *,
    schedule_job_id: UUID | None = None, schedule_state: str | None = None,
    scheduled_now: datetime | None = None, **outcome,
) -> None:
    try:
        await _set_run_limits(session)
        await _finish_attempt(session, attempt_id, state, **outcome)
        if schedule_job_id is not None and schedule_state is not None:
            await session.execute(update(BatchSyncScheduleJobRow).where(
                BatchSyncScheduleJobRow.id == schedule_job_id,
                BatchSyncScheduleJobRow.state.in_(("pending", "dispatched")),
            ).values(state=schedule_state, finished_at=scheduled_now or datetime.now(timezone.utc),
                     message=outcome.get("message")))
        await session.commit()
    except Exception:
        await _rollback(session)
        logger.warning("Batch sync attempt outcome could not be recorded: %s", attempt_id)


async def _verify_source_revision(session: AsyncSession, connection_id: UUID, expected: datetime, *, lock: bool) -> None:
    # This is the app's registry, not a query to the saved external source.
    lock_clause = " FOR SHARE" if lock else ""
    row = (await session.execute(text("""
        SELECT engine, config->>'host' AS host, updated_at
        FROM public.ontology_data_connections WHERE id=:id
    """ + lock_clause), {"id": connection_id})).mappings().first()
    if row is None:
        raise HTTPException(404, "Saved PostgreSQL source does not exist")
    if row["updated_at"] != expected:
        raise _RunFailure(409, "source_revision_changed",
                          "Saved source settings changed; reopen the source catalog and save a new plan version")
    if row["engine"] != "postgresql":
        raise HTTPException(422, "This batch pilot requires a saved PostgreSQL connection")
    host = row["host"]
    try:
        local = "%" not in host and ip_address(host).is_loopback
    except (TypeError, ValueError):
        local = False
    if not local:
        raise HTTPException(422, "This batch pilot requires a saved numeric loopback address")


async def _target_metadata(session: AsyncSession, table: str):
    catalog = await _sandbox_tables(session, table, table, schema=TARGET_SCHEMA)
    if len(catalog) != 1:
        raise HTTPException(422, "The pre-existing batch target must be a plain sandbox table without triggers, rules, or custom constraints")
    target = catalog[0]
    keys = target["key_columns"]
    if len(keys) != 1:
        raise HTTPException(422, "The batch target requires one non-deferrable primary-key column")
    cols = (await session.execute(text("""
        SELECT a.attname::text AS name, a.atttypid AS type_oid, a.atttypmod AS typmod,
               a.attnotnull AS not_null, a.attgenerated::text AS generated, a.attidentity::text AS identity
        FROM pg_catalog.pg_attribute a
        WHERE a.attrelid=:table_oid AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum LIMIT :limit
    """), {"table_oid": target["table_oid"], "limit": MAX_MAPPINGS + 1})).mappings().all()
    if not cols or len(cols) > MAX_MAPPINGS or any(col["generated"] or col["identity"] for col in cols):
        raise HTTPException(422, "The batch target must have 1–32 writable columns and no generated or identity columns")
    by_name = {col["name"]: col for col in cols}
    if by_name[keys[0]]["type_oid"] not in _KEY_TYPES:
        raise HTTPException(422, "The batch target primary key must be an integer or UUID")
    return target, by_name


def _check_target_mapping(mapping, source_key: str, target, columns):
    if set(columns) != {item.targetColumn for item in mapping}:
        raise HTTPException(422, "Map every writable batch target column exactly once")
    mapped_key = next(item.targetColumn for item in mapping if item.sourceColumn == source_key)
    if mapped_key != target["key_columns"][0]:
        raise HTTPException(422, "Map the source primary key to the target primary key")


async def save_batch_sync(
    session: AsyncSession, payload: BatchSyncPayload, plan_id: UUID | None = None,
) -> BatchSyncPlan:
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    if plan_id is not None:
        row = (await session.scalars(select(BatchSyncPlanRow).where(BatchSyncPlanRow.id == plan_id)
                                      .with_for_update().execution_options(populate_existing=True))).one_or_none()
        if row is None:
            raise HTTPException(404, "Batch sync plan does not exist")
    else:
        row = None
    await _verify_source_revision(session, payload.connectionId, payload.expectedSourceUpdatedAt, lock=True)
    target, columns = await _target_metadata(session, payload.targetTable)
    _check_target_mapping(payload.mapping, payload.sourceKeyColumn, target, columns)
    attrs = dict(
        name=payload.name.strip(), connection_id=payload.connectionId, source_schema=payload.sourceSchema,
        source_table=payload.sourceTable, source_key_column=payload.sourceKeyColumn, target_table=payload.targetTable,
        mapping=[item.model_dump() for item in payload.mapping], source_updated_at=payload.expectedSourceUpdatedAt,
        target_oid=target["table_oid"], target_file_node=target["table_node"],
    )
    now = datetime.now(timezone.utc)
    if row is None:
        row = BatchSyncPlanRow(**attrs, updated_at=now)
        session.add(row)
    else:
        for key, value in attrs.items():
            setattr(row, key, value)
        row.updated_at = max(now, row.updated_at + timedelta(microseconds=1))
    result = _plan_response(row)
    await session.commit()
    return result


def _check_source_projection(plan: BatchSyncPlan, columns, source_metadata):
    by_name = {item["name"]: item for item in source_metadata}
    if set(by_name) != {item.sourceColumn for item in plan.mapping}:
        raise HTTPException(422, "The saved source projection changed; review the batch mapping")
    for item in plan.mapping:
        source = by_name[item.sourceColumn]
        target = columns[item.targetColumn]
        if (source["typeOid"], source["typmod"]) != (target["type_oid"], target["typmod"]):
            raise HTTPException(422, f"Source and target types do not match for {item.targetColumn}")
        if source["nullable"] and target["not_null"]:
            raise HTTPException(422, f"Nullable source is incompatible with NOT NULL target {item.targetColumn}")
        if item.sourceColumn == plan.sourceKeyColumn and not source["primaryKey"]:
            raise HTTPException(422, "The selected source primary key changed")


def _source_failure(error: HTTPException) -> _RunFailure:
    if error.status_code == 504:
        return _RunFailure(504, "source_timeout", "The PostgreSQL source timed out; retry with the same request ID", "retryable")
    if error.status_code >= 500:
        return _RunFailure(502, "source_unavailable", "The saved PostgreSQL source is unavailable; retry with the same request ID", "retryable")
    if error.status_code == 409:
        return _RunFailure(409, "source_changed", "Source schema, revision, or read permissions changed; review the plan")
    if isinstance(error.detail, str) and ("100-row" in error.detail or "8 KiB" in error.detail):
        return _RunFailure(422, "source_limit", "The source exceeded the row count or per-row byte limit; no batch was written")
    return _RunFailure(422, "source_rejected", "The source failed batch eligibility checks; review the plan")


def _run_failure(error: Exception, *, committing: bool) -> _RunFailure:
    if isinstance(error, _RunFailure):
        return error
    if isinstance(error, DBAPIError):
        code = getattr(error.orig, "sqlstate", None)
        if code in {"55P03", "57014", "40P01", "40001"}:
            return _RunFailure(409, "target_busy", "Batch target or plan is busy; retry with the same request ID", "retryable")
        if code in {"42P01", "42703", "42P10"}:
            return _RunFailure(409, "target_changed", "Batch target or its primary key changed; review the plan")
        if code in {"21000", "23502", "23503", "23505", "23514"}:
            return _RunFailure(422, "target_rejected", "The target rejected the batch; no data or receipt was committed")
    if committing:
        return _RunFailure(503, "outcome_unknown", "Batch commit could not be confirmed; retry with the same request ID", "outcome_unknown")
    if isinstance(error, HTTPException) and error.status_code < 500:
        return _RunFailure(error.status_code, "definition_rejected", "The batch definition is no longer compatible; review the plan")
    return _RunFailure(503, "execution_unavailable", "Batch stopped before commit; retry with the same request ID", "retryable")


def _check_job_time(job: BatchSyncScheduleJobRow, now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current < job.slot:
        raise _RunFailure(409, "schedule_not_due", "The UTC slot has not opened", "retryable")
    if current >= job.expires_at:
        raise _RunFailure(409, "schedule_expired", "The UTC slot expired before the batch could proceed")
    return current


async def _lock_batch_schedule_job(
    session: AsyncSession, row: BatchSyncPlanRow, req: BatchSyncRunRequest,
    actor_id: UUID, job_id: UUID, scheduled_now: datetime | None,
) -> BatchSyncScheduleJobRow:
    # Every binding mutation takes the same plan lock. Re-read the job in case
    # another worker settled it while this delivery waited.
    job = (await session.scalars(select(BatchSyncScheduleJobRow).where(BatchSyncScheduleJobRow.id == job_id)
                                 .with_for_update().execution_options(populate_existing=True))).one_or_none()
    if (job is None or job.plan_id != row.id or job.id != req.requestId or job.armed_by != actor_id
        or req.expectedUpdatedAt != job.plan_updated_at):
        raise _RunFailure(409, "schedule_request_conflict", "The schedule job is not bound to this batch request")
    if not scheduled_batch_sync_enabled():
        raise _RunFailure(403, "schedule_disabled", "Scheduled batch execution is disabled")
    if job.state == "committed":
        return job
    if job.state not in ("pending", "dispatched"):
        raise _RunFailure(409, "schedule_settled", "This schedule job already has a terminal outcome")
    current = _check_job_time(job, scheduled_now)
    binding = await session.get(BatchSyncScheduleRow, row.id, populate_existing=True)
    if (binding is None or not binding.enabled or binding.generation != job.generation
        or binding.armed_by != actor_id or binding.armed_for_updated_at != job.plan_updated_at
        or binding.source_updated_at != job.source_updated_at or row.updated_at != job.plan_updated_at
        or row.source_updated_at != job.source_updated_at
        or binding.planned_time_utc != job.slot.astimezone(timezone.utc).strftime("%H:%M")):
        raise _RunFailure(409, "schedule_stale", "The schedule was paused or its reviewed plan/source version changed")
    if binding.execution_scope != local_batch_sync_scope():
        raise _RunFailure(409, "schedule_scope_mismatch", "This worker does not share the loopback scope that armed the schedule")
    eligible_actor = await session.scalar(select(User.id).where(
        User.id == actor_id, User.status == StatusTypes.ENABLE, User.is_superuser.is_(True),
    ).with_for_update(read=True))
    if eligible_actor is None:
        raise _RunFailure(403, "schedule_actor_revoked", "The administrator who armed this schedule is no longer eligible")
    if job.dispatched_at is None:
        job.dispatched_at = current
    return job


async def run_batch_sync(
    session: AsyncSession, plan_id: UUID, req: BatchSyncRunRequest, actor_id: UUID, *,
    scheduled_job_id: UUID | None = None, scheduled_now: datetime | None = None,
) -> BatchSyncReceipt:
    if not _batch_sync_enabled():
        raise HTTPException(403, "Batch sync execution is disabled")
    if scheduled_job_id is None and req.requestId.version != 4:
        raise _RunFailure(409, "scheduled_request_reserved", "Manual runs require a UUIDv4 request ID")
    effective_actor = _SCHEDULE_ACTOR if scheduled_job_id is not None else actor_id
    try:
        attempt_id = await _record_start(session, plan_id, req, effective_actor, scheduled_job_id)
    except HTTPException:
        await _rollback(session)
        raise
    except Exception:
        await _rollback(session)
        raise _RunFailure(503, "execution_unavailable", "Batch could not start; retry with the same request ID", "retryable") from None

    committing = False
    try:
        await _set_run_limits(session)
        row = (await session.scalars(select(BatchSyncPlanRow).where(BatchSyncPlanRow.id == plan_id)
                                      .with_for_update().execution_options(populate_existing=True))).one_or_none()
        if row is None:
            raise HTTPException(404, "Batch sync plan does not exist")
        job = (await _lock_batch_schedule_job(session, row, req, actor_id, scheduled_job_id, scheduled_now)
               if scheduled_job_id is not None else None)
        previous = (await session.scalars(select(BatchSyncReceiptRow).where(
            BatchSyncReceiptRow.plan_id == plan_id, BatchSyncReceiptRow.request_id == req.requestId,
        ))).one_or_none()
        if previous is not None:
            if previous.plan_updated_at != req.expectedUpdatedAt:
                raise _RunFailure(409, "request_revision_conflict", "This request ID belongs to a different plan version")
            if job is not None and (job.state != "committed" or job.receipt_id != previous.id
                                    or previous.initiated_by != _SCHEDULE_ACTOR):
                raise _RunFailure(409, "schedule_receipt_conflict", "The request already has an unrelated batch receipt")
            result = _receipt_response(previous)
            # A replay only reads an existing authoritative receipt. An audit
            # recording interruption cannot make that already-known result unknown.
            try:
                await _finish_attempt(session, attempt_id, "replayed", receipt_id=result.id)
                await session.commit()
            except Exception:
                if await _rollback(session):
                    await _finish_after_rollback(session, attempt_id, "replayed", receipt_id=result.id)
            return result
        if job is not None and job.state == "committed":
            raise _RunFailure(409, "schedule_receipt_conflict", "The job has no matching committed batch receipt")
        if row.updated_at != req.expectedUpdatedAt:
            raise _RunFailure(409, "plan_changed", "Batch sync plan changed; review it before starting")
        plan = _plan_response(row)
        qualified = f"{quote_identifier(TARGET_SCHEMA)}.{quote_identifier(plan.targetTable)}"
        await _target_metadata(session, plan.targetTable)
        # This is a physical PostgreSQL lock, not a plan-local mutex. A rival
        # request using another plan ID cannot take its source snapshot first.
        await session.execute(text(f"LOCK TABLE ONLY {qualified} IN SHARE ROW EXCLUSIVE MODE NOWAIT"))
        target, columns = await _target_metadata(session, plan.targetTable)
        if target["table_oid"] != row.target_oid or target["table_node"] != row.target_file_node:
            raise _RunFailure(409, "target_rewritten",
                              "The reviewed batch target was replaced or rewritten; review and save the plan again")
        _check_target_mapping(plan.mapping, plan.sourceKeyColumn, target, columns)
        await _verify_source_revision(session, plan.connectionId, plan.sourceUpdatedAt, lock=True)
        if job is not None:
            _check_job_time(job, scheduled_now)
        source_columns = [item.sourceColumn for item in plan.mapping]
        try:
            source_rows, source_meta = await read_batch_sync_snapshot(
                get_data_connections(), str(plan.connectionId), expected_updated_at=plan.sourceUpdatedAt,
                schema=plan.sourceSchema, table=plan.sourceTable, columns=source_columns, key_column=plan.sourceKeyColumn,
            )
        except HTTPException as error:
            raise _source_failure(error) from None
        _check_source_projection(plan, columns, source_meta)
        if len(source_rows) > BATCH_ROWS:
            raise _RunFailure(422, "source_limit", "The source exceeds the 100-row batch limit; no batch was written")
        affected = 0
        if job is not None:
            _check_job_time(job, scheduled_now)
        if source_rows:
            names = [quote_identifier(item.targetColumn) for item in plan.mapping]
            key = quote_identifier(target["key_columns"][0])
            assignments = ", ".join(f"{name} = EXCLUDED.{name}" for name in (
                [name for name in names if name != key] or [key]
            ))
            params = {}
            tuples = []
            for i, source_row in enumerate(source_rows):
                placeholders = []
                for j, source_column in enumerate(source_columns):
                    placeholder = f"v{i}_{j}"
                    params[placeholder] = source_row[source_column]
                    placeholders.append(":" + placeholder)
                tuples.append("(" + ", ".join(placeholders) + ")")
            values = ", ".join(tuples)
            affected = (await session.execute(text(f"""
                WITH merged AS (
                    INSERT INTO {qualified} ({', '.join(names)})
                    VALUES {values}
                    ON CONFLICT ({key}) DO UPDATE SET {assignments}
                    RETURNING 1
                )
                SELECT count(*) FROM merged
            """), params)).scalar_one()
            if affected != len(source_rows):
                raise HTTPException(409, "Target did not acknowledge the complete batch")
        saved = BatchSyncReceiptRow(
            plan_id=plan_id, request_id=req.requestId, initiated_by=effective_actor, connection_id=plan.connectionId,
            source_schema=plan.sourceSchema, source_table=plan.sourceTable, target_table=plan.targetTable,
            plan_updated_at=plan.updatedAt, source_updated_at=plan.sourceUpdatedAt,
            rows_affected=affected, recorded_at=datetime.now(timezone.utc),
        )
        session.add(saved)
        result = _receipt_response(saved)
        await session.flush()
        if await _finish_attempt(session, attempt_id, "committed", receipt_id=saved.id) != 1:
            raise _RunFailure(503, "execution_unavailable", "The batch attempt could not settle; retry with the same request ID",
                              "retryable")
        if job is not None:
            job.state = "committed"
            job.receipt_id = saved.id
            job.finished_at = scheduled_now or datetime.now(timezone.utc)
        committing = True
        await session.commit()
        return result
    except Exception as error:
        failure = _run_failure(error, committing=committing)
        if await _rollback(session):
            # New transaction. The guarded update cannot overwrite a committed
            # attempt if PostgreSQL committed despite losing its acknowledgement.
            job_state = ("expired" if failure.code == "schedule_expired" else failure.outcome
                         if failure.outcome in ("rejected", "outcome_unknown") else None)
            await _finish_after_rollback(session, attempt_id, failure.outcome, code=failure.code, message=failure.message,
                                         schedule_job_id=scheduled_job_id, schedule_state=job_state,
                                         scheduled_now=scheduled_now)
        raise failure from None
