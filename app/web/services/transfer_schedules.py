"""Durable daily and weekly UTC slots for the existing scheduler's local Upsert pilot."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, exists, extract, func, or_, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.storage.postgres_client import get_pg_client
from app.web.api.models.transfer_plans import TransferAttemptRow, TransferPlanRow, TransferRunRequest, TransferRunRow
from app.web.api.models.transfer_schedules import (
    TransferSchedule, TransferScheduleChange, TransferScheduleJob, TransferScheduleJobCancel,
    TransferScheduleJobRow, TransferScheduleRow,
    TransferScheduleSummary,
)
from app.web.core.config import settings
from app.web.api.models.transfer_upstream import UpstreamTriggerRow
from app.web.api.models.user import User
from app.web.api.utils.models import StatusTypes
from app.web.services.transfer_runs import (
    HISTORY_LIMIT, SANDBOX, clock_slot_job_id, run_sandbox_transfer, scheduled_transfer_enabled, weekly_transfer_enabled,
)

SLOT_WINDOW = timedelta(minutes=5)
DISPATCH_LEASE = timedelta(seconds=45)
TICK_LIMIT = 100
OPEN_JOB_STATES = ("pending", "dispatched")
_CANCELED_MESSAGE = "Canceled. This UTC slot will not run again."


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _eligible(plan: TransferPlanRow) -> bool:
    return (plan.trigger in {"scheduled", "weekly"} and plan.schedule_time is not None and plan.write_mode == "upsert"
            and plan.source_schema == SANDBOX and plan.target_schema == SANDBOX and plan.incremental_cursor is None)


def transfer_job_cancel_enabled() -> bool:
    return settings.TRANSFER_SANDBOX_JOB_CANCEL_ENABLED and scheduled_transfer_enabled()


_job_identity = clock_slot_job_id


async def _cancel_now(session: AsyncSession) -> datetime:
    return (await session.scalar(text("SELECT pg_catalog.clock_timestamp()"))).astimezone(timezone.utc)


def _cancel_error(code: str, message: str) -> HTTPException:
    return HTTPException(409, {"code": code, "message": message})


def _current_job(plan, schedule, job, conflict: bool) -> bool:
    return bool(schedule is not None and schedule.enabled and not conflict and _eligible(plan)
                and plan.trigger == "scheduled" and job.schedule_weekday_utc is None
                and schedule.generation == job.generation and schedule.armed_by == job.armed_by
                and schedule.armed_for_updated_at == plan.updated_at == job.plan_updated_at
                and schedule.updated_at <= job.slot and job.expires_at == job.slot + SLOT_WINDOW
                and job.slot.second == job.slot.microsecond == 0
                and plan.schedule_time == job.slot.astimezone(timezone.utc).strftime("%H:%M")
                and job.id == _job_identity(plan.id, job.generation, job.slot))


async def _mixed_conflict(session: AsyncSession, plan_id: UUID) -> bool:
    upstream = await session.get(UpstreamTriggerRow, plan_id, populate_existing=True)
    return bool(upstream is not None and upstream.enabled and upstream.mixed_time_utc is not None)


async def _eligible_schedule_actor(session: AsyncSession, schedule: TransferScheduleRow | None) -> bool:
    return bool(schedule and await session.scalar(select(User.id).where(
        User.id == schedule.armed_by, User.status == StatusTypes.ENABLE, User.is_superuser.is_(True),
    )))


def _prior_attempts(plan_id: UUID, job_ids: list[UUID]):
    attempt = TransferAttemptRow
    return select(attempt.request_id, attempt.status).where(
        attempt.plan_id == plan_id, attempt.request_id.in_(job_ids),
    )


def _job_response(job: TransferScheduleJobRow, *, include_generation: bool = False, cancelable: bool = False) -> TransferScheduleJob:
    return TransferScheduleJob(
        id=job.id, slot=job.slot, state=job.state, dispatchedAt=job.dispatched_at,
        scheduleWeekdayUtc=job.schedule_weekday_utc,
        finishedAt=job.finished_at, receiptId=job.receipt_id, message=job.message,
        generation=job.generation if include_generation or job.state == "canceled" else None,
        canceledAt=job.canceled_at, cancelable=True if cancelable else None,
    )


def _response(plan: TransferPlanRow, binding: TransferScheduleRow | None) -> TransferSchedule:
    available = scheduled_transfer_enabled() and _eligible(plan) and (plan.trigger != "weekly" or weekly_transfer_enabled())
    return TransferSchedule(
        available=available,
        jobCancelAvailable=True if available and plan.trigger == "scheduled" and transfer_job_cancel_enabled() else None,
        enabled=bool((available or plan.trigger == "weekly") and binding and binding.enabled and binding.armed_for_updated_at == plan.updated_at),
        generation=binding.generation if binding else None,
        plannedTimeUtc=plan.schedule_time,
        plannedWeekdayUtc=plan.schedule_weekday_utc,
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
    if change.enabled and plan.trigger == "weekly" and not weekly_transfer_enabled():
        raise HTTPException(403, "Weekly UTC sandbox transfer execution is disabled")
    if change.enabled and not scheduled_transfer_enabled():
        raise HTTPException(403, "Automatic sandbox transfer execution is disabled")
    if change.enabled:
        upstream = await session.get(UpstreamTriggerRow, plan_id, populate_existing=True)
        if upstream is not None and upstream.enabled and upstream.mixed_time_utc is not None:
            raise HTTPException(409, "Pause the mixed upstream trigger before arming the independent UTC schedule")
    if change.enabled and not _eligible(plan):
        raise HTTPException(422, "Only daily or weekly UTC Upsert plans in the dedicated local sandbox can be scheduled")

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
    plan = await session.get(TransferPlanRow, plan_id)
    if plan is None:
        raise HTTPException(404, "Transfer draft does not exist")
    statement = select(TransferScheduleJobRow).where(TransferScheduleJobRow.plan_id == plan_id).order_by(
        TransferScheduleJobRow.slot.desc(), TransferScheduleJobRow.generation.desc(),
    ).limit(HISTORY_LIMIT)
    jobs = (await session.scalars(statement)).all()
    enabled = transfer_job_cancel_enabled()
    possible = set()
    if enabled and jobs and _eligible(plan) and plan.trigger == "scheduled":
        now = await _cancel_now(session)
        binding = await session.get(TransferScheduleRow, plan_id)
        conflict = await _mixed_conflict(session, plan_id)
        if await _eligible_schedule_actor(session, binding):
            possible = {job.id for job in jobs if job.state in OPEN_JOB_STATES and job.receipt_id is None
                        and job.finished_at is None and job.slot <= now < job.expires_at
                        and _current_job(plan, binding, job, conflict)}
        if possible:
            attempts = _prior_attempts(plan_id, sorted(possible)).with_only_columns(TransferAttemptRow.request_id).distinct()
            possible -= set((await session.scalars(attempts)).all())
        if possible:
            possible -= set((await session.scalars(select(TransferRunRow.request_id).where(
                TransferRunRow.plan_id == plan_id, TransferRunRow.request_id.in_(possible),
            ))).all())
    return [_job_response(job, include_generation=enabled and job.schedule_weekday_utc is None, cancelable=job.id in possible) for job in jobs]


async def cancel_transfer_schedule_job(
    session: AsyncSession, plan_id: UUID, job_id: UUID, change: TransferScheduleJobCancel, actor_id: UUID,
) -> TransferScheduleJob:
    if not transfer_job_cancel_enabled():
        raise HTTPException(403, "Canceling a Daily UTC job requires explicit local opt-in")
    committing = False
    try:
        await session.execute(text("SET LOCAL lock_timeout = '100ms'"))
        await session.execute(text("SET LOCAL statement_timeout = '2s'"))
        # Same order as both scheduled worker admission and actual execution.
        plan = await session.scalar(select(TransferPlanRow).where(TransferPlanRow.id == plan_id)
                                    .with_for_update(nowait=True).execution_options(populate_existing=True))
        if plan is None:
            raise HTTPException(404, "The exact Daily UTC job does not exist")
        job = await session.scalar(select(TransferScheduleJobRow).where(
            TransferScheduleJobRow.id == job_id, TransferScheduleJobRow.plan_id == plan_id,
        ).with_for_update(nowait=True).execution_options(populate_existing=True))
        if job is None or job.schedule_weekday_utc is not None:
            raise HTTPException(404, "The exact Daily UTC job does not exist")
        if job.generation != change.expectedGeneration or job.slot != change.expectedSlot:
            raise _cancel_error("job_scope_changed", "The exact reviewed generation or UTC slot does not match")
        if job.state == "canceled":
            result = _job_response(job, include_generation=True)
            await session.rollback()
            return result
        if job.state not in OPEN_JOB_STATES or job.receipt_id is not None or job.finished_at is not None:
            raise _cancel_error("job_terminal", "This Daily UTC job already has an outcome and cannot be canceled")
        binding = await session.get(TransferScheduleRow, plan_id, populate_existing=True)
        if (not _current_job(plan, binding, job, await _mixed_conflict(session, plan_id))
            or not await _eligible_schedule_actor(session, binding)):
            raise _cancel_error("job_stale", "The reviewed Daily UTC job is no longer current")
        if await session.scalar(select(TransferRunRow.id).where(
            TransferRunRow.plan_id == plan_id, TransferRunRow.request_id == job_id,
        ).limit(1)):
            raise _cancel_error("job_terminal", "A transfer receipt already exists for this job")
        attempt = TransferAttemptRow
        if await session.scalar(_prior_attempts(plan_id, [job_id]).where(attempt.status != "started").limit(1)):
            raise _cancel_error("job_terminal", "This job already has a previous execution attempt")
        if await session.scalar(_prior_attempts(plan_id, [job_id]).where(attempt.status == "started").limit(1)):
            raise _cancel_error("job_busy", "This job is already settling and was not canceled")
        now = await _cancel_now(session)
        if not job.slot <= now < job.expires_at:
            raise _cancel_error("job_stale", "The UTC dispatch window is not open for this job")
        job.state = "canceled"
        job.canceled_by = actor_id
        job.canceled_at = job.finished_at = now
        job.message = _CANCELED_MESSAGE
        result = _job_response(job, include_generation=True)
        committing = True
        await session.commit()
        return result
    except (DBAPIError, OSError, TimeoutError) as failure:
        await session.rollback()
        if committing:
            raise HTTPException(503, {"code": "cancel_outcome_unknown",
                                      "message": "Cancellation was not confirmed; check or retry this exact job"}) from None
        if getattr(getattr(failure, "orig", None), "sqlstate", None) in {"55P03", "40P01", "57014"}:
            raise _cancel_error("job_busy", "This job or its plan is settling and was not canceled") from None
        raise
    except HTTPException:
        await session.rollback()
        raise


def _active_actor(binding: type[TransferScheduleRow]):
    return exists().where(User.id == binding.armed_by, User.status == StatusTypes.ENABLE, User.is_superuser.is_(True))


def _no_mixed_binding(plan_id):
    return ~exists().where(UpstreamTriggerRow.plan_id == plan_id, UpstreamTriggerRow.enabled,
                           UpstreamTriggerRow.mixed_time_utc.is_not(None))


def _weekly_lease_scope(now: datetime):
    job, plan, binding = TransferScheduleJobRow, TransferPlanRow, TransferScheduleRow
    utc_slot = func.timezone("UTC", job.slot)
    return exists(select(plan.id).join(binding, binding.plan_id == plan.id).where(
        plan.id == job.plan_id, plan.trigger == "weekly", plan.write_mode == "upsert",
        plan.source_schema == SANDBOX, plan.target_schema == SANDBOX, plan.incremental_cursor.is_(None),
        plan.schedule_weekday_utc == job.schedule_weekday_utc,
        job.schedule_weekday_utc == extract("isodow", utc_slot),
        plan.schedule_time == func.to_char(utc_slot, "HH24:MI"), extract("second", utc_slot) == 0,
        job.slot <= now, job.expires_at == job.slot + text("interval '5 minutes'"),
        plan.updated_at == job.plan_updated_at, binding.armed_for_updated_at == plan.updated_at,
        binding.enabled, binding.generation == job.generation, binding.armed_by == job.armed_by,
        binding.updated_at <= job.slot, _active_actor(binding), _no_mixed_binding(plan.id),
    )).correlate(job)


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
    weekly = weekly_transfer_enabled()
    for offset in range(5):
        slot = minute - timedelta(minutes=offset)
        daily_mode = TransferPlanRow.trigger == "scheduled"
        weekly_mode = and_(TransferPlanRow.trigger == "weekly", TransferPlanRow.schedule_weekday_utc == slot.isoweekday(),
                           _active_actor(TransferScheduleRow), _no_mixed_binding(TransferPlanRow.id))
        matches = select(TransferScheduleRow, TransferPlanRow.schedule_weekday_utc).join(
            TransferPlanRow, TransferPlanRow.id == TransferScheduleRow.plan_id,
        ).where(
            TransferScheduleRow.enabled, TransferScheduleRow.armed_for_updated_at == TransferPlanRow.updated_at,
            TransferScheduleRow.updated_at <= slot,
            or_(daily_mode, weekly_mode) if weekly else daily_mode, TransferPlanRow.write_mode == "upsert",
            TransferPlanRow.source_schema == SANDBOX, TransferPlanRow.target_schema == SANDBOX,
            TransferPlanRow.schedule_time == slot.strftime("%H:%M"), TransferPlanRow.incremental_cursor.is_(None),
            ~exists().where(
                TransferScheduleJobRow.plan_id == TransferScheduleRow.plan_id,
                TransferScheduleJobRow.generation == TransferScheduleRow.generation,
                TransferScheduleJobRow.slot == slot,
            ),
        ).order_by(TransferScheduleRow.plan_id).limit(TICK_LIMIT)
        bindings = (await session.execute(matches)).all()
        if bindings:
            await session.execute(insert(TransferScheduleJobRow).values([
                {
                    "id": _job_identity(binding.plan_id, binding.generation, slot),
                    "plan_id": binding.plan_id, "generation": binding.generation, "slot": slot,
                    "expires_at": slot + SLOT_WINDOW, "dispatch_after": now,
                    "plan_updated_at": binding.armed_for_updated_at, "armed_by": binding.armed_by, "state": "pending",
                    "schedule_weekday_utc": weekday,
                }
                for binding, weekday in bindings
            ]).on_conflict_do_nothing())

    jobs = (await session.scalars(select(TransferScheduleJobRow).where(
        TransferScheduleJobRow.state.in_(OPEN_JOB_STATES), TransferScheduleJobRow.expires_at > now,
        TransferScheduleJobRow.dispatch_after <= now,
        or_(TransferScheduleJobRow.schedule_weekday_utc.is_(None), _weekly_lease_scope(now))
        if weekly else TransferScheduleJobRow.schedule_weekday_utc.is_(None),
    ).order_by(TransferScheduleJobRow.dispatch_after, TransferScheduleJobRow.id)
        .limit(TICK_LIMIT).with_for_update(skip_locked=True))).all()
    ids = []
    for job in jobs:
        if job.schedule_weekday_utc is not None and job.id != _job_identity(job.plan_id, job.generation, job.slot):
            continue
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
            True if weekly_transfer_enabled() else TransferScheduleJobRow.schedule_weekday_utc.is_(None),
        ).values(state="dispatched", dispatched_at=_utc_now()))


async def execute_scheduled_transfer_job(job_id: UUID, *, now: datetime | None = None) -> str:
    if not scheduled_transfer_enabled():
        return "disabled"
    async with get_pg_client().session() as session:
        return await execute_transfer_schedule_job(session, job_id, now=now)


async def execute_transfer_schedule_job(session: AsyncSession, job_id: UUID, *, now: datetime | None = None) -> str:
    if not scheduled_transfer_enabled():
        return "disabled"
    job = await session.get(TransferScheduleJobRow, job_id, populate_existing=True)
    if job is None:
        return "missing"
    if job.state not in OPEN_JOB_STATES:
        return job.state
    if job.schedule_weekday_utc is not None and not weekly_transfer_enabled():
        return "disabled"
    try:
        await run_sandbox_transfer(
            session, job.plan_id, TransferRunRequest(requestId=job.id, expectedUpdatedAt=job.plan_updated_at),
            job.armed_by, scheduled_job_id=job.id, scheduled_now=now,
        )
        return "committed"
    except HTTPException:
        updated = await session.get(TransferScheduleJobRow, job_id, populate_existing=True)
        return updated.state if updated else "missing"
