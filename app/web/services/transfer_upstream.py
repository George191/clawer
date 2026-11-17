"""One-hop sandbox trigger over durable, positive manual Upsert commit events."""

from datetime import datetime, timedelta, timezone
from itertools import product
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import case, delete, exists, func, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.storage.postgres_client import get_pg_client
from app.web.api.models.transfer_plans import TransferPlanRow, TransferRunRequest
from app.web.api.models.transfer_upstream import (
    TransferCommitEventRow, UpstreamCandidate, UpstreamTrigger, UpstreamTriggerChange, UpstreamTriggerEvent,
    UpstreamTriggerJob, UpstreamTriggerJobEventRow, UpstreamTriggerJobRow, UpstreamTriggerParent,
    UpstreamTriggerParentRow, UpstreamTriggerRow, UpstreamTriggerSummary,
)
from app.web.services.transfer_runs import (
    HISTORY_LIMIT, SANDBOX, is_sandbox_upsert, lock_transfer_plans, run_sandbox_transfer,
    upstream_catalog, upstream_catalog_digest, upstream_pair_eligible, upstream_transfer_enabled,
)
from app.web.services.etl_layers import get_etl_layers
from app.web.services.upstream_rules import (
    EVENT_WINDOW, canonical_group, exact_nested_job, positive_nested_events, valid_witness, witness_id,
)
from app.web.services.transfer_plans import _response as plan_response, compare_transfer_endpoints

_OPEN = ("pending", "dispatched")
_WINDOW = EVENT_WINDOW
_LEASE = timedelta(seconds=45)
_LIMIT = 100


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _response(
    child: TransferPlanRow, binding: UpstreamTriggerRow | None,
    reviewed: list[tuple[UpstreamTriggerParentRow, TransferPlanRow]],
) -> UpstreamTrigger:
    available = upstream_transfer_enabled() and is_sandbox_upsert(child, "upstream")
    group = None
    valid = True
    if binding:
        try:
            group = canonical_group(binding.operator, binding.group_operator, binding.group_parent_ids,
                                    [member.parent_plan_id for member, _ in reviewed])
        except ValueError:
            valid = False
    return UpstreamTrigger(
        available=available, configuredEnabled=bool(binding and binding.enabled),
        enabled=bool(available and valid and binding and binding.enabled and binding.child_updated_at == child.updated_at
                     and 1 <= len(reviewed) <= 3 and len({parent.source_table for _, parent in reviewed}) == len(reviewed)
                     and all(upstream_pair_eligible(child, parent)
                     and member.parent_updated_at == parent.updated_at for member, parent in reviewed)),
        operator=binding.operator if binding else "OR", group=group,
        parents=[UpstreamTriggerParent(planId=member.parent_plan_id, planName=parent.name,
                                       armedForUpdatedAt=member.parent_updated_at) for member, parent in reviewed],
        generation=binding.generation if binding else None,
        armedForUpdatedAt=binding.child_updated_at if binding else None,
        updatedAt=binding.updated_at if binding else None,
    )


async def _reviewed_parents(
    session: AsyncSession, ids: list[UUID],
) -> dict[UUID, list[tuple[UpstreamTriggerParentRow, TransferPlanRow]]]:
    if not ids:
        return {}
    statement = (select(UpstreamTriggerParentRow, TransferPlanRow)
                 .join(TransferPlanRow, TransferPlanRow.id == UpstreamTriggerParentRow.parent_plan_id)
                 .where(UpstreamTriggerParentRow.plan_id.in_(ids)).order_by(UpstreamTriggerParentRow.parent_plan_id))
    grouped = {}
    for member, parent in (await session.execute(statement)).all():
        grouped.setdefault(member.plan_id, []).append((member, parent))
    return grouped


async def _need_plan(session: AsyncSession, plan_id: UUID) -> TransferPlanRow:
    row = await session.get(TransferPlanRow, plan_id)
    if row is None:
        raise HTTPException(404, "Transfer draft does not exist")
    return row


async def read_upstream_trigger(session: AsyncSession, plan_id: UUID) -> UpstreamTrigger:
    child = await _need_plan(session, plan_id)
    binding = await session.get(UpstreamTriggerRow, plan_id)
    reviewed = (await _reviewed_parents(session, [plan_id])).get(plan_id, [])
    return _response(child, binding, reviewed)


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
    await session.execute(text("SET LOCAL lock_timeout = '3s'"))
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    selected = {item.planId: item.expectedUpdatedAt for item in change.parents}
    try:
        locked = await lock_transfer_plans(session, plan_id, *selected)
        binding = (await session.scalars(select(UpstreamTriggerRow).where(UpstreamTriggerRow.plan_id == plan_id)
                                        .with_for_update().execution_options(populate_existing=True))).one_or_none()
    except DBAPIError as failure:
        if getattr(failure.orig, "sqlstate", None) == "55P03":
            raise HTTPException(409, "A transfer is settling; retry changing this trigger") from None
        raise
    child = locked.get(plan_id)
    if child is None:
        raise HTTPException(404, "Transfer draft does not exist")
    if child.updated_at != change.expectedUpdatedAt:
        raise HTTPException(409, "The downstream draft changed; reload it before changing the trigger")
    if change.expectedGeneration != (binding.generation if binding else None):
        raise HTTPException(409, "The upstream trigger changed; reload it")
    reviewed = (await _reviewed_parents(session, [plan_id])).get(plan_id, [])
    if not change.enabled:
        if binding and binding.enabled:
            binding.enabled = False
            binding.generation += 1
            binding.updated_at = _utc_now()
        result = _response(child, binding, reviewed)
        await session.commit()
        return result

    parents = [locked.get(pid) for pid in sorted(selected)]
    if any(parent is None or not upstream_pair_eligible(child, parent) for parent in parents):
        raise HTTPException(422, "Choose manual Upsert parents whose target is this source, with a distinct downstream target")
    if len({parent.source_table for parent in parents}) != len(parents):
        raise HTTPException(422, "Choose distinct source tables for the reviewed upstream plans")
    if any(parent.updated_at != selected[parent.id] for parent in parents):
        raise HTTPException(409, "An upstream draft changed; choose its reviewed version again")
    physical = upstream_catalog_digest(await upstream_catalog(session, child, parents))
    for plan in [*parents, child]:
        endpoints = await get_etl_layers().transfer_endpoint_columns_in_session(
            session, (SANDBOX, plan.source_table), (SANDBOX, plan.target_table),
        )
        if compare_transfer_endpoints(plan_response(plan), endpoints).status != "compatible":
            raise HTTPException(422, "Every transfer must pass current sandbox structure preflight")
    nested = change.group.operator if change.group else None
    paired = list(change.group.parentPlanIds) if change.group else None
    if (binding is None or not binding.enabled or binding.operator != change.operator or binding.armed_by != actor_id
        or binding.group_operator != nested or binding.group_parent_ids != paired
        or binding.child_updated_at != child.updated_at or binding.physical_digest != physical
        or {member.parent_plan_id: member.parent_updated_at for member, _ in reviewed} != selected):
        # Each producer is locked until its own commit event settles. This
        # generation therefore cannot consume any earlier producer event.
        cutoffs = dict((await session.execute(select(TransferCommitEventRow.parent_plan_id,
                       func.max(TransferCommitEventRow.id)).where(TransferCommitEventRow.parent_plan_id.in_(selected))
                       .group_by(TransferCommitEventRow.parent_plan_id))).all())
        fields = dict(enabled=True, operator=change.operator, group_operator=nested, group_parent_ids=paired, generation=binding.generation + 1 if binding else 1,
                      armed_by=actor_id, child_updated_at=child.updated_at, physical_digest=physical, updated_at=_utc_now())
        if binding is None:
            binding = UpstreamTriggerRow(plan_id=plan_id, **fields)
            session.add(binding)
            await session.flush()
        else:
            for field, value in fields.items():
                setattr(binding, field, value)
            await session.execute(delete(UpstreamTriggerParentRow).where(UpstreamTriggerParentRow.plan_id == plan_id))
        reviewed = [(UpstreamTriggerParentRow(plan_id=plan_id, parent_plan_id=parent.id,
                     parent_updated_at=parent.updated_at, cutoff_event_id=cutoffs.get(parent.id, 0)), parent)
                    for parent in parents]
        session.add_all([member for member, _ in reviewed])
    result = _response(child, binding, reviewed)
    await session.commit()
    return result


async def list_upstream_triggers(session: AsyncSession) -> list[UpstreamTriggerSummary]:
    statement = (select(TransferPlanRow, UpstreamTriggerRow)
                 .join(UpstreamTriggerRow, UpstreamTriggerRow.plan_id == TransferPlanRow.id)
                 .order_by(UpstreamTriggerRow.updated_at.desc(), TransferPlanRow.id).limit(_LIMIT))
    rows = (await session.execute(statement)).all()
    reviewed = await _reviewed_parents(session, [child.id for child, _ in rows])
    return [UpstreamTriggerSummary(planId=child.id, planName=child.name, sourceSchema=child.source_schema,
                                   sourceTable=child.source_table, targetSchema=child.target_schema,
                                   targetTable=child.target_table,
                                   **_response(child, binding, reviewed.get(child.id, [])).model_dump())
            for child, binding in rows]


async def list_upstream_jobs(session: AsyncSession, plan_id: UUID) -> list[UpstreamTriggerJob]:
    await _need_plan(session, plan_id)
    statement = (select(UpstreamTriggerJobRow).where(UpstreamTriggerJobRow.plan_id == plan_id)
                 .order_by(UpstreamTriggerJobRow.event_id.desc(), UpstreamTriggerJobRow.generation.desc())
                 .limit(HISTORY_LIMIT))
    jobs = (await session.scalars(statement)).all()
    if not jobs:
        return []
    link = UpstreamTriggerJobEventRow
    bounded = select(link.job_id, link.event_id, func.row_number().over(
        partition_by=link.job_id, order_by=link.event_id,
    ).label("position")).where(link.job_id.in_([job.id for job in jobs])).subquery()
    event_query = (select(bounded.c.job_id, TransferCommitEventRow)
                   .join(TransferCommitEventRow, TransferCommitEventRow.id == bounded.c.event_id)
                   .where(bounded.c.position <= 4).order_by(TransferCommitEventRow.id))
    events = {}
    for job_id, event in (await session.execute(event_query)).all():
        events.setdefault(job_id, []).append(event)
    output = []
    for job in jobs:
        chosen = events.get(job.id, [])
        if job.group_operator is not None and (
            not exact_nested_job(job, chosen) or not await positive_nested_events(session, chosen)
        ):
            continue
        if job.group_operator is None and not valid_witness(
            job.operator, None, None, [], [event.parent_plan_id for event in chosen], historic_flat=True,
        ):
            continue
        group = canonical_group(job.operator, job.group_operator, job.group_parent_ids, job.rule_parent_ids or [])
        output.append(UpstreamTriggerJob(
            id=job.id, operator=job.operator, group=group, ruleParentPlanIds=job.rule_parent_ids,
            events=[UpstreamTriggerEvent(eventId=str(event.id), eventRecordedAt=event.recorded_at,
                                         parentReceiptId=event.parent_receipt_id, parentPlanId=event.parent_plan_id)
                    for event in chosen],
            state=job.state, receiptId=job.receipt_id, dispatchedAt=job.dispatched_at,
            finishedAt=job.finished_at, message=job.message,
        ))
    return output

# Lock each ready binding before selecting events. The lock serializes pollers
# and arm/pause; the membership ledger independently enforces non-reuse.
_READY_BINDINGS = text("""
    SELECT t.plan_id
    FROM ontology_transfer_upstream_triggers t
    JOIN ontology_transfer_plans child ON child.id = t.plan_id
    CROSS JOIN LATERAL (
        SELECT count(*) AS members, count(e.id) AS ready, min(e.id) AS oldest, max(e.recorded_at) AS latest,
               bool_and(p.updated_at = m.parent_updated_at) AS unchanged,
               count(*) FILTER (WHERE m.parent_plan_id = ANY(t.group_parent_ids)) AS grouped_members,
               count(e.id) FILTER (WHERE m.parent_plan_id = ANY(t.group_parent_ids)) AS grouped_ready,
               array_agg(e.id ORDER BY m.parent_plan_id) FILTER (WHERE m.parent_plan_id = ANY(t.group_parent_ids)) AS pair_first,
               array_agg(f.id ORDER BY m.parent_plan_id) FILTER (WHERE m.parent_plan_id = ANY(t.group_parent_ids)) AS pair_fresh,
               max(e.id) FILTER (WHERE m.parent_plan_id = ANY(t.group_parent_ids)) AS group_anchor,
               min(e.id) FILTER (WHERE m.parent_plan_id = ANY(t.group_parent_ids)) AS alternate_first,
               min(f.id) FILTER (WHERE m.parent_plan_id = ANY(t.group_parent_ids)) AS alternate_fresh,
               min(e.id) FILTER (WHERE NOT (m.parent_plan_id = ANY(t.group_parent_ids))) AS single_first,
               min(f.id) FILTER (WHERE NOT (m.parent_plan_id = ANY(t.group_parent_ids))) AS single_fresh
        FROM ontology_transfer_upstream_parents m
        JOIN ontology_transfer_plans p ON p.id = m.parent_plan_id
        LEFT JOIN LATERAL (
            SELECT e.id, e.recorded_at FROM ontology_transfer_commit_events e
            WHERE e.parent_plan_id = m.parent_plan_id AND e.parent_updated_at = m.parent_updated_at
              AND e.id > m.cutoff_event_id AND e.recorded_at <= :now
              AND NOT EXISTS (
                  SELECT 1 FROM ontology_transfer_upstream_job_events j
                  WHERE j.plan_id = t.plan_id AND j.generation = t.generation AND j.event_id = e.id
              )
            ORDER BY CASE WHEN t.group_operator IS NULL AND t.operator = 'OR'
                               AND e.recorded_at <= :window_start THEN 1 ELSE 0 END, e.id
            LIMIT 1
        ) e ON true
        LEFT JOIN LATERAL (
            SELECT fresh.id FROM ontology_transfer_commit_events fresh
            WHERE t.group_operator IS NOT NULL AND fresh.parent_plan_id = m.parent_plan_id
              AND fresh.parent_updated_at = m.parent_updated_at AND fresh.id > m.cutoff_event_id
              AND fresh.recorded_at > :window_start AND fresh.recorded_at <= :now
              AND NOT EXISTS (
                  SELECT 1 FROM ontology_transfer_upstream_job_events j
                  WHERE j.plan_id = t.plan_id AND j.generation = t.generation AND j.event_id = fresh.id
              )
            ORDER BY fresh.id LIMIT 1
        ) f ON true
        WHERE m.plan_id = t.plan_id
    ) c
    CROSS JOIN LATERAL (
        SELECT CASE WHEN c.grouped_ready = 2 THEN least(
                    CASE WHEN c.pair_fresh[1] IS NOT NULL THEN greatest(c.pair_fresh[1], c.pair_first[2]) END,
                    CASE WHEN c.pair_fresh[2] IS NOT NULL THEN greatest(c.pair_first[1], c.pair_fresh[2]) END
                ) END AS pair_live,
               CASE WHEN c.single_first IS NOT NULL AND c.grouped_ready > 0 THEN least(
                    CASE WHEN c.single_fresh IS NOT NULL THEN greatest(c.single_fresh, c.alternate_first) END,
                    CASE WHEN c.alternate_fresh IS NOT NULL THEN greatest(c.single_first, c.alternate_fresh) END
                ) END AS common_live
    ) choices
    CROSS JOIN LATERAL (
        SELECT CASE WHEN t.group_operator IS NULL THEN c.latest > :window_start
                    WHEN t.operator = 'OR' THEN least(choices.pair_live, c.single_fresh) IS NOT NULL
                    ELSE choices.common_live IS NOT NULL END AS live,
               CASE WHEN t.group_operator IS NULL THEN c.oldest
                    WHEN t.operator = 'OR' THEN coalesce(least(choices.pair_live, c.single_fresh),
                        least(CASE WHEN c.grouped_ready = 2 THEN c.group_anchor END, c.single_first))
                    ELSE coalesce(choices.common_live, greatest(c.single_first, c.alternate_first)) END AS priority
    ) witness
    WHERE t.enabled AND child.updated_at = t.child_updated_at AND c.unchanged
      AND CASE WHEN t.group_operator IS NULL THEN c.members BETWEEN 1 AND 3
                    AND CASE t.operator WHEN 'AND' THEN c.ready = c.members ELSE c.ready > 0 END
               ELSE c.members = 3 AND c.grouped_members = 2 AND
                    CASE t.operator WHEN 'OR' THEN c.grouped_ready = 2 OR c.single_first IS NOT NULL
                         ELSE c.single_first IS NOT NULL AND c.grouped_ready > 0 END END
    ORDER BY witness.live IS NOT TRUE, witness.priority, t.plan_id
    LIMIT :limit FOR UPDATE OF t SKIP LOCKED
""")


def _nested_job_sets(binding: UpstreamTriggerRow, choices: dict, now: datetime, budget: int):
    group = tuple(binding.group_parent_ids)
    common, = set(choices) - set(group)
    window = now - _WINDOW
    def earliest(values):
        if not values:
            return []
        first = values[0]
        fresh = next((event for event in values if event.recorded_at > window), None)
        return [first, fresh] if fresh is not None and fresh.id != first.id else [first]

    selected = []
    for _ in range(budget):
        single = earliest(choices[common])
        left, right = (earliest(choices[parent]) for parent in group)
        if binding.operator == "OR":
            candidates = [[event] for event in single] + [list(pair) for pair in product(left, right)]
        else:
            candidates = [list(pair) for pair in product(single, [*left, *right])]
        if not candidates:
            break
        witness = min(candidates, key=lambda events: (
            max(event.recorded_at for event in events) <= window,
            max(event.id for event in events), tuple(sorted(event.id for event in events)),
        ))
        selected.append(witness)
        for event in witness:
            choices[event.parent_plan_id].remove(event)
    return selected


async def _select_job_sets(
    session: AsyncSession, binding: UpstreamTriggerRow, reviewed: list[tuple[UpstreamTriggerParentRow, TransferPlanRow]],
    now: datetime, budget: int,
) -> list[list[TransferCommitEventRow]]:
    events = TransferCommitEventRow
    by_parent = []
    nested_choices = {}
    for member, _ in reviewed:
        waiting = select(events).where(
            events.parent_plan_id == member.parent_plan_id, events.parent_updated_at == member.parent_updated_at,
            events.id > member.cutoff_event_id, events.recorded_at <= now,
            ~exists().where(UpstreamTriggerJobEventRow.plan_id == binding.plan_id,
                            UpstreamTriggerJobEventRow.generation == binding.generation,
                            UpstreamTriggerJobEventRow.event_id == events.id),
        ).order_by(events.id)
        if binding.group_operator is not None:
            fifo = (await session.scalars(waiting.limit(budget))).all()
            fresh = (await session.scalars(waiting.where(events.recorded_at > now - _WINDOW).limit(budget))).all()
            nested_choices[member.parent_plan_id] = sorted(
                {event.id: event for event in [*fifo, *fresh]}.values(), key=lambda event: event.id,
            )
        elif binding.operator == "AND":
            by_parent.append((await session.scalars(waiting.limit(budget))).all())
        else:
            window_start = now - _WINDOW
            fresh = list((await session.scalars(waiting.where(events.recorded_at > window_start).limit(budget))).all())
            if len(fresh) < budget:
                fresh += (await session.scalars(waiting.where(events.recorded_at <= window_start)
                                               .limit(budget - len(fresh)))).all()
            by_parent.append(fresh)
    if nested_choices:
        return _nested_job_sets(binding, nested_choices, now, budget)
    if binding.operator == "AND":
        return [list(group) for group in zip(*by_parent)]
    ordered = sorted((event for matches in by_parent for event in matches),
                     key=lambda event: (event.recorded_at + _WINDOW <= now, event.id))
    return [[event] for event in ordered[:budget]]


async def _admit_upstream_jobs(session: AsyncSession, now: datetime) -> None:
    ids = list((await session.execute(_READY_BINDINGS, {"now": now, "window_start": now - _WINDOW,
                                                        "limit": _LIMIT})).scalars().all())
    if not ids:
        return
    reviewed = await _reviewed_parents(session, ids)
    # Give every reserved binding a turn; one active binding can still fill
    # the entire per-tick work budget when nothing else is ready.
    remaining = _LIMIT
    for index, plan_id in enumerate(ids):
        binding = await session.get(UpstreamTriggerRow, plan_id, populate_existing=True)
        child = await session.get(TransferPlanRow, plan_id, populate_existing=True)
        members = reviewed.get(plan_id, [])
        if binding is None or child is None or not _response(child, binding, members).enabled:
            continue
        sets = await _select_job_sets(session, binding, members, now, remaining // (len(ids) - index))
        for events in sets:
            anchor = max(event.id for event in events)
            deadline = max(event.recorded_at for event in events) + _WINDOW
            missed = deadline <= now
            rule_parents = sorted(parent.parent_plan_id for parent, _ in members) if binding.group_operator else None
            job_id = witness_id(
                binding.plan_id, binding.generation, [event.id for event in events], root=binding.operator,
                nested=binding.group_operator, pair=binding.group_parent_ids, parents=rule_parents,
            )
            await session.execute(insert(UpstreamTriggerJobRow).values(
                id=job_id, plan_id=binding.plan_id, generation=binding.generation, event_id=anchor,
                operator=binding.operator, group_operator=binding.group_operator, group_parent_ids=binding.group_parent_ids,
                rule_parent_ids=rule_parents,
                child_updated_at=binding.child_updated_at, armed_by=binding.armed_by,
                expires_at=deadline, state="expired" if missed else "pending", dispatch_after=now,
                finished_at=now if missed else None, message="The event dispatch window closed before dispatch" if missed else None,
            ))
            await session.execute(insert(UpstreamTriggerJobEventRow).values([
                dict(job_id=job_id, plan_id=binding.plan_id, generation=binding.generation, event_id=event.id)
                for event in events
            ]))
        remaining -= len(sets)


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

    await _admit_upstream_jobs(session, now)
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
