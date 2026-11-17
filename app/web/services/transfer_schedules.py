"""Durable daily UTC slots for the existing scheduler's local Upsert pilot."""

from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import exists, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.storage.postgres_client import get_pg_client
from app.web.api.models.transfer_plans import TransferPlanRow, TransferRunRequest
from app.web.api.models.transfer_schedules import (
    TransferSchedule, TransferScheduleChange, TransferScheduleJob, TransferScheduleJobRow, TransferScheduleRow,
    TransferScheduleSummary,
)
from app.web.core.config import settings
from app.web.api.models.transfer_upstream import UpstreamTriggerRow
from app.web.services.transfer_runs import (
    HISTORY_LIMIT, SANDBOX, run_sandbox_transfer, scheduled_transfer_enabled,
)

SLOT_WINDOW = timedelta(minutes=5)
DISPATCH_LEASE = timedelta(seconds=45)
TICK_LIMIT = 100
OPEN_JOB_STATES = ("pending", "dispatched")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _eligible(plan: TransferPlanRow) -> bool:
    return (plan.trigger == "scheduled" and plan.schedule_time is not None and plan.write_mode == "upsert"
            and plan.source_schema == SANDBOX and plan.target_schema == SANDBOX and plan.incremental_cursor is None)


def _response(plan: TransferPlanRow, binding: TransferScheduleRow | None) -> TransferSchedule:
    available = scheduled_transfer_enabled() and _eligible(plan)
    return TransferSchedule(
        available=available,
        enabled=bool(available and binding and binding.enabled and binding.armed_for_updated_at == plan.updated_at),
        generation=binding.generation if binding else None,
        plannedTimeUtc=plan.schedule_time,
        armedForUpdatedAt=binding.armed_for_updated_at if binding else None,
        updatedAt=binding.updated_at if binding else None,
    )


async def read_transfer_schedule(session: AsyncSession, plan_id: UUID) -> TransferSchedule:
    plan = await session.get(TransferPlanRow, plan_id)
    if plan is None:
        raise HTTPException(404, "Transfer draft does not exist")
    return _response(plan, await session.get(TransferScheduleRow, plan_id))


async def change_transfer_schedule(
    session: AsyncSession, plan_id: UUID, change: TransferScheduleChange, actor_id: UUID,
) -> TransferSchedule:
    # Execution takes locks in the same order. Once Pause returns, queued jobs
    # cannot start; a transaction already holding the plan lock may finish first.
    await session.execute(text("SET LOCAL lock_timeout = '3s'"))
    try:
        plan = (await session.scalars(select(TransferPlanRow).where(TransferPlanRow.id == plan_id)
                                      .with_for_update().execution_options(populate_existing=True))).one_or_none()
    except DBAPIError as failure:
        if getattr(failure.orig, "sqlstate", None) == "55P03":
            raise HTTPException(409, "A transfer is already settling. Retry the schedule change.") from failure
        raise
    if plan is None:
        raise HTTPException(404, "Transfer draft does not exist")
    if plan.updated_at != change.expectedUpdatedAt:
        raise HTTPException(409, "The transfer draft changed. Reload it before changing the schedule")
    if change.enabled and not scheduled_transfer_enabled():
        raise HTTPException(403, "Automatic sandbox transfer execution is disabled")
    if change.enabled:
        upstream = await session.get(UpstreamTriggerRow, plan_id, populate_existing=True)
        if upstream is not None and upstream.enabled and upstream.mixed_time_utc is not None:
            raise HTTPException(409, "Pause the mixed upstream trigger before arming the independent Daily UTC schedule")
    if change.enabled and not _eligible(plan):
        raise HTTPException(422, "Only daily UTC Upsert plans in the dedicated local sandbox can be scheduled")

    binding = await session.get(TransferScheduleRow, plan_id)
    if binding is None and not change.enabled:
        return _response(plan, None)
    if binding is None:
        binding = TransferScheduleRow(
            plan_id=plan_id, generation=1, enabled=True, armed_by=actor_id,
            armed_for_updated_at=plan.updated_at, updated_at=_utc_now(),
        )
        session.add(binding)
    elif binding.enabled != change.enabled or (change.enabled and (
        binding.armed_for_updated_at != plan.updated_at or binding.armed_by != actor_id
    )):
        binding.generation += 1
        binding.enabled = change.enabled
        binding.armed_by = actor_id
        binding.armed_for_updated_at = plan.updated_at
        binding.updated_at = _utc_now()
    result = _response(plan, binding)
    await session.commit()
    return result


async def list_transfer_schedules(session: AsyncSession) -> list[TransferScheduleSummary]:
    statement = (select(TransferPlanRow, TransferScheduleRow)
                 .join(TransferScheduleRow, TransferScheduleRow.plan_id == TransferPlanRow.id)
                 .order_by(TransferScheduleRow.updated_at.desc(), TransferPlanRow.id).limit(TICK_LIMIT))
    return [TransferScheduleSummary(planId=plan.id, planName=plan.name, sourceTable=plan.source_table,
                                    targetTable=plan.target_table, **_response(plan, binding).model_dump())
            for plan, binding in (await session.execute(statement)).all()]


async def list_transfer_schedule_jobs(session: AsyncSession, plan_id: UUID) -> list[TransferScheduleJob]:
    if await session.get(TransferPlanRow, plan_id) is None:
        raise HTTPException(404, "Transfer draft does not exist")
    statement = select(TransferScheduleJobRow).where(TransferScheduleJobRow.plan_id == plan_id).order_by(
        TransferScheduleJobRow.slot.desc(), TransferScheduleJobRow.generation.desc(),
    ).limit(HISTORY_LIMIT)
    return [TransferScheduleJob(id=row.id, slot=row.slot, state=row.state, dispatchedAt=row.dispatched_at,
                                finishedAt=row.finished_at, receiptId=row.receipt_id, message=row.message)
            for row in (await session.scalars(statement)).all()]


async def collect_due_transfer_jobs(session: AsyncSession, *, now: datetime | None = None) -> list[UUID]:
    if not scheduled_transfer_enabled():
        return []
    now = (now or _utc_now()).astimezone(timezone.utc)
    await session.execute(text("SET LOCAL lock_timeout = '3s'"))
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    expired = (await session.scalars(select(TransferScheduleJobRow).where(
        TransferScheduleJobRow.state.in_(OPEN_JOB_STATES), TransferScheduleJobRow.expires_at <= now,
    ).order_by(TransferScheduleJobRow.expires_at).limit(TICK_LIMIT).with_for_update(skip_locked=True))).all()
    for job in expired:
        job.state = "expired"
        job.finished_at = now
        job.message = "UTC dispatch window closed before an outcome was confirmed"

    minute = now.replace(second=0, microsecond=0)
    for offset in range(5):
        slot = minute - timedelta(minutes=offset)
        matches = select(TransferScheduleRow).join(TransferPlanRow, TransferPlanRow.id == TransferScheduleRow.plan_id).where(
            TransferScheduleRow.enabled, TransferScheduleRow.armed_for_updated_at == TransferPlanRow.updated_at,
            TransferScheduleRow.updated_at <= slot,
            TransferPlanRow.trigger == "scheduled", TransferPlanRow.write_mode == "upsert",
            TransferPlanRow.source_schema == SANDBOX, TransferPlanRow.target_schema == SANDBOX,
            TransferPlanRow.schedule_time == slot.strftime("%H:%M"), TransferPlanRow.incremental_cursor.is_(None),
            ~exists().where(
                TransferScheduleJobRow.plan_id == TransferScheduleRow.plan_id,
                TransferScheduleJobRow.generation == TransferScheduleRow.generation,
                TransferScheduleJobRow.slot == slot,
            ),
        ).order_by(TransferScheduleRow.plan_id).limit(TICK_LIMIT)
        bindings = (await session.scalars(matches)).all()
        if bindings:
            await session.execute(insert(TransferScheduleJobRow).values([
                {
                    "id": uuid5(NAMESPACE_URL, f"asiral:transfer:{binding.plan_id}:{binding.generation}:{slot.isoformat()}"),
                    "plan_id": binding.plan_id, "generation": binding.generation, "slot": slot,
                    "expires_at": slot + SLOT_WINDOW, "dispatch_after": now,
                    "plan_updated_at": binding.armed_for_updated_at, "armed_by": binding.armed_by, "state": "pending",
                }
                for binding in bindings
            ]).on_conflict_do_nothing())

    jobs = (await session.scalars(select(TransferScheduleJobRow).where(
        TransferScheduleJobRow.state.in_(OPEN_JOB_STATES), TransferScheduleJobRow.expires_at > now,
        TransferScheduleJobRow.dispatch_after <= now,
    ).order_by(TransferScheduleJobRow.dispatch_after, TransferScheduleJobRow.id)
        .limit(TICK_LIMIT).with_for_update(skip_locked=True))).all()
    ids = []
    for job in jobs:
        job.dispatch_after = now + DISPATCH_LEASE
        ids.append(job.id)
    await session.commit()
    return ids


async def list_due_dispatch_jobs() -> list[UUID]:
    if not scheduled_transfer_enabled():
        return []
    async with get_pg_client().session() as session:
        return await collect_due_transfer_jobs(session)


async def record_transfer_job_dispatched(job_id: UUID) -> None:
    if not scheduled_transfer_enabled():
        return
    async with get_pg_client().session() as session:
        await session.execute(text("SET LOCAL lock_timeout = '3s'"))
        await session.execute(update(TransferScheduleJobRow).where(
            TransferScheduleJobRow.id == job_id, TransferScheduleJobRow.state == "pending",
        ).values(state="dispatched", dispatched_at=_utc_now()))


async def execute_scheduled_transfer_job(job_id: UUID, *, now: datetime | None = None) -> str:
    if not scheduled_transfer_enabled():
        return "disabled"
    async with get_pg_client().session() as session:
        return await execute_transfer_schedule_job(session, job_id, now=now)


async def execute_transfer_schedule_job(session: AsyncSession, job_id: UUID, *, now: datetime | None = None) -> str:
    if not scheduled_transfer_enabled():
        return "disabled"
    job = await session.get(TransferScheduleJobRow, job_id)
    if job is None:
        return "missing"
    if job.state not in OPEN_JOB_STATES:
        return job.state
    try:
        await run_sandbox_transfer(
            session, job.plan_id, TransferRunRequest(requestId=job.id, expectedUpdatedAt=job.plan_updated_at),
            job.armed_by, scheduled_job_id=job.id, scheduled_now=now,
        )
        return "committed"
    except HTTPException:
        updated = await session.get(TransferScheduleJobRow, job_id, populate_existing=True)
        return updated.state if updated else "missing"
