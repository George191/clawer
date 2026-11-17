"""UTC daily jobs for the local PostgreSQL batch pilot, on the existing scheduler."""

from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import DateTime, Uuid, case, column, exists, literal_column, table, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.storage.postgres_client import get_pg_client
from app.web.api.models.batch_syncs import BatchSyncPlanRow, BatchSyncRunRequest
from app.web.api.models.batch_sync_schedules import (
    BatchSyncSchedule, BatchSyncScheduleChange, BatchSyncScheduleJob, BatchSyncScheduleJobRow,
    BatchSyncScheduleRow, BatchSyncScheduleSummary,
)
from app.web.services.batch_source_identity import plan_source_ready_clause, require_plan_source_identity
from app.web.services.batch_sync_incremental import incremental_ready_clause
from app.web.services.batch_syncs import _HISTORY_LIMIT, _target_metadata, _verify_source_revision, run_batch_sync, scheduled_batch_sync_enabled
from app.web.services.batch_sync_scope import local_batch_sync_scope
from app.web.services.data_connections import paused_at_sql

SLOT_WINDOW = timedelta(minutes=5)
DISPATCH_LEASE = timedelta(seconds=45)
TICK_LIMIT = 100
OPEN_JOB_STATES = ("pending", "dispatched")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


_REGISTRY = table("ontology_data_connections", column("id", Uuid()), column("updated_at", DateTime(timezone=True)),
                  schema="public").alias("saved_batch_source")


_SOURCE_PAUSED_AT = literal_column(paused_at_sql("saved_batch_source"), type_=DateTime(timezone=True))


async def _source_state(session: AsyncSession, identity: UUID) -> tuple[datetime | None, bool]:
    row = (await session.execute(select(_REGISTRY.c.updated_at, _SOURCE_PAUSED_AT)
                                 .where(_REGISTRY.c.id == identity))).one_or_none()
    return (row[0], row[1] is not None) if row is not None else (None, False)


def _response(plan: BatchSyncPlanRow, binding: BatchSyncScheduleRow | None,
              source_version: datetime | None, identity_ready: bool, source_paused: bool) -> BatchSyncSchedule:
    available = (scheduled_batch_sync_enabled() and not source_paused
                 and source_version == plan.source_updated_at and identity_ready)
    return BatchSyncSchedule(
        available=available,
        sourcePaused=True if source_paused else None,
        configuredEnabled=bool(binding and binding.enabled),
        enabled=bool(available and binding and binding.enabled and binding.armed_for_updated_at == plan.updated_at
                     and binding.source_updated_at == plan.source_updated_at
                     and binding.execution_scope == local_batch_sync_scope()),
        generation=binding.generation if binding else None,
        plannedTimeUtc=binding.planned_time_utc if binding else None,
        armedForUpdatedAt=binding.armed_for_updated_at if binding else None,
        armedForSourceUpdatedAt=binding.source_updated_at if binding else None,
        updatedAt=binding.updated_at if binding else None,
    )


async def read_batch_sync_schedule(session: AsyncSession, plan_id: UUID) -> BatchSyncSchedule:
    plan = await session.get(BatchSyncPlanRow, plan_id)
    if plan is None:
        raise HTTPException(404, "Batch sync plan does not exist")
    source_version, source_paused = await _source_state(session, plan.connection_id)
    identity_ready = await _source_identity_ready(session, plan_id)
    return _response(plan, await session.get(BatchSyncScheduleRow, plan_id), source_version, identity_ready, source_paused)


async def change_batch_sync_schedule(
    session: AsyncSession, plan_id: UUID, change: BatchSyncScheduleChange, actor_id: UUID,
) -> BatchSyncSchedule:
    # Execution takes locks in the same order. Once Pause returns, queued jobs
    # cannot start; a transaction already holding the plan lock may finish first.
    await session.execute(text("SET LOCAL lock_timeout = '3s'"))
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    try:
        plan = (await session.scalars(select(BatchSyncPlanRow).where(BatchSyncPlanRow.id == plan_id)
                                      .with_for_update().execution_options(populate_existing=True))).one_or_none()
    except DBAPIError as failure:
        if getattr(failure.orig, "sqlstate", None) == "55P03":
            raise HTTPException(409, "A batch is already settling. Retry the schedule change.") from failure
        raise
    if plan is None:
        raise HTTPException(404, "Batch sync plan does not exist")
    if plan.updated_at != change.expectedUpdatedAt:
        raise HTTPException(409, "The batch sync plan changed. Reload it before changing the schedule")
    execution_scope = local_batch_sync_scope()
    if change.enabled and (not scheduled_batch_sync_enabled() or execution_scope is None):
        raise HTTPException(403, "Automatic sandbox batch execution is disabled")
    if change.enabled:
        await _verify_source_revision(session, plan.connection_id, plan.source_updated_at, lock=True)
        await require_plan_source_identity(session, plan)
        if not await _source_identity_ready(session, plan.id):
            raise HTTPException(409, "Review the exact synthetic stream binding before arming this plan")
        target, _ = await _target_metadata(session, plan.target_table)
        if target["table_oid"] != plan.target_oid or target["table_node"] != plan.target_file_node:
            raise HTTPException(409, "The batch target was replaced or rewritten; review and save the plan again")

    binding = await session.get(BatchSyncScheduleRow, plan_id)
    if change.expectedGeneration != (binding.generation if binding else None):
        raise HTTPException(409, "The UTC schedule changed. Reload it before making changes")
    if binding is None and not change.enabled:
        return await read_batch_sync_schedule(session, plan_id)
    if binding is None:
        binding = BatchSyncScheduleRow(
            plan_id=plan_id, generation=1, enabled=True, armed_by=actor_id, execution_scope=execution_scope,
            armed_for_updated_at=plan.updated_at, planned_time_utc=change.plannedTimeUtc,
            source_updated_at=plan.source_updated_at, updated_at=_utc_now(),
        )
        session.add(binding)
    elif binding.enabled != change.enabled or (change.enabled and (
        binding.armed_for_updated_at != plan.updated_at or binding.armed_by != actor_id
        or binding.planned_time_utc != change.plannedTimeUtc or binding.source_updated_at != plan.source_updated_at
        or binding.execution_scope != execution_scope
    )):
        binding.generation += 1
        binding.enabled = change.enabled
        if change.enabled:
            binding.armed_by = actor_id
            binding.execution_scope = execution_scope
            binding.armed_for_updated_at = plan.updated_at
            binding.source_updated_at = plan.source_updated_at
            binding.planned_time_utc = change.plannedTimeUtc
        binding.updated_at = _utc_now()
    source_version, source_paused = await _source_state(session, plan.connection_id)
    result = _response(plan, binding, source_version, await _source_identity_ready(session, plan.id), source_paused)
    await session.commit()
    return result


async def _source_identity_ready(session: AsyncSession, plan_id: UUID) -> bool:
    return bool(await session.scalar(select(BatchSyncPlanRow.id).where(
        BatchSyncPlanRow.id == plan_id, plan_source_ready_clause(), incremental_ready_clause(),
    )))


async def list_batch_sync_schedules(session: AsyncSession) -> list[BatchSyncScheduleSummary]:
    statement = (select(BatchSyncPlanRow, BatchSyncScheduleRow, _REGISTRY.c.updated_at, _SOURCE_PAUSED_AT,
                      plan_source_ready_clause() & incremental_ready_clause())
                 .join(BatchSyncScheduleRow, BatchSyncScheduleRow.plan_id == BatchSyncPlanRow.id)
                 .outerjoin(_REGISTRY, _REGISTRY.c.id == BatchSyncPlanRow.connection_id)
                 .order_by(BatchSyncScheduleRow.updated_at.desc(), BatchSyncPlanRow.id).limit(TICK_LIMIT))
    return [BatchSyncScheduleSummary(planId=plan.id, connectionId=plan.connection_id, planName=plan.name, sourceTable=plan.source_table,
                                    targetTable=plan.target_table, **_response(plan, binding, source_version, identity_ready, paused_at is not None).model_dump())
            for plan, binding, source_version, paused_at, identity_ready in (await session.execute(statement)).all()]


async def list_batch_sync_schedule_jobs(session: AsyncSession, plan_id: UUID) -> list[BatchSyncScheduleJob]:
    if await session.get(BatchSyncPlanRow, plan_id) is None:
        raise HTTPException(404, "Batch sync plan does not exist")
    statement = select(BatchSyncScheduleJobRow).where(BatchSyncScheduleJobRow.plan_id == plan_id).order_by(
        BatchSyncScheduleJobRow.slot.desc(), BatchSyncScheduleJobRow.generation.desc(),
    ).limit(_HISTORY_LIMIT)
    return [BatchSyncScheduleJob(id=row.id, slot=row.slot, state=row.state, dispatchedAt=row.dispatched_at,
                                finishedAt=row.finished_at, receiptId=row.receipt_id, message=row.message)
            for row in (await session.scalars(statement)).all()]


async def collect_due_batch_sync_jobs(session: AsyncSession, *, now: datetime | None = None) -> list[UUID]:
    if not scheduled_batch_sync_enabled():
        return []
    now = (now or _utc_now()).astimezone(timezone.utc)
    await session.execute(text("SET LOCAL lock_timeout = '3s'"))
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    expired = (await session.scalars(select(BatchSyncScheduleJobRow).where(
        BatchSyncScheduleJobRow.state.in_(OPEN_JOB_STATES), BatchSyncScheduleJobRow.expires_at <= now,
    ).order_by(BatchSyncScheduleJobRow.expires_at).limit(TICK_LIMIT).with_for_update(skip_locked=True))).all()
    for job in expired:
        job.state = "expired"
        job.finished_at = now
        job.message = "UTC dispatch window closed before an outcome was confirmed"

    minute = now.replace(second=0, microsecond=0)
    for offset in range(5):
        slot = minute - timedelta(minutes=offset)
        matches = (select(BatchSyncScheduleRow).join(BatchSyncPlanRow, BatchSyncPlanRow.id == BatchSyncScheduleRow.plan_id)
                   .join(_REGISTRY, _REGISTRY.c.id == BatchSyncPlanRow.connection_id).where(
            BatchSyncScheduleRow.enabled, BatchSyncScheduleRow.armed_for_updated_at == BatchSyncPlanRow.updated_at,
            BatchSyncScheduleRow.updated_at <= slot,
            BatchSyncScheduleRow.source_updated_at == BatchSyncPlanRow.source_updated_at,
            _REGISTRY.c.updated_at == BatchSyncScheduleRow.source_updated_at, _SOURCE_PAUSED_AT.is_(None),
            plan_source_ready_clause(), incremental_ready_clause(),
            BatchSyncScheduleRow.planned_time_utc == slot.strftime("%H:%M"),
            ~exists().where(
                BatchSyncScheduleJobRow.plan_id == BatchSyncScheduleRow.plan_id,
                BatchSyncScheduleJobRow.generation == BatchSyncScheduleRow.generation,
                BatchSyncScheduleJobRow.slot == slot,
            ),
        ).order_by(BatchSyncScheduleRow.plan_id).limit(TICK_LIMIT))
        bindings = (await session.scalars(matches)).all()
        if bindings:
            await session.execute(insert(BatchSyncScheduleJobRow).values([
                {
                    "id": uuid5(NAMESPACE_URL, f"asiral:batch-sync:{binding.plan_id}:{binding.generation}:{slot.isoformat()}"),
                    "plan_id": binding.plan_id, "generation": binding.generation, "slot": slot,
                    "expires_at": slot + SLOT_WINDOW, "dispatch_after": now,
                    "plan_updated_at": binding.armed_for_updated_at, "source_updated_at": binding.source_updated_at, "armed_by": binding.armed_by, "state": "pending",
                }
                for binding in bindings
            ]).on_conflict_do_nothing())

    job, plan, schedule = BatchSyncScheduleJobRow, BatchSyncPlanRow, BatchSyncScheduleRow
    jobs = (await session.scalars(select(job)
        .join(plan, plan.id == job.plan_id)
        .join(schedule, schedule.plan_id == plan.id)
        .join(_REGISTRY, _REGISTRY.c.id == plan.connection_id)
        .where(
            job.state.in_(OPEN_JOB_STATES), job.expires_at > now, job.dispatch_after <= now,
            schedule.enabled, schedule.generation == job.generation,
            schedule.armed_for_updated_at == job.plan_updated_at, plan.updated_at == job.plan_updated_at,
            schedule.source_updated_at == job.source_updated_at, plan.source_updated_at == job.source_updated_at,
            _REGISTRY.c.updated_at == job.source_updated_at, _SOURCE_PAUSED_AT.is_(None),
            plan_source_ready_clause(), incremental_ready_clause(),
        ).order_by(job.dispatch_after, job.id).limit(TICK_LIMIT).with_for_update(of=job, skip_locked=True))).all()
    ids = []
    for job in jobs:
        job.dispatch_after = now + DISPATCH_LEASE
        ids.append(job.id)
    await session.commit()
    return ids


async def list_due_batch_sync_jobs() -> list[UUID]:
    if not scheduled_batch_sync_enabled():
        return []
    async with get_pg_client().session() as session:
        return await collect_due_batch_sync_jobs(session)


async def record_batch_sync_job_dispatched(job_id: UUID) -> None:
    if not scheduled_batch_sync_enabled():
        return
    async with get_pg_client().session() as session:
        await session.execute(text("SET LOCAL lock_timeout = '3s'"))
        await session.execute(update(BatchSyncScheduleJobRow).where(
            BatchSyncScheduleJobRow.id == job_id, BatchSyncScheduleJobRow.dispatched_at.is_(None),
        ).values(state=case((BatchSyncScheduleJobRow.state == "pending", "dispatched"),
                            else_=BatchSyncScheduleJobRow.state), dispatched_at=_utc_now()))


async def execute_scheduled_batch_sync_job(job_id: UUID, *, now: datetime | None = None) -> str:
    if not scheduled_batch_sync_enabled():
        return "disabled"
    async with get_pg_client().session() as session:
        return await execute_batch_sync_schedule_job(session, job_id, now=now)


async def execute_batch_sync_schedule_job(session: AsyncSession, job_id: UUID, *, now: datetime | None = None) -> str:
    if not scheduled_batch_sync_enabled():
        return "disabled"
    job = await session.get(BatchSyncScheduleJobRow, job_id)
    if job is None:
        return "missing"
    if job.state not in OPEN_JOB_STATES:
        return job.state
    try:
        result = await run_batch_sync(
            session, job.plan_id, BatchSyncRunRequest(requestId=job.id, expectedUpdatedAt=job.plan_updated_at),
            job.armed_by, scheduled_job_id=job.id, scheduled_now=now,
        )
        return result.status
    except HTTPException:
        updated = await session.get(BatchSyncScheduleJobRow, job_id, populate_existing=True)
        return updated.state if updated else "missing"
