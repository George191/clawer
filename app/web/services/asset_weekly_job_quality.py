"""Observe exact Weekly UTC transfer results on the existing sandbox quality tick."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.asset_freshness import AssetFreshnessPolicyRow, AssetQualityIncident, AssetQualityIncidentRow
from app.web.api.models.asset_job_quality import (
    AssetJobObservation, AssetJobQualityReview, TransferTargetRejectionRow, WeeklyJobQualityBinding,
    WeeklyJobEvidence, WeeklyJobQualityCandidate, WeeklyJobQualityRow, WeeklyJobQualityStatus,
    WeeklyReceiptEvidence, WeeklyTargetConstraintEvidence,
)
from app.web.api.models.transfer_plans import TransferAttemptRow, TransferPlanRow, TransferRunRow
from app.web.api.models.transfer_schedules import TransferScheduleJobRow, TransferScheduleRow
from app.web.api.models.user import User
from app.web.api.utils.models import StatusTypes
from app.web.core.config import settings
from app.web.services.asset_collected_stats import _fence_current, _visible
from app.web.services.asset_quality_incidents import (
    acknowledge_quality_incident_locked, list_quality_incidents, observe_quality_incident,
)
from app.web.services.transfer_runs import SANDBOX, SCHEDULED_SYSTEM_ACTOR, clock_slot_job_id, weekly_transfer_enabled

_TTL = timedelta(minutes=3)
_KIND = "weekly_job"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def weekly_job_quality_enabled() -> bool:
    from app.web.services.asset_freshness import asset_freshness_enabled
    return settings.TRANSFER_SANDBOX_WEEKLY_JOB_QUALITY_ENABLED and asset_freshness_enabled()


def _require_schema(schema: str) -> None:
    if schema != SANDBOX:
        raise HTTPException(404, "Weekly transfer checks are available only for reviewed sandbox targets")


async def _context(session: AsyncSession, policy: AssetFreshnessPolicyRow, identity: tuple | None, *, lock: bool = False):
    if not weekly_transfer_enabled() or not policy.enabled or identity != (policy.target_oid, policy.target_filenode):
        return None, None
    statement = select(TransferPlanRow).where(TransferPlanRow.id == policy.plan_id)
    plan = await session.scalar(statement.with_for_update(read=True, nowait=True) if lock else statement)
    if (plan is None or plan.updated_at != policy.plan_updated_at or plan.source_schema != SANDBOX
        or plan.target_schema != SANDBOX or plan.target_table != policy.target_table
        or plan.trigger != "weekly" or plan.write_mode != "upsert" or plan.incremental_cursor is not None
        or plan.schedule_time is None or plan.schedule_weekday_utc not in range(1, 8)):
        return None, None
    statement = select(TransferScheduleRow).where(TransferScheduleRow.plan_id == plan.id)
    schedule = await session.scalar(statement.with_for_update(read=True, nowait=True) if lock else statement)
    if schedule is None or not schedule.enabled or schedule.armed_for_updated_at != plan.updated_at:
        return None, None
    actor = select(User.id).where(
        User.id == schedule.armed_by, User.status == StatusTypes.ENABLE, User.is_superuser.is_(True),
    )
    if await session.scalar(actor.with_for_update(read=True, nowait=True) if lock else actor) is None:
        return None, None
    return plan, schedule


def _candidate(policy: AssetFreshnessPolicyRow, plan: TransferPlanRow | None, schedule: TransferScheduleRow | None):
    if not plan or not schedule:
        return None
    return WeeklyJobQualityCandidate(planId=plan.id, planUpdatedAt=plan.updated_at, freshnessGeneration=policy.generation,
                               scheduleGeneration=schedule.generation, plannedTimeUtc=plan.schedule_time,
                                     plannedWeekdayUtc=plan.schedule_weekday_utc)


def _binding(row: WeeklyJobQualityRow) -> WeeklyJobQualityBinding:
    return WeeklyJobQualityBinding(planId=row.plan_id, planUpdatedAt=row.plan_updated_at, freshnessGeneration=row.freshness_generation,
                             scheduleGeneration=row.schedule_generation, plannedTimeUtc=row.planned_time_utc,
                             generation=row.generation, reviewedAt=row.reviewed_at, plannedWeekdayUtc=row.planned_weekday_utc)


def _drift(row, policy, identity, plan, schedule) -> str | None:
    if (not policy.enabled or policy.generation != row.freshness_generation or policy.plan_id != row.plan_id
        or policy.plan_updated_at != row.plan_updated_at):
        return "policy_changed"
    if identity != (row.target_oid, row.target_filenode):
        return "target_changed"
    if (plan is None or schedule is None or schedule.generation != row.schedule_generation
        or schedule.armed_by != row.armed_by or plan.schedule_time != row.planned_time_utc
        or plan.schedule_weekday_utc != row.planned_weekday_utc):
        return "schedule_changed"
    return None


def _expected_slot(now: datetime, clock: str, weekday: int) -> datetime:
    utc = now.astimezone(timezone.utc)
    hours, minutes = map(int, clock.split(":"))
    latest = utc.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    latest -= timedelta(days=(utc.isoweekday() - weekday) % 7)
    return latest if latest <= utc else latest - timedelta(days=7)


async def _supersede(session: AsyncSession, row: WeeklyJobQualityRow, now: datetime, *, cause: str) -> None:
    await session.execute(update(AssetQualityIncidentRow).where(
        AssetQualityIncidentRow.target_table == row.target_table, AssetQualityIncidentRow.generation == row.freshness_generation,
        AssetQualityIncidentRow.kind == _KIND, AssetQualityIncidentRow.closed_at.is_(None),
    ).values(closed_at=now, close_reason=cause))


async def _read_context(session: AsyncSession, table: str):
    from app.web.services.asset_freshness import _identity
    identity = await _identity(session, table)
    return identity, await session.get(AssetFreshnessPolicyRow, table)


def _observation(row: WeeklyJobQualityRow) -> AssetJobObservation | None:
    return (AssetJobObservation(slot=row.observed_slot, state=row.job_state, finishedAt=row.finished_at,
                                receiptId=row.receipt_id, rowsAffected=row.rows_affected)
            if row.observed_slot else None)


async def read_weekly_job_quality(session: AsyncSession, schema: str, table: str) -> WeeklyJobQualityStatus:
    from app.web.services.asset_freshness import asset_freshness_enabled

    _require_schema(schema)
    disabled = WeeklyJobQualityStatus(available=False, state="disabled", reason="checking_disabled")
    if not asset_freshness_enabled():
        return disabled
    enabled = weekly_job_quality_enabled()
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL statement_timeout='1500ms'"))
    await session.execute(text("SET LOCAL lock_timeout='750ms'"))
    if not await _visible(session, table):
        return WeeklyJobQualityStatus(available=False, state="disabled", reason="target_unavailable")
    identity, policy = await _read_context(session, table)
    if policy is None:
        if not enabled:
            return disabled
        return WeeklyJobQualityStatus(available=identity is not None, state="not_configured",
                                     reason="target_unavailable" if identity is None else "no_eligible_schedule")
    plan, schedule = await _context(session, policy, identity)
    candidate = _candidate(policy, plan, schedule)
    row = await session.get(WeeklyJobQualityRow, table)
    if not enabled:
        if row is None or _drift(row, policy, identity, plan, schedule) or not await _fence_current(session, policy):
            return disabled
        return WeeklyJobQualityStatus(
            available=True, state="disabled", reason="checking_disabled", binding=_binding(row), checkedAt=row.checked_at,
            observation=_observation(row), incidents=await list_quality_incidents(session, policy, weekly_only=True),
        )
    if row is None:
        return WeeklyJobQualityStatus(available=True, state="not_configured", candidate=candidate,
                                     reason="no_eligible_schedule" if candidate is None else None)
    incidents = await list_quality_incidents(session, policy, weekly_only=True)
    stale_scope = _drift(row, policy, identity, plan, schedule) or (row.reason if row.state == "superseded" else None)
    if stale_scope is None and not await _fence_current(session, policy):
        stale_scope, candidate = "target_changed", None
    info = dict(available=True, candidate=candidate, binding=_binding(row), incidents=incidents, checkedAt=row.checked_at)
    if stale_scope:
        incidents.active = []
        return WeeklyJobQualityStatus(**info, state="superseded", reason=stale_scope)
    if row.checked_at is None:
        return WeeklyJobQualityStatus(**info, state="not_checked")
    valid = min(row.checked_at + _TTL, _expected_slot(row.checked_at, row.planned_time_utc, row.planned_weekday_utc) + timedelta(days=7))
    if not row.checked_at <= _utc_now() < valid:
        return WeeklyJobQualityStatus(**info, state="unknown", reason="evaluation_stale")
    return WeeklyJobQualityStatus(
        **info, state=row.state, reason=row.reason, validUntil=valid,
        observation=_observation(row),
    )


async def review_weekly_job_quality(
    session: AsyncSession, schema: str, table: str, request: AssetJobQualityReview,
) -> WeeklyJobQualityStatus:
    from app.web.services.asset_freshness import _identity, _lock_policy
    _require_schema(schema)
    if not weekly_job_quality_enabled():
        raise HTTPException(403, "Weekly transfer outcome checks are disabled")
    await session.execute(text("SET LOCAL statement_timeout='2500ms'"))
    await session.execute(text("SET LOCAL lock_timeout='750ms'"))
    if not await _visible(session, table):
        raise HTTPException(404, "The exact sandbox target is not eligible")
    policy = await _lock_policy(session, table)
    if policy is None:
        raise HTTPException(409, "Review the exact asset freshness policy first")
    row = await session.get(WeeklyJobQualityRow, table)
    if request.expectedGeneration != (row.generation if row else None):
        raise HTTPException(409, "The Weekly UTC review changed; reload before continuing")
    identity = await _identity(session, table)
    plan, schedule = await _context(session, policy, identity, lock=True)
    if ((candidate := _candidate(policy, plan, schedule)) is None or not await _fence_current(session, policy)
        or (candidate.planId, candidate.planUpdatedAt, candidate.freshnessGeneration, candidate.scheduleGeneration) != (
            request.planId, request.expectedPlanUpdatedAt, request.expectedFreshnessGeneration, request.expectedScheduleGeneration,
        )):
        raise HTTPException(409, "Review the current eligible Weekly UTC schedule and freshness policy")
    if row is not None and row.state != "superseded" and not _drift(row, policy, identity, plan, schedule):
        await session.commit()
        return await read_weekly_job_quality(session, schema, table)
    now = _utc_now()
    if row is not None:
        await _supersede(session, row, now, cause="schedule_changed")
    updates = dict(
        generation=row.generation + 1 if row else 1, freshness_generation=policy.generation,
        schedule_generation=schedule.generation, plan_id=plan.id, plan_updated_at=plan.updated_at,
        armed_by=schedule.armed_by, target_oid=identity[0], target_filenode=identity[1], planned_time_utc=plan.schedule_time,
        planned_weekday_utc=plan.schedule_weekday_utc,
        reviewed_at=now, checked_at=None, state="not_checked", reason=None, observed_slot=None, job_state=None,
        finished_at=None, receipt_id=None, rows_affected=None, confirmed_slot=None,
    )
    if row is None:
        session.add(WeeklyJobQualityRow(target_table=table, **updates))
    else:
        for field, value in updates.items():
            setattr(row, field, value)
    await session.commit()
    return await read_weekly_job_quality(session, schema, table)


async def acknowledge_weekly_job(
    session: AsyncSession, schema: str, table: str, incident: UUID, generation: int, actor: UUID,
) -> AssetQualityIncident:
    from app.web.services.asset_freshness import _identity, _lock_policy
    _require_schema(schema)
    if not weekly_job_quality_enabled():
        raise HTTPException(403, "Weekly transfer outcome checks are disabled")
    await session.execute(text("SET LOCAL statement_timeout='2500ms'"))
    await session.execute(text("SET LOCAL lock_timeout='750ms'"))
    if not await _visible(session, table):
        raise HTTPException(404, "The exact sandbox target is not eligible")
    policy = await _lock_policy(session, table)
    row = await session.get(WeeklyJobQualityRow, table)
    if policy is None or row is None or row.generation != generation or row.state == "superseded":
        raise HTTPException(409, "The Weekly UTC review changed; reload the incident")
    identity = await _identity(session, table)
    plan, schedule = await _context(session, policy, identity, lock=True)
    if _drift(row, policy, identity, plan, schedule) or not await _fence_current(session, policy):
        raise HTTPException(409, "The Weekly UTC review changed; reload the incident")
    result = await acknowledge_quality_incident_locked(session, policy, incident, policy.generation, actor, _utc_now(),
                                                       weekly_only=True)
    await session.commit()
    return result


def _attempt_scope(attempt: TransferAttemptRow, row: WeeklyJobQualityRow, job: TransferScheduleJobRow,
                   plan: TransferPlanRow) -> bool:
    return (attempt.plan_id == job.plan_id and attempt.plan_updated_at == row.plan_updated_at
            and attempt.request_id == attempt.schedule_job_id == job.id and attempt.scope_trigger == "week_utc"
            and attempt.upstream_job_id is None
            and attempt.target_table == row.target_table and attempt.source_table == plan.source_table
            and (attempt.target_oid, attempt.target_filenode) == (row.target_oid, row.target_filenode)
            and attempt.finished_at is not None and job.slot <= attempt.started_at <= attempt.finished_at <= job.finished_at)


async def _decision(
    session: AsyncSession, row: WeeklyJobQualityRow, job: TransferScheduleJobRow, plan: TransferPlanRow, now: datetime,
):
    if job.state in ("committed", "rejected") and job.finished_at is not None and not (
        job.slot <= job.finished_at <= now and job.finished_at < job.expires_at
        and (job.dispatched_at is None or job.slot <= job.dispatched_at <= job.finished_at)
    ):
        return "unknown", "outcome_unknown", None, None
    if job.state == "committed" and job.finished_at is not None and job.receipt_id is not None:
        item = await session.get(TransferRunRow, job.receipt_id)
        if (item is not None and item.plan_id == job.plan_id and item.request_id == job.id
            and item.plan_updated_at == row.plan_updated_at and item.initiated_by == SCHEDULED_SYSTEM_ACTOR
            and job.slot <= item.recorded_at <= job.finished_at
            and (job.dispatched_at is None or job.dispatched_at <= item.recorded_at)
            and 0 <= item.rows_affected <= 100
            and item.write_mode == "upsert" and item.cursor_through is None and item.has_more is None
            and item.source_schema == item.target_schema == SANDBOX
            and item.source_table == plan.source_table and item.target_table == row.target_table
            and (item.target_oid, item.target_filenode) == (row.target_oid, row.target_filenode)):
            attempts = (await session.scalars(select(TransferAttemptRow).where(
                TransferAttemptRow.receipt_id == item.id,
                text("scope_trigger IN ('daily_utc','week_utc') AND status = 'committed' AND receipt_id IS NOT NULL"),
                TransferAttemptRow.scope_trigger == "week_utc",
                TransferAttemptRow.schedule_job_id == job.id,
            ).limit(2))).all()
            if (len(attempts) == 1 and _attempt_scope(attempts[0], row, job, plan)
                and attempts[0].started_at <= item.recorded_at <= attempts[0].finished_at):
                return (("healthy", None, item.id, item.rows_affected) if item.rows_affected > 0
                        else ("unknown", "zero_rows", None, 0))
        return "unknown", "unverified_receipt", None, None
    if job.state == "rejected" and job.finished_at is not None and job.receipt_id is None:
        proof = await session.get(TransferTargetRejectionRow, job.id)
        if proof is not None and proof.code == "target_constraint" and (
            proof.target_oid, proof.target_filenode,
        ) == (row.target_oid, row.target_filenode):
            attempt = await session.get(TransferAttemptRow, proof.attempt_id)
            if (attempt is not None and attempt.status == "rejected" and attempt.receipt_id is None
                and _attempt_scope(attempt, row, job, plan)):
                return "unhealthy", "target_rejected", None, None
    reason = {"pending": "nonterminal", "dispatched": "nonterminal", "expired": "expired",
              "canceled": "canceled", "outcome_unknown": "outcome_unknown",
              "rejected": "unproven_rejection"}.get(job.state, "unverified_receipt")
    return "unknown", reason, None, None


def weekly_evidence_unavailable() -> HTTPException:
    return HTTPException(404, "Verified Weekly UTC evidence is unavailable",
                         headers={"Cache-Control": "private, no-store"})


async def read_weekly_job_evidence(
    session: AsyncSession, schema: str, table: str, expected_generation: int, expected_slot: datetime,
) -> WeeklyJobEvidence:
    """Reprove one current persisted outcome; never look up arbitrary IDs or read business rows."""
    from app.web.services.transfer_observed_lineage import observed_receipt_physical_proof, visible_observed_table_identity
    from app.web.services.transfer_runs import observed_lineage_enabled

    unavailable = weekly_evidence_unavailable()
    if schema != SANDBOX or not weekly_job_quality_enabled():
        raise unavailable
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL statement_timeout='750ms'"))
    await session.execute(text("SET LOCAL lock_timeout='100ms'"))
    identity = await visible_observed_table_identity(session, schema, table)
    if identity is None:
        raise unavailable
    policy = await session.get(AssetFreshnessPolicyRow, table)
    row = await session.get(WeeklyJobQualityRow, table)
    if (policy is None or row is None or row.generation != expected_generation
        or row.checked_at is None or row.state not in ("healthy", "unhealthy")):
        raise unavailable
    plan, schedule = await _context(session, policy, identity)
    if _drift(row, policy, identity, plan, schedule):
        raise unavailable
    now = _utc_now()
    slot = _expected_slot(now, row.planned_time_utc, row.planned_weekday_utc)
    if (slot != expected_slot or slot != row.observed_slot or slot != row.confirmed_slot
        or not row.reviewed_at < slot <= row.checked_at <= now < row.checked_at + _TTL
        or _expected_slot(row.checked_at, row.planned_time_utc, row.planned_weekday_utc) != slot):
        raise unavailable

    job = await session.get(TransferScheduleJobRow, clock_slot_job_id(row.plan_id, row.schedule_generation, slot))
    if (job is None or job.plan_id != row.plan_id or job.generation != row.schedule_generation
        or job.schedule_weekday_utc != row.planned_weekday_utc or job.slot != slot
        or job.plan_updated_at != row.plan_updated_at or job.armed_by != row.armed_by
        or job.expires_at != slot + timedelta(minutes=5) or schedule.updated_at > slot
        or job.state != row.job_state or job.finished_at != row.finished_at or job.finished_at is None
        or job.canceled_at is not None or job.canceled_by is not None):
        raise unavailable
    state, reason, receipt_id, affected = await _decision(session, row, job, plan, row.checked_at)
    if (state, reason, receipt_id, affected) != (row.state, row.reason, row.receipt_id, row.rows_affected):
        raise unavailable
    source = await visible_observed_table_identity(session, plan.source_schema, plan.source_table)
    if source is None:
        raise unavailable
    fields = dict(jobId=job.id, slot=slot, finishedAt=job.finished_at)
    if state == "healthy" and receipt_id is not None and affected is not None and observed_lineage_enabled():
        receipt = await session.get(TransferRunRow, receipt_id)
        physical = await observed_receipt_physical_proof(session, table, receipt_id)
        if (receipt is None or physical is None or physical.trigger != "weekly_utc" or physical.job_id != job.id
            or source != (physical.source_oid, physical.source_node)
            or identity != (physical.target_oid, physical.target_node)):
            raise unavailable
        return WeeklyReceiptEvidence(**fields, receiptId=receipt.id, recordedAt=receipt.recorded_at, rowsAffected=affected)
    if state == "unhealthy" and reason == "target_rejected":
        proof = await session.get(TransferTargetRejectionRow, job.id)
        attempt = await session.get(TransferAttemptRow, proof.attempt_id) if proof is not None else None
        if attempt is None or attempt.finished_at is None:
            raise unavailable
        # There is deliberately no claim of a physically attested source or a transferred row.
        return WeeklyTargetConstraintEvidence(
            **fields, attemptId=attempt.id, startedAt=attempt.started_at, attemptFinishedAt=attempt.finished_at,
        )
    raise unavailable


async def evaluate_weekly_job_quality(
    session: AsyncSession, policy: AssetFreshnessPolicyRow, identity: tuple | None, now: datetime,
) -> None:
    """Evaluate one exact newest eligible slot, never dispatch or inspect business rows."""
    row = await session.get(WeeklyJobQualityRow, policy.target_table)
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
    expected = _expected_slot(now, row.planned_time_utc, row.planned_weekday_utc)
    if row.confirmed_slot is not None and expected < row.confirmed_slot:
        return
    row.checked_at = now
    row.observed_slot, row.job_state, row.finished_at, row.receipt_id, row.rows_affected = None, None, None, None, None
    if expected <= row.reviewed_at:
        row.state, row.reason = "unknown", "no_post_review_slot"
        return
    job = await session.scalar(select(TransferScheduleJobRow).where(
        TransferScheduleJobRow.plan_id == row.plan_id, TransferScheduleJobRow.generation == row.schedule_generation,
        TransferScheduleJobRow.schedule_weekday_utc == row.planned_weekday_utc, TransferScheduleJobRow.slot == expected,
    ))
    if job is None or job.slot != expected:
        row.state, row.reason = "unknown", "missing_slot"
        return
    if (job.plan_updated_at != row.plan_updated_at or job.armed_by != row.armed_by
        or job.id != clock_slot_job_id(row.plan_id, row.schedule_generation, expected)
        or job.expires_at != expected + timedelta(minutes=5)):
        row.state, row.reason = "unknown", "unverified_receipt"
        return
    row.observed_slot, row.job_state, row.finished_at = job.slot, job.state, job.finished_at
    row.state, row.reason, row.receipt_id, row.rows_affected = await _decision(session, row, job, plan, now)
    if row.state in ("healthy", "unhealthy") and (row.confirmed_slot is None or job.slot > row.confirmed_slot):
        await observe_quality_incident(session, policy, _KIND, now, healthy=row.state == "healthy",
                                       failure="target_rejected" if row.state == "unhealthy" else None)
        row.confirmed_slot = job.slot
