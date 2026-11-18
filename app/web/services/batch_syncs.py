"""Managed, manually triggered PostgreSQL snapshot Upsert in the local sandbox."""

from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
import logging
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import and_, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import select

from app.web.api.models.batch_source_identity import BatchSourceIdentityRow
from app.web.api.models.batch_syncs import (
    BATCH_ROWS, INCREMENTAL_SOURCE_SCHEMA, INCREMENTAL_TARGET, MAX_MAPPINGS, TARGET_SCHEMA,
    BatchSyncAttempt, BatchSyncAttemptRow, BatchSyncCapabilities,
    BatchSyncKeyRange, BatchSyncPayload, BatchSyncPlan, BatchSyncPlanRow, BatchSyncReceipt, BatchSyncReceiptRow,
    BatchSyncRequestRow, BatchSyncRunRequest, managed_batch_scope, range_snapshot_scope,
)
from app.web.api.models.batch_sync_schedules import BatchSyncScheduleJobRow, BatchSyncScheduleRow
from app.web.api.models.user import User
from app.web.api.utils.models import StatusTypes
from app.web.core.config import settings
from app.web.services.data_connections import SavedSourcePaused, get_data_connections, paused_at_sql
from app.web.services.batch_sync_scope import local_batch_sync_scope
from app.web.services.etl_layers import quote_identifier
from app.web.services.batch_source_identity import (
    expected_fingerprint, identity_required, require_legacy_source_scope, require_plan_source_identity, require_source_proof,
)
from app.web.services.batch_sync_incremental import (
    bind_incremental_target, incremental_batch_enabled, incremental_preflight_enabled, lock_incremental_target, require_target_shape,
    range_snapshot_enabled, same_binding, watermark, watermark_scope,
)
from app.web.services.postgres_committed_batch_source import (
    MAX_BATCH_BYTES, MAX_BATCH_ROWS, read_committed_batch, review_committed_batch_source,
)
from app.web.services.batch_snapshot_range_scope import BATCH_RANGE_SOURCE_SCHEMA, BATCH_RANGE_TARGET
from app.web.services.postgres_range_snapshot_source import read_range_snapshot, review_range_snapshot_source
from app.web.services.postgres_sync_source import SourceIdentityMismatch, read_batch_sync_snapshot, review_batch_source
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
    return BatchSyncCapabilities(enabled=_batch_sync_enabled(), scheduledBatchSync=scheduled_batch_sync_enabled(),
                                 sourceIdentityRequired=True if identity_required() else None,
                                 rangeSnapshot=True if range_snapshot_enabled() else None,
                                 incrementalPreflight=True if incremental_preflight_enabled() else None,
                                 incrementalBatchSync=True if incremental_batch_enabled() else None,
                                 incrementalMaxRows=MAX_BATCH_ROWS if incremental_batch_enabled() else None,
                                 incrementalMaxBytes=MAX_BATCH_BYTES if incremental_batch_enabled() else None)


def _plan_response(row: BatchSyncPlanRow, reviewed: bool = False, applied_through: int | None = None) -> BatchSyncPlan:
    return BatchSyncPlan(
        id=row.id, name=row.name, connectionId=row.connection_id, sourceSchema=row.source_schema, mode=row.mode,
        keyRange=_range_response(row),
        appliedThrough=str(applied_through) if applied_through is not None else None,
        sourceTable=row.source_table, sourceKeyColumn=row.source_key_column, targetTable=row.target_table,
        mapping=row.mapping, sourceUpdatedAt=row.source_updated_at, updatedAt=row.updated_at,
        sourceIdentityReviewed=True if reviewed else None,
    )


def _range_response(row) -> BatchSyncKeyRange | None:
    return BatchSyncKeyRange(min=str(row.range_min), max=str(row.range_max)) if row.range_min is not None else None


def _receipt_response(row: BatchSyncReceiptRow) -> BatchSyncReceipt:
    interval = row.from_exclusive is not None
    return BatchSyncReceipt(
        mode="incremental" if interval else None, status=row.outcome, keyRange=_range_response(row),
        fromExclusive=str(row.from_exclusive) if interval else None,
        throughInclusive=str(row.through_inclusive) if interval else None,
        sourceHighWater=str(row.source_high_water) if interval else None,
        eventsRead=row.events_read, manifestHash=row.manifest_hash,
        id=row.id, planId=row.plan_id, rowsAffected=row.rows_affected, recordedAt=row.recorded_at,
        sourceUpdatedAt=row.source_updated_at, targetTable=row.target_table,
        sourceIdentityVerified=True if row.source_identity_verified else None,
    )


async def list_batch_syncs(
    session: AsyncSession, connection_id: UUID | None, plan_id: UUID | None = None,
) -> list[BatchSyncPlan]:
    statement = select(BatchSyncPlanRow)
    if connection_id is not None:
        statement = statement.where(BatchSyncPlanRow.connection_id == connection_id)
    if plan_id is not None:
        statement = statement.where(BatchSyncPlanRow.id == plan_id)
    rows = (await session.scalars(
        statement.order_by(BatchSyncPlanRow.updated_at.desc(), BatchSyncPlanRow.id).limit(_PLAN_LIMIT),
    )).all()
    stream = await watermark(session) if any(item.mode == "incremental" for item in rows) else None

    def scanned(row, reviewed=False):
        return _plan_response(row, reviewed, stream.applied_through
                              if row.mode == "incremental" and same_binding(stream, row) else None)

    expected = expected_fingerprint()
    if expected is None:
        return [scanned(row) for row in rows]
    candidates = {row.connection_id for row in rows if row.source_identity_fingerprint == expected}
    current = (await session.execute(text("""
        SELECT proof.connection_id, proof.source_updated_at
        FROM public.ontology_batch_source_identities proof
        JOIN public.ontology_data_connections conn
          ON conn.id=proof.connection_id AND conn.updated_at=proof.source_updated_at
        WHERE proof.connection_id=ANY(CAST(:ids AS uuid[])) AND proof.fingerprint=:fingerprint AND proof.is_current
    """), {"ids": list(candidates), "fingerprint": expected})).all() if candidates else []
    verified = {tuple(item) for item in current}
    return [scanned(row, row.source_identity_fingerprint == expected
                           and (row.connection_id, row.source_updated_at) in verified) for row in rows]


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
    plan = await session.get(BatchSyncPlanRow, plan_id, populate_existing=True)
    if plan is None:
        raise HTTPException(404, "Batch sync plan does not exist")
    scope = None
    if plan.mode == "incremental" and plan.updated_at == req.expectedUpdatedAt:
        binding = await watermark(session)
        if same_binding(binding, plan):
            scope = watermark_scope(binding)
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
    attempt = BatchSyncAttemptRow(plan_id=plan_id, request_id=req.requestId, initiated_by=actor_id, incremental_scope=scope,
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


async def _saved_source_state(session: AsyncSession, connection_id: UUID, *, lock: bool):
    # This is the app's registry, not a query to the saved external source.
    lock_clause = " FOR SHARE" if lock else ""
    return (await session.execute(text(f"""
        SELECT engine, config->>'host' AS host, updated_at, {paused_at_sql('registry')} AS paused_at
        FROM public.ontology_data_connections registry WHERE id=:id
    """ + lock_clause), {"id": connection_id})).mappings().first()


def _require_active_source(row) -> None:
    if row is not None and row["paused_at"] is not None:
        raise _RunFailure(409, "source_paused", "This saved PostgreSQL source is paused")


def _validate_source_revision(row, expected: datetime) -> None:
    _require_active_source(row)
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


async def _verify_source_revision(session: AsyncSession, connection_id: UUID, expected: datetime, *, lock: bool) -> None:
    _validate_source_revision(await _saved_source_state(session, connection_id, lock=lock), expected)


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


async def _require_range_target_local(session: AsyncSession) -> None:
    engine = session.bind
    try:
        host = ip_address(engine.url.host or "") if isinstance(engine, AsyncEngine) and not engine.url.query else None
    except ValueError:
        host = None
    if host is None or not host.is_loopback or "%" in str(host) or not await session.scalar(text("""
        SELECT pg_catalog.inet_server_addr() = CAST(:address AS inet)
            AND pg_catalog.inet_server_port() = :port
    """), {"address": str(host), "port": engine.url.port or 5432}):
        raise HTTPException(409, "The exact range snapshot target must use the isolated numeric-loopback database")


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
    incremental = payload.mode == "incremental"
    ranged = payload.keyRange is not None
    if row is not None and (row.range_min is not None) != ranged:
        raise HTTPException(409, "A reviewed bounded snapshot cannot be converted into a whole snapshot")
    if ranged:
        if not range_snapshot_enabled():
            raise HTTPException(403, "Managed range snapshots are disabled")
        await _require_range_target_local(session)
    if row is not None and row.mode != payload.mode:
        raise HTTPException(409, "The batch plan mode cannot be changed")
    if incremental and not incremental_batch_enabled():
        raise HTTPException(403, "Managed incremental batch sync is disabled")
    await _verify_source_revision(session, payload.connectionId, payload.expectedSourceUpdatedAt, lock=True)
    stream_digest = None
    fingerprint = None
    pin = None
    if identity_required():
        fingerprint = await require_source_proof(session, payload.connectionId, payload.expectedSourceUpdatedAt)
        marker = settings.BATCH_SYNC_SOURCE_IDENTITY_MARKER
        if marker is None:
            raise HTTPException(409, "The expected local PostgreSQL source identity is not provisioned")
        if incremental:
            stream_digest = await review_committed_batch_source(
                get_data_connections(), str(payload.connectionId), payload.expectedSourceUpdatedAt, marker,
            )
        elif ranged:
            pin = await review_range_snapshot_source(
                get_data_connections(), str(payload.connectionId), payload.expectedSourceUpdatedAt, marker,
            )
        else:
            pin = await review_batch_source(
                get_data_connections(), str(payload.connectionId), payload.expectedSourceUpdatedAt, marker,
                schema=payload.sourceSchema, table=payload.sourceTable,
            )
    else:
        if row is not None and row.source_identity_fingerprint is not None:
            raise HTTPException(409, "Reenable the reviewed local PostgreSQL source identity policy")
        await require_legacy_source_scope(session, payload.connectionId)
    if incremental:
        try:
            await lock_incremental_target(session)
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) in {"55P03", "42P01", "57014"}:
                await session.rollback()
                raise HTTPException(409, "The fixed incremental target is missing or busy") from None
            raise
    target, columns = await _target_metadata(session, payload.targetTable)
    if incremental or ranged:
        require_target_shape(target, columns)
    _check_target_mapping(payload.mapping, payload.sourceKeyColumn, target, columns)
    attrs = dict(
        name=payload.name.strip(), mode=payload.mode, source_stream_digest=stream_digest,
        range_min=int(payload.keyRange.min) if ranged else None, range_max=int(payload.keyRange.max) if ranged else None,
        range_index_oid=pin[2] if ranged else None, range_index_node=pin[3] if ranged else None,
        connection_id=payload.connectionId, source_schema=payload.sourceSchema,
        source_table=payload.sourceTable, source_key_column=payload.sourceKeyColumn, target_table=payload.targetTable,
        mapping=[item.model_dump() for item in payload.mapping], source_updated_at=payload.expectedSourceUpdatedAt,
        target_oid=target["table_oid"], target_file_node=target["table_node"],
        source_identity_fingerprint=fingerprint, source_oid=pin[0] if pin else None,
        source_file_node=pin[1] if pin else None,
    )
    binding = await bind_incremental_target(session, attrs) if incremental else None
    now = datetime.now(timezone.utc)
    if row is None:
        row = BatchSyncPlanRow(**attrs, updated_at=now)
        session.add(row)
    else:
        for key, value in attrs.items():
            setattr(row, key, value)
        row.updated_at = max(now, row.updated_at + timedelta(microseconds=1))
    result = _plan_response(row, fingerprint is not None, binding.applied_through if binding is not None else None)
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
    if isinstance(error, SavedSourcePaused):
        return _RunFailure(409, "source_paused", "This saved PostgreSQL source is paused")
    if isinstance(error, SourceIdentityMismatch):
        return _RunFailure(409, "source_identity_mismatch", "The saved PostgreSQL source no longer matches its reviewed identity")
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
    if job.state in ("committed", "no_new_data"):
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
            if job is not None and (job.state != previous.outcome or job.receipt_id != previous.id
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
        if job is not None and job.state in ("committed", "no_new_data"):
            raise _RunFailure(409, "schedule_receipt_conflict", "The job has no matching committed batch receipt")
        if row.updated_at != req.expectedUpdatedAt:
            raise _RunFailure(409, "plan_changed", "Batch sync plan changed; review it before starting")
        # Replay above is deliberately source-free. Hold the registry fence until
        # this new attempt has settled, even if the remote source is slow.
        saved_source = await _saved_source_state(session, row.connection_id, lock=True)
        _require_active_source(saved_source)
        plan = _plan_response(row)
        incremental = row.mode == "incremental"
        ranged = row.range_min is not None
        if ranged:
            if not range_snapshot_enabled():
                raise _RunFailure(403, "range_snapshot_disabled", "Managed range snapshots are disabled")
            if not range_snapshot_scope(plan):
                raise _RunFailure(409, "range_scope_changed", "The bounded snapshot no longer matches the fixed fixture")
            await _require_range_target_local(session)
        elif plan.targetTable == BATCH_RANGE_TARGET or plan.sourceSchema == BATCH_RANGE_SOURCE_SCHEMA:
            raise _RunFailure(409, "range_scope_changed", "The reserved snapshot fixture requires reviewed exact bounds")
        if incremental:
            if not incremental_batch_enabled():
                raise _RunFailure(403, "incremental_disabled", "Managed incremental batch sync is disabled")
            if not managed_batch_scope(plan):
                raise _RunFailure(409, "incremental_scope_changed", "The plan no longer matches the fixed synthetic stream")
        elif plan.targetTable == INCREMENTAL_TARGET or plan.sourceSchema == INCREMENTAL_SOURCE_SCHEMA:
            raise _RunFailure(409, "incremental_scope_changed", "The managed stream and target require incremental mode")
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
        binding = None
        if ranged:
            require_target_shape(target, columns)
        if incremental:
            require_target_shape(target, columns)
            binding = await watermark(session, lock=True)
            if not same_binding(binding, row):
                raise _RunFailure(409, "incremental_binding_changed", "The fixed synthetic stream binding differs from its review")
        _validate_source_revision(saved_source, plan.sourceUpdatedAt)
        if job is not None:
            _check_job_time(job, scheduled_now)
        try:
            marker = await require_plan_source_identity(session, row)
        except HTTPException:
            raise _RunFailure(409, "source_identity_review_required",
                              "Verify the saved PostgreSQL source and review the batch plan again") from None
        source_columns = [item.sourceColumn for item in plan.mapping]
        outcome = "committed"
        interval = {}
        try:
            if binding is not None:
                batch = await read_committed_batch(
                    get_data_connections(), str(plan.connectionId), expected_updated_at=plan.sourceUpdatedAt,
                    identity_marker=marker, stream_digest=row.source_stream_digest, cursor=binding.applied_through,
                )
                start = binding.applied_through
                source_rows = batch.rows
                if batch.ordinal is None:
                    if batch.published_through != start or batch.digest is not None or source_rows:
                        raise SourceIdentityMismatch()
                    outcome = "no_new_data"
                    end = start
                else:
                    end = batch.ordinal
                    if (end != start + 1 or batch.published_through < end or not source_rows
                        or len(source_rows) > MAX_BATCH_ROWS or not re.fullmatch(r"[0-9a-f]{64}", batch.digest or "")):
                        raise SourceIdentityMismatch()
                interval = dict(from_exclusive=start, through_inclusive=end, source_high_water=batch.published_through,
                                events_read=len(source_rows), manifest_hash=batch.digest)
            elif ranged:
                if marker is None:
                    raise SourceIdentityMismatch()
                source_rows = await read_range_snapshot(
                    get_data_connections(), str(plan.connectionId), expected_updated_at=plan.sourceUpdatedAt, marker=marker,
                    pin=(row.source_oid, row.source_file_node, row.range_index_oid, row.range_index_node),
                    lower=row.range_min, upper=row.range_max,
                )
                interval = dict(range_min=row.range_min, range_max=row.range_max)
                if not source_rows:
                    outcome = "no_new_data"
            else:
                proof_args = {"identity_marker": marker, "relation_pin": (row.source_oid, row.source_file_node)} if marker else {}
                source_rows, source_meta = await read_batch_sync_snapshot(
                    get_data_connections(), str(plan.connectionId), expected_updated_at=plan.sourceUpdatedAt,
                    schema=plan.sourceSchema, table=plan.sourceTable, columns=source_columns, key_column=plan.sourceKeyColumn,
                    **proof_args,
                )
        except HTTPException as error:
            raise _source_failure(error) from None
        if binding is None and not ranged:
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
        if binding is not None and outcome == "committed":
            binding.applied_through = interval["through_inclusive"]
        saved = BatchSyncReceiptRow(
            plan_id=plan_id, request_id=req.requestId, initiated_by=effective_actor, connection_id=plan.connectionId,
            source_schema=plan.sourceSchema, source_table=plan.sourceTable, target_table=plan.targetTable,
            plan_updated_at=plan.updatedAt, source_updated_at=plan.sourceUpdatedAt,
            rows_affected=affected, recorded_at=datetime.now(timezone.utc),
            source_identity_verified=True if marker is not None else None, outcome=outcome, **interval,
        )
        session.add(saved)
        result = _receipt_response(saved)
        await session.flush()
        if await _finish_attempt(session, attempt_id, outcome, receipt_id=saved.id) != 1:
            raise _RunFailure(503, "execution_unavailable", "The batch attempt could not settle; retry with the same request ID",
                              "retryable")
        if job is not None:
            job.state = outcome
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
