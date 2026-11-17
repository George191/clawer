"""One-hop sandbox trigger over durable, positive manual Upsert commit events."""

from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import case, exists, func, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlmodel import select

from app.storage.postgres_client import get_pg_client
from app.web.api.models.transfer_plans import TransferPlanRow, TransferRunRequest
from app.web.api.models.transfer_upstream import (
    TransferCommitEventRow, UpstreamCandidate, UpstreamTrigger, UpstreamTriggerChange, UpstreamTriggerJob,
    UpstreamTriggerJobRow, UpstreamTriggerRow, UpstreamTriggerSummary,
)
from app.web.services.transfer_runs import (
    HISTORY_LIMIT, SANDBOX, is_sandbox_upsert, lock_transfer_plans, run_sandbox_transfer,
    upstream_catalog, upstream_catalog_digest, upstream_pair_eligible, upstream_transfer_enabled,
)
from app.web.services.etl_layers import get_etl_layers
from app.web.services.transfer_plans import _response as plan_response, compare_transfer_endpoints

_OPEN = ("pending", "dispatched")
_WINDOW = timedelta(minutes=5)
_LEASE = timedelta(seconds=45)
_LIMIT = 100


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _response(child: TransferPlanRow, binding: UpstreamTriggerRow | None, parent: TransferPlanRow | None) -> UpstreamTrigger:
    available = upstream_transfer_enabled() and is_sandbox_upsert(child, "upstream")
    return UpstreamTrigger(
        available=available, configuredEnabled=bool(binding and binding.enabled),
        enabled=bool(available and binding and binding.enabled and parent and upstream_pair_eligible(child, parent)
                     and binding.child_updated_at == child.updated_at and binding.parent_updated_at == parent.updated_at),
        generation=binding.generation if binding else None,
        parentPlanId=binding.parent_plan_id if binding else None, parentPlanName=parent.name if parent else None,
        armedForUpdatedAt=binding.child_updated_at if binding else None,
        armedForParentUpdatedAt=binding.parent_updated_at if binding else None,
        updatedAt=binding.updated_at if binding else None,
    )


async def _need_plan(session: AsyncSession, plan_id: UUID) -> TransferPlanRow:
    row = await session.get(TransferPlanRow, plan_id)
    if row is None:
        raise HTTPException(404, "Transfer draft does not exist")
    return row


async def read_upstream_trigger(session: AsyncSession, plan_id: UUID) -> UpstreamTrigger:
    child = await _need_plan(session, plan_id)
    binding = await session.get(UpstreamTriggerRow, plan_id)
    parent = await session.get(TransferPlanRow, binding.parent_plan_id) if binding else None
    return _response(child, binding, parent)


async def list_upstream_candidates(session: AsyncSession, plan_id: UUID) -> list[UpstreamCandidate]:
    child = await _need_plan(session, plan_id)
    if not is_sandbox_upsert(child, "upstream"):
        return []
    rows = await session.scalars(select(TransferPlanRow).where(
        TransferPlanRow.id != plan_id, TransferPlanRow.trigger == "manual", TransferPlanRow.write_mode == "upsert",
        TransferPlanRow.source_schema == SANDBOX, TransferPlanRow.target_schema == SANDBOX,
        TransferPlanRow.target_table == child.source_table, TransferPlanRow.source_table != child.target_table,
        TransferPlanRow.incremental_cursor.is_(None),
    ).order_by(TransferPlanRow.name, TransferPlanRow.id).limit(_LIMIT))
    return [UpstreamCandidate(id=row.id, name=row.name, updatedAt=row.updated_at,
                              sourceTable=row.source_table, targetTable=row.target_table) for row in rows]


async def change_upstream_trigger(
    session: AsyncSession, plan_id: UUID, change: UpstreamTriggerChange, actor_id: UUID,
) -> UpstreamTrigger:
    if change.enabled and not upstream_transfer_enabled():
        raise HTTPException(403, "Upstream sandbox transfer execution is disabled")
    if change.enabled and (change.parentPlanId is None or change.expectedParentUpdatedAt is None):
        raise HTTPException(422, "Choose one reviewed manual Upsert parent")
    await session.execute(text("SET LOCAL lock_timeout = '3s'"))
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    ids = (plan_id, change.parentPlanId) if change.enabled else (plan_id,)
    try:
        locked = await lock_transfer_plans(session, *ids)
    except DBAPIError as failure:
        if getattr(failure.orig, "sqlstate", None) == "55P03":
            raise HTTPException(409, "A transfer is settling; retry changing this trigger") from None
        raise
    child = locked.get(plan_id)
    if child is None:
        raise HTTPException(404, "Transfer draft does not exist")
    if child.updated_at != change.expectedUpdatedAt:
        raise HTTPException(409, "The downstream draft changed; reload it before changing the trigger")
    binding = await session.get(UpstreamTriggerRow, plan_id)
    if change.expectedGeneration != (binding.generation if binding else None):
        raise HTTPException(409, "The upstream trigger changed; reload it")
    if not change.enabled:
        parent = await session.get(TransferPlanRow, binding.parent_plan_id) if binding else None
        if binding and binding.enabled:
            binding.enabled = False
            binding.generation += 1
            binding.updated_at = _utc_now()
        result = _response(child, binding, parent)
        await session.commit()
        return result
    parent = locked.get(change.parentPlanId)
    if parent is None or not upstream_pair_eligible(child, parent):
        raise HTTPException(422, "Choose a manual Upsert parent whose target is this source, with a distinct downstream target")
    if parent.updated_at != change.expectedParentUpdatedAt:
        raise HTTPException(409, "The upstream draft changed; choose its reviewed version again")
    physical = upstream_catalog_digest(await upstream_catalog(session, child, parent))
    for plan in (parent, child):
        endpoints = await get_etl_layers().transfer_endpoint_columns_in_session(
            session, (SANDBOX, plan.source_table), (SANDBOX, plan.target_table),
        )
        if compare_transfer_endpoints(plan_response(plan), endpoints).status != "compatible":
            raise HTTPException(422, "Both transfers must pass current sandbox structure preflight")
    if (binding is None or not binding.enabled or binding.parent_plan_id != parent.id or binding.armed_by != actor_id
        or binding.child_updated_at != child.updated_at or binding.parent_updated_at != parent.updated_at
        or binding.physical_digest != physical):
        # Producer holds the same parent lock while committing each event.
        # Sequence cutoffs are therefore ordered for this parent even if other parents run concurrently.
        cutoff = await session.scalar(select(func.coalesce(func.max(TransferCommitEventRow.id), 0)).where(
            TransferCommitEventRow.parent_plan_id == parent.id,
        ))
        fields = dict(
            enabled=True, generation=binding.generation + 1 if binding else 1, armed_by=actor_id,
            parent_plan_id=parent.id, parent_updated_at=parent.updated_at, child_updated_at=child.updated_at,
            physical_digest=physical, cutoff_event_id=cutoff, updated_at=_utc_now(),
        )
        if binding is None:
            binding = UpstreamTriggerRow(plan_id=plan_id, **fields)
            session.add(binding)
        else:
            for field, value in fields.items():
                setattr(binding, field, value)
    result = _response(child, binding, parent)
    await session.commit()
    return result


async def list_upstream_triggers(session: AsyncSession) -> list[UpstreamTriggerSummary]:
    parent_alias = aliased(TransferPlanRow)
    statement = (select(TransferPlanRow, UpstreamTriggerRow, parent_alias)
                 .join(UpstreamTriggerRow, UpstreamTriggerRow.plan_id == TransferPlanRow.id)
                 .join(parent_alias, parent_alias.id == UpstreamTriggerRow.parent_plan_id)
                 .order_by(UpstreamTriggerRow.updated_at.desc(), TransferPlanRow.id).limit(_LIMIT))
    return [UpstreamTriggerSummary(planId=child.id, planName=child.name, sourceSchema=child.source_schema,
                                   sourceTable=child.source_table, targetSchema=child.target_schema,
                                   targetTable=child.target_table, **_response(child, binding, parent).model_dump())
            for child, binding, parent in (await session.execute(statement)).all()]


async def list_upstream_jobs(session: AsyncSession, plan_id: UUID) -> list[UpstreamTriggerJob]:
    await _need_plan(session, plan_id)
    statement = (select(UpstreamTriggerJobRow, TransferCommitEventRow)
                 .join(TransferCommitEventRow, TransferCommitEventRow.id == UpstreamTriggerJobRow.event_id)
                 .where(UpstreamTriggerJobRow.plan_id == plan_id)
                 .order_by(UpstreamTriggerJobRow.event_id.desc(), UpstreamTriggerJobRow.generation.desc()).limit(HISTORY_LIMIT))
    return [UpstreamTriggerJob(id=job.id, eventId=str(event.id), eventRecordedAt=event.recorded_at,
                               parentReceiptId=event.parent_receipt_id, state=job.state, receiptId=job.receipt_id,
                               dispatchedAt=job.dispatched_at, finishedAt=job.finished_at, message=job.message)
            for job, event in (await session.execute(statement)).all()]


async def collect_due_upstream_transfer_jobs(session: AsyncSession, *, now: datetime | None = None) -> list[UUID]:
    if not upstream_transfer_enabled():
        return []
    now = (now or _utc_now()).astimezone(timezone.utc)
    await session.execute(text("SET LOCAL lock_timeout = '3s'"))
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    expired = (await session.scalars(select(UpstreamTriggerJobRow).where(
        UpstreamTriggerJobRow.state.in_(_OPEN), UpstreamTriggerJobRow.expires_at <= now,
    ).order_by(UpstreamTriggerJobRow.expires_at).limit(_LIMIT).with_for_update(skip_locked=True))).all()
    for job in expired:
        job.state = "expired"
        job.finished_at = now
        job.message = "The event dispatch window closed before an outcome was confirmed"

    parent = aliased(TransferPlanRow)
    due = (select(UpstreamTriggerRow, TransferCommitEventRow)
           .join(TransferCommitEventRow, TransferCommitEventRow.parent_plan_id == UpstreamTriggerRow.parent_plan_id)
           .join(TransferPlanRow, TransferPlanRow.id == UpstreamTriggerRow.plan_id)
           .join(parent, parent.id == UpstreamTriggerRow.parent_plan_id)
           .where(UpstreamTriggerRow.enabled, UpstreamTriggerRow.child_updated_at == TransferPlanRow.updated_at,
                  UpstreamTriggerRow.parent_updated_at == parent.updated_at,
                  TransferCommitEventRow.parent_updated_at == UpstreamTriggerRow.parent_updated_at,
                  TransferCommitEventRow.id > UpstreamTriggerRow.cutoff_event_id,
                  TransferCommitEventRow.recorded_at <= now,
                  ~exists().where(UpstreamTriggerJobRow.plan_id == UpstreamTriggerRow.plan_id,
                                  UpstreamTriggerJobRow.generation == UpstreamTriggerRow.generation,
                                  UpstreamTriggerJobRow.event_id == TransferCommitEventRow.id))
           .order_by(TransferCommitEventRow.id, UpstreamTriggerRow.plan_id))
    window_start = now - _WINDOW
    candidates = (await session.execute(
        due.where(TransferCommitEventRow.recorded_at > window_start).limit(_LIMIT),
    )).all()
    if len(candidates) < _LIMIT:
        candidates += (await session.execute(
            due.where(TransferCommitEventRow.recorded_at <= window_start).limit(_LIMIT - len(candidates)),
        )).all()
    if candidates:
        values = []
        for binding, event in candidates:
            deadline = event.recorded_at + _WINDOW
            missed = deadline <= now
            values.append(dict(
                id=uuid5(NAMESPACE_URL, f"asiral:upstream:{binding.plan_id}:{binding.generation}:{event.id}"),
                plan_id=binding.plan_id, generation=binding.generation, event_id=event.id,
                child_updated_at=binding.child_updated_at, armed_by=binding.armed_by, expires_at=deadline,
                state="expired" if missed else "pending", dispatch_after=now,
                finished_at=now if missed else None,
                message="The event dispatch window closed before dispatch" if missed else None,
            ))
        await session.execute(insert(UpstreamTriggerJobRow).values(values).on_conflict_do_nothing())
    jobs = (await session.scalars(select(UpstreamTriggerJobRow).where(
        UpstreamTriggerJobRow.state.in_(_OPEN), UpstreamTriggerJobRow.expires_at > now,
        UpstreamTriggerJobRow.dispatch_after <= now,
    ).order_by(UpstreamTriggerJobRow.dispatch_after, UpstreamTriggerJobRow.id)
        .limit(_LIMIT).with_for_update(skip_locked=True))).all()
    ids = []
    for job in jobs:
        job.dispatch_after = now + _LEASE
        ids.append(job.id)
    await session.commit()
    return ids


async def list_due_upstream_transfer_jobs() -> list[UUID]:
    if not upstream_transfer_enabled():
        return []
    async with get_pg_client().session() as session:
        return await collect_due_upstream_transfer_jobs(session)


async def record_upstream_transfer_job_dispatched(job_id: UUID) -> None:
    if not upstream_transfer_enabled():
        return
    async with get_pg_client().session() as session:
        await session.execute(text("SET LOCAL lock_timeout = '3s'"))
        await session.execute(update(UpstreamTriggerJobRow).where(
            UpstreamTriggerJobRow.id == job_id, UpstreamTriggerJobRow.dispatched_at.is_(None),
        ).values(state=case((UpstreamTriggerJobRow.state == "pending", "dispatched"), else_=UpstreamTriggerJobRow.state),
                 dispatched_at=_utc_now()))


async def execute_upstream_transfer_job_in_session(session: AsyncSession, job_id: UUID, *, now: datetime | None = None) -> str:
    if not upstream_transfer_enabled():
        return "disabled"
    job = await session.get(UpstreamTriggerJobRow, job_id)
    if job is None:
        return "missing"
    if job.state not in _OPEN:
        return job.state
    try:
        await run_sandbox_transfer(session, job.plan_id, TransferRunRequest(
            requestId=job.id, expectedUpdatedAt=job.child_updated_at,
        ), job.armed_by, upstream_job_id=job.id, scheduled_now=now)
        return "committed"
    except HTTPException:
        updated = await session.get(UpstreamTriggerJobRow, job_id, populate_existing=True)
        return updated.state if updated else "missing"


async def execute_upstream_transfer_job(job_id: UUID, *, now: datetime | None = None) -> str:
    if not upstream_transfer_enabled():
        return "disabled"
    async with get_pg_client().session() as session:
        return await execute_upstream_transfer_job_in_session(session, job_id, now=now)
