"""Observe exact Daily UTC transfer results on the existing sandbox quality tick."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.asset_freshness import AssetFreshnessPolicyRow, AssetQualityIncident, AssetQualityIncidentRow
from app.web.api.models.asset_job_quality import (
    AssetJobObservation, AssetJobQualityReview, AssetJobQualityRow, AssetJobQualityStatus, JobQualityBinding,
    JobQualityCandidate, TransferTargetRejectionRow,
)
from app.web.api.models.transfer_plans import TransferAttemptRow, TransferPlanRow, TransferRunRow
from app.web.api.models.transfer_schedules import TransferScheduleJobRow, TransferScheduleRow
from app.web.core.config import settings
from app.web.services.asset_collected_stats import _fence_current, _visible
from app.web.services.asset_quality_incidents import (
    acknowledge_quality_incident_locked, list_quality_incidents, observe_quality_incident,
)
from app.web.services.transfer_runs import SANDBOX, SCHEDULED_SYSTEM_ACTOR, scheduled_transfer_enabled

_TTL = timedelta(minutes=3)
_KIND = "daily_job"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def job_quality_enabled() -> bool:
    from app.web.services.asset_freshness import asset_freshness_enabled
    return (settings.TRANSFER_SANDBOX_JOB_QUALITY_ENABLED and asset_freshness_enabled() and scheduled_transfer_enabled())


def _require_schema(schema: str) -> None:
    if schema != SANDBOX:
        raise HTTPException(404, "Daily transfer checks are available only for reviewed sandbox targets")


async def _context(session: AsyncSession, policy: AssetFreshnessPolicyRow, identity: tuple | None, *, lock: bool = False):
    if not policy.enabled or identity != (policy.target_oid, policy.target_filenode):
        return None, None
    statement = select(TransferPlanRow).where(TransferPlanRow.id == policy.plan_id)
    plan = await session.scalar(statement.with_for_update(read=True, nowait=True) if lock else statement)
    if (plan is None or plan.updated_at != policy.plan_updated_at or plan.source_schema != SANDBOX
        or plan.target_schema != SANDBOX or plan.target_table != policy.target_table
        or plan.trigger != "scheduled" or plan.write_mode != "upsert" or plan.incremental_cursor is not None
        or plan.schedule_time is None):
        return None, None
    statement = select(TransferScheduleRow).where(TransferScheduleRow.plan_id == plan.id)
    schedule = await session.scalar(statement.with_for_update(read=True, nowait=True) if lock else statement)
    if schedule is None or not schedule.enabled or schedule.armed_for_updated_at != plan.updated_at:
        return None, None
    return plan, schedule


def _candidate(policy: AssetFreshnessPolicyRow, plan: TransferPlanRow | None, schedule: TransferScheduleRow | None):
    if not plan or not schedule:
        return None
    return JobQualityCandidate(planId=plan.id, planUpdatedAt=plan.updated_at, freshnessGeneration=policy.generation,
                               scheduleGeneration=schedule.generation, plannedTimeUtc=plan.schedule_time)


def _binding(row: AssetJobQualityRow) -> JobQualityBinding:
    return JobQualityBinding(planId=row.plan_id, planUpdatedAt=row.plan_updated_at, freshnessGeneration=row.freshness_generation,
                             scheduleGeneration=row.schedule_generation, plannedTimeUtc=row.planned_time_utc,
                             generation=row.generation, reviewedAt=row.reviewed_at)


def _drift(row, policy, identity, plan, schedule) -> str | None:
    if (not policy.enabled or policy.generation != row.freshness_generation or policy.plan_id != row.plan_id
        or policy.plan_updated_at != row.plan_updated_at):
        return "policy_changed"
    if identity != (row.target_oid, row.target_filenode):
        return "target_changed"
    if (plan is None or schedule is None or schedule.generation != row.schedule_generation
        or schedule.armed_by != row.armed_by or plan.schedule_time != row.planned_time_utc):
        return "schedule_changed"
    return None


def _expected_slot(now: datetime, clock: str) -> datetime:
    utc = now.astimezone(timezone.utc)
    hours, minutes = map(int, clock.split(":"))
    latest = utc.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    return latest if latest <= utc else latest - timedelta(days=1)


async def _supersede(session: AsyncSession, row: AssetJobQualityRow, now: datetime, *, cause: str) -> None:
    await session.execute(update(AssetQualityIncidentRow).where(
        AssetQualityIncidentRow.target_table == row.target_table, AssetQualityIncidentRow.generation == row.freshness_generation,
        AssetQualityIncidentRow.kind == _KIND, AssetQualityIncidentRow.closed_at.is_(None),
    ).values(closed_at=now, close_reason=cause))


async def _read_context(session: AsyncSession, table: str):
    from app.web.services.asset_freshness import _identity
    identity = await _identity(session, table)
    return identity, await session.get(AssetFreshnessPolicyRow, table)


async def read_job_quality(session: AsyncSession, schema: str, table: str) -> AssetJobQualityStatus:
    _require_schema(schema)
    if not job_quality_enabled():
        return AssetJobQualityStatus(available=False, state="disabled", reason="checking_disabled")
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL statement_timeout='1500ms'"))
    await session.execute(text("SET LOCAL lock_timeout='750ms'"))
    if not await _visible(session, table):
        return AssetJobQualityStatus(available=False, state="disabled", reason="target_unavailable")
    identity, policy = await _read_context(session, table)
    if policy is None:
        return AssetJobQualityStatus(available=identity is not None, state="not_configured",
                                     reason="target_unavailable" if identity is None else "no_eligible_schedule")
    plan, schedule = await _context(session, policy, identity)
    candidate = _candidate(policy, plan, schedule)
    row = await session.get(AssetJobQualityRow, table)
    if row is None:
        return AssetJobQualityStatus(available=True, state="not_configured", candidate=candidate,
                                     reason="no_eligible_schedule" if candidate is None else None)
    incidents = await list_quality_incidents(session, policy, daily_only=True)
    stale_scope = _drift(row, policy, identity, plan, schedule) or (row.reason if row.state == "superseded" else None)
    if stale_scope is None and not await _fence_current(session, policy):
        stale_scope, candidate = "target_changed", None
    info = dict(available=True, candidate=candidate, binding=_binding(row), incidents=incidents, checkedAt=row.checked_at)
    if stale_scope:
        incidents.active = []
        return AssetJobQualityStatus(**info, state="superseded", reason=stale_scope)
    if row.checked_at is None:
        return AssetJobQualityStatus(**info, state="not_checked")
    valid = min(row.checked_at + _TTL, _expected_slot(row.checked_at, row.planned_time_utc) + timedelta(days=1))
    if not row.checked_at <= _utc_now() < valid:
        return AssetJobQualityStatus(**info, state="unknown", reason="evaluation_stale")
    return AssetJobQualityStatus(
        **info, state=row.state, reason=row.reason, validUntil=valid,
        observation=(AssetJobObservation(slot=row.observed_slot, state=row.job_state, finishedAt=row.finished_at,
                                         receiptId=row.receipt_id, rowsAffected=row.rows_affected)
                     if row.observed_slot else None),
    )


async def review_job_quality(
    session: AsyncSession, schema: str, table: str, request: AssetJobQualityReview,
) -> AssetJobQualityStatus:
    from app.web.services.asset_freshness import _identity, _lock_policy
    _require_schema(schema)
    if not job_quality_enabled():
        raise HTTPException(403, "Daily transfer outcome checks are disabled")
    await session.execute(text("SET LOCAL statement_timeout='2500ms'"))
    await session.execute(text("SET LOCAL lock_timeout='750ms'"))
    if not await _visible(session, table):
        raise HTTPException(404, "The exact sandbox target is not eligible")
    policy = await _lock_policy(session, table)
    if policy is None:
        raise HTTPException(409, "Review the exact asset freshness policy first")
    row = await session.get(AssetJobQualityRow, table)
    if request.expectedGeneration != (row.generation if row else None):
        raise HTTPException(409, "The Daily UTC review changed; reload before continuing")
    identity = await _identity(session, table)
    plan, schedule = await _context(session, policy, identity, lock=True)
    if ((candidate := _candidate(policy, plan, schedule)) is None or not await _fence_current(session, policy)
        or (candidate.planId, candidate.planUpdatedAt, candidate.freshnessGeneration, candidate.scheduleGeneration) != (
            request.planId, request.expectedPlanUpdatedAt, request.expectedFreshnessGeneration, request.expectedScheduleGeneration,
        )):
        raise HTTPException(409, "Review the current eligible Daily UTC schedule and freshness policy")
    if row is not None and row.state != "superseded" and not _drift(row, policy, identity, plan, schedule):
        await session.commit()
        return await read_job_quality(session, schema, table)
    now = _utc_now()
    if row is not None:
        await _supersede(session, row, now, cause="schedule_changed")
    updates = dict(
        generation=row.generation + 1 if row else 1, freshness_generation=policy.generation,
        schedule_generation=schedule.generation, plan_id=plan.id, plan_updated_at=plan.updated_at,
        armed_by=schedule.armed_by, target_oid=identity[0], target_filenode=identity[1], planned_time_utc=plan.schedule_time,
        reviewed_at=now, checked_at=None, state="not_checked", reason=None, observed_slot=None, job_state=None,
        finished_at=None, receipt_id=None, rows_affected=None, confirmed_slot=None,
    )
    if row is None:
        session.add(AssetJobQualityRow(target_table=table, **updates))
    else:
        for field, value in updates.items():
            setattr(row, field, value)
    await session.commit()
    return await read_job_quality(session, schema, table)


async def acknowledge_daily_job(
    session: AsyncSession, schema: str, table: str, incident: UUID, generation: int, actor: UUID,
) -> AssetQualityIncident:
    from app.web.services.asset_freshness import _identity, _lock_policy
    _require_schema(schema)
    if not job_quality_enabled():
        raise HTTPException(403, "Daily transfer outcome checks are disabled")
    await session.execute(text("SET LOCAL statement_timeout='2500ms'"))
    await session.execute(text("SET LOCAL lock_timeout='750ms'"))
    if not await _visible(session, table):
        raise HTTPException(404, "The exact sandbox target is not eligible")
    policy = await _lock_policy(session, table)
    row = await session.get(AssetJobQualityRow, table)
    if policy is None or row is None or row.generation != generation or row.state == "superseded":
        raise HTTPException(409, "The Daily UTC review changed; reload the incident")
    identity = await _identity(session, table)
    plan, schedule = await _context(session, policy, identity, lock=True)
    if _drift(row, policy, identity, plan, schedule) or not await _fence_current(session, policy):
        raise HTTPException(409, "The Daily UTC review changed; reload the incident")
    result = await acknowledge_quality_incident_locked(session, policy, incident, policy.generation, actor, _utc_now(),
                                                       daily_only=True)
    await session.commit()
    return result


async def _decision(
    session: AsyncSession, row: AssetJobQualityRow, job: TransferScheduleJobRow, plan: TransferPlanRow, now: datetime,
):
    if job.finished_at is not None and not job.slot <= job.finished_at <= now:
        return "unknown", "outcome_unknown", None, None
    if job.state == "committed" and job.finished_at and job.receipt_id:
        item = await session.get(TransferRunRow, job.receipt_id)
        if (item is not None and item.plan_id == job.plan_id and item.request_id == job.id
            and item.plan_updated_at == row.plan_updated_at and item.initiated_by == SCHEDULED_SYSTEM_ACTOR
            and item.recorded_at <= now and item.write_mode == "upsert" and item.source_schema == item.target_schema == SANDBOX
            and item.source_table == plan.source_table and item.target_table == row.target_table
            and (item.target_oid, item.target_filenode) == (row.target_oid, row.target_filenode)):
            return ("healthy", None, item.id, item.rows_affected) if item.rows_affected > 0 else ("unknown", "zero_rows", None, 0)
        return "unknown", "unverified_receipt", None, None
    if job.state == "rejected" and job.finished_at is not None and job.receipt_id is None:
        proof = await session.get(TransferTargetRejectionRow, job.id)
        if proof is not None and proof.code == "target_constraint" and (
            proof.target_oid, proof.target_filenode,
        ) == (row.target_oid, row.target_filenode):
            attempt = await session.get(TransferAttemptRow, proof.attempt_id)
            if (attempt is not None and attempt.plan_id == job.plan_id and attempt.plan_updated_at == row.plan_updated_at
                and attempt.request_id == attempt.schedule_job_id == job.id and attempt.scope_trigger == "daily_utc"
                and attempt.target_table == row.target_table and attempt.source_table == plan.source_table
                and (attempt.target_oid, attempt.target_filenode) == (row.target_oid, row.target_filenode)
                and attempt.status == "rejected" and attempt.finished_at is not None and attempt.receipt_id is None):
                return "unhealthy", "target_rejected", None, None
    reason = {"pending": "nonterminal", "dispatched": "nonterminal", "expired": "expired",
              "outcome_unknown": "outcome_unknown", "rejected": "unproven_rejection"}.get(job.state, "unverified_receipt")
    return "unknown", reason, None, None


async def evaluate_daily_job_quality(
    session: AsyncSession, policy: AssetFreshnessPolicyRow, identity: tuple | None, now: datetime,
) -> None:
    """Evaluate one exact newest eligible slot, never dispatch or inspect business rows."""
    row = await session.get(AssetJobQualityRow, policy.target_table)
    if (row is None or row.state == "superseded" or (row.checked_at is not None and now < row.checked_at)
        or not await _visible(session, policy.target_table)):
        return
    plan, schedule = await _context(session, policy, identity, lock=True)
    reason = _drift(row, policy, identity, plan, schedule)
    if reason is None and not await _fence_current(session, policy):
        reason = "target_changed"
    if reason is not None:
        await _supersede(session, row, now, cause={
            "policy_changed": "policy_reviewed", "target_changed": "target_changed", "schedule_changed": "schedule_changed",
        }[reason])
        row.state, row.reason = "superseded", reason
        return
    expected = _expected_slot(now, row.planned_time_utc)
    if row.confirmed_slot is not None and expected < row.confirmed_slot:
        return
    row.checked_at = now
    row.observed_slot, row.job_state, row.finished_at, row.receipt_id, row.rows_affected = None, None, None, None, None
    if expected <= row.reviewed_at:
        row.state, row.reason = "unknown", "no_post_review_slot"
        return
    job = await session.scalar(select(TransferScheduleJobRow).where(
        TransferScheduleJobRow.plan_id == row.plan_id, TransferScheduleJobRow.generation == row.schedule_generation,
        TransferScheduleJobRow.slot > row.reviewed_at, TransferScheduleJobRow.slot <= expected,
    ).order_by(TransferScheduleJobRow.slot.desc()).limit(1))
    if job is None or job.slot != expected:
        row.state, row.reason = "unknown", "missing_slot"
        return
    row.observed_slot, row.job_state, row.finished_at = job.slot, job.state, job.finished_at
    if job.plan_updated_at != row.plan_updated_at or job.armed_by != row.armed_by:
        row.state, row.reason = "unknown", "unverified_receipt"
        return
    row.state, row.reason, row.receipt_id, row.rows_affected = await _decision(session, row, job, plan, now)
    if row.state in ("healthy", "unhealthy") and (row.confirmed_slot is None or job.slot > row.confirmed_slot):
        await observe_quality_incident(session, policy, _KIND, now, healthy=row.state == "healthy",
                                       failure="target_rejected" if row.state == "unhealthy" else None)
        row.confirmed_slot = job.slot
