"""Conservative mixed Daily UTC and positive witness decisions on the existing upstream collector."""

from datetime import datetime, timedelta
from itertools import combinations

from fastapi import HTTPException
from sqlalchemy import case, exists, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.transfer_plans import TransferPlanRow, TransferRunRow
from app.web.api.models.transfer_schedules import TransferScheduleRow
from app.web.api.models.transfer_upstream import (
    TransferCommitEventRow, UpstreamMixedSlotRow, UpstreamTriggerJobEventRow, UpstreamTriggerJobRow,
    UpstreamTriggerParentRow, UpstreamTriggerRow,
)
from app.web.api.models.user import User
from app.web.api.utils.models import StatusTypes
from app.web.services import upstream_mixed_rules as mixed
from app.web.services.transfer_runs import (
    SCHEDULED_SYSTEM_ACTOR, UPSTREAM_SYSTEM_ACTOR, is_sandbox_upsert, upstream_catalog,
    upstream_catalog_digest, upstream_pair_eligible,
)
from app.web.services.upstream_rules import EVENT_WINDOW, positive_nested_events, valid_witness

_MIXED_LIMIT = 24


async def _eligible_policy(session, binding):
    child = await session.get(TransferPlanRow, binding.plan_id, populate_existing=True)
    schedule = await session.get(TransferScheduleRow, binding.plan_id, populate_existing=True)
    if (child is None or not is_sandbox_upsert(child, "upstream") or child.updated_at != binding.child_updated_at
        or (schedule is not None and schedule.enabled)):
        return None
    reviewed = (await session.execute(select(UpstreamTriggerParentRow, TransferPlanRow)
                .join(TransferPlanRow, TransferPlanRow.id == UpstreamTriggerParentRow.parent_plan_id)
                .where(UpstreamTriggerParentRow.plan_id == child.id)
                .order_by(UpstreamTriggerParentRow.parent_plan_id).limit(4))).all()
    if (not 1 <= len(reviewed) <= 3 or len({parent.source_table for _, parent in reviewed}) != len(reviewed)
        or any(not upstream_pair_eligible(child, parent) or parent.updated_at != member.parent_updated_at
               for member, parent in reviewed)):
        return None
    actor = await session.scalar(select(User.id).where(
        User.id == binding.armed_by, User.status == StatusTypes.ENABLE, User.is_superuser.is_(True),
    ))
    if actor is None:
        return None
    try:
        catalog = await upstream_catalog(session, child, [parent for _, parent in reviewed])
    except HTTPException:
        return None
    return (reviewed, catalog) if upstream_catalog_digest(catalog) == binding.physical_digest else None


def _event_matches(event, member, parent, catalog, lower, upper) -> bool:
    source, target = catalog[parent.source_table], catalog[parent.target_table]
    return (event.parent_plan_id == member.parent_plan_id and event.parent_updated_at == member.parent_updated_at
            and event.id > member.cutoff_event_id and lower < event.recorded_at < upper
            and (event.source_oid, event.source_node, event.target_oid, event.target_node)
            == (source["table_oid"], source["table_node"], target["table_oid"], target["table_node"]))


async def _choose_visible_events(session, binding, reviewed, catalog, slot):
    event, receipt, link = TransferCommitEventRow, TransferRunRow, UpstreamTriggerJobEventRow
    lower = mixed.mixed_lower_bound(binding, slot)
    candidates = []
    for member, parent in reviewed:
        source, target = catalog[parent.source_table], catalog[parent.target_table]
        first = await session.scalar(select(event).join(receipt, receipt.id == event.parent_receipt_id).where(
            event.parent_plan_id == member.parent_plan_id, event.parent_updated_at == member.parent_updated_at,
            event.id > member.cutoff_event_id, event.recorded_at > lower, event.recorded_at < slot,
            event.source_oid == source["table_oid"], event.source_node == source["table_node"],
            event.target_oid == target["table_oid"], event.target_node == target["table_node"],
            receipt.plan_id == event.parent_plan_id, receipt.plan_updated_at == event.parent_updated_at,
            receipt.recorded_at == event.recorded_at, receipt.rows_affected > 0, receipt.write_mode == "upsert",
            receipt.initiated_by.not_in((SCHEDULED_SYSTEM_ACTOR, UPSTREAM_SYSTEM_ACTOR)),
            receipt.source_schema == parent.source_schema, receipt.source_table == parent.source_table,
            receipt.target_schema == parent.target_schema, receipt.target_table == parent.target_table,
            receipt.target_oid == event.target_oid, receipt.target_filenode == event.target_node,
            ~exists().where(link.plan_id == binding.plan_id, link.generation == binding.generation, link.event_id == event.id),
        ).order_by(event.recorded_at, event.id).limit(1))
        if first is not None:
            candidates.append(first)
    parents = [member.parent_plan_id for member, _ in reviewed]
    valid = (chosen for size in (1, 2, 3) for chosen in combinations(candidates, size)
             if valid_witness(binding.operator, binding.group_operator, binding.group_parent_ids,
                              parents, [event.parent_plan_id for event in chosen]))
    return min(valid, key=lambda chosen: (max(event.id for event in chosen), tuple(sorted(e.id for e in chosen))),
               default=())


async def _observe(session, binding, slot, policy):
    if policy is None:
        return
    reviewed, catalog = policy
    events = await _choose_visible_events(session, binding, reviewed, catalog, slot)
    if not events:
        return
    # Read the database clock only after all chosen positive commit rows were
    # visible in SQL snapshots. Their pre-commit recorded_at is not this proof.
    observed_at = await mixed.mixed_db_now(session)
    if not slot - mixed.SLOT_OBSERVATION_WINDOW <= observed_at < slot:
        return
    if any(event.recorded_at > observed_at for event in events):
        return
    members = [member for member, _ in reviewed]
    await session.execute(insert(UpstreamMixedSlotRow).values(
        plan_id=binding.plan_id, generation=binding.generation, slot=slot, observed_at=observed_at,
        rule_digest=mixed.mixed_rule_digest(binding, members, slot),
        event_ids=sorted(event.id for event in events), rule_parent_ids=sorted(member.parent_plan_id for member in members),
    ).on_conflict_do_nothing())


async def _still_valid(session, binding, observed, policy):
    if observed is None or policy is None or observed.observed_at is None:
        return False
    reviewed, catalog = policy
    members = {member.parent_plan_id: (member, parent) for member, parent in reviewed}
    if (observed.rule_parent_ids != sorted(members)
        or observed.rule_digest != mixed.mixed_rule_digest(binding, [member for member, _ in reviewed], observed.slot)):
        return False
    event, link = TransferCommitEventRow, UpstreamTriggerJobEventRow
    candidates = (await session.scalars(select(event).where(
        event.id.in_(observed.event_ids),
        ~exists().where(link.plan_id == binding.plan_id, link.generation == binding.generation, link.event_id == event.id),
    ).order_by(event.id))).all()
    low = mixed.mixed_lower_bound(binding, observed.slot)
    return (observed.event_ids == [event.id for event in candidates] and all(
        event.parent_plan_id in members and event.recorded_at <= observed.observed_at
        and _event_matches(event, *members[event.parent_plan_id], catalog, low, observed.slot) for event in candidates
    ) and valid_witness(binding.operator, binding.group_operator, binding.group_parent_ids, members.keys(),
                        [event.parent_plan_id for event in candidates]) and await positive_nested_events(session, candidates, parents_by_id={key: value[1] for key, value in members.items()}))


async def _decide(session, binding, slot, observed, policy):
    valid = await _still_valid(session, binding, observed, policy)
    decided_at = await mixed.mixed_db_now(session)
    if not slot <= decided_at < slot + mixed.SLOT_DECISION_WINDOW:
        return  # no catch-up and no inferred no-witness decision for a missed minute
    if observed is None:
        await session.execute(insert(UpstreamMixedSlotRow).values(
            plan_id=binding.plan_id, generation=binding.generation, slot=slot, decided_at=decided_at,
        ).on_conflict_do_nothing())
        return
    observed.decided_at = decided_at
    if valid:
        job_id = mixed.mixed_witness_id(observed, binding)
        await session.execute(insert(UpstreamTriggerJobRow).values(
            id=job_id, plan_id=binding.plan_id, generation=binding.generation, event_id=max(observed.event_ids),
            operator=binding.operator, group_operator=binding.group_operator, group_parent_ids=binding.group_parent_ids,
            rule_parent_ids=observed.rule_parent_ids if binding.group_operator else None,
            child_updated_at=binding.child_updated_at, armed_by=binding.armed_by, mixed_slot=slot,
            expires_at=slot + EVENT_WINDOW, state="pending", dispatch_after=decided_at,
        ))
        await session.execute(insert(UpstreamTriggerJobEventRow).values([
            dict(job_id=job_id, plan_id=binding.plan_id, generation=binding.generation, event_id=identity)
            for identity in observed.event_ids
        ]))
        observed.job_id = job_id


async def collect_mixed_slot_decisions(session: AsyncSession) -> None:
    clock = await mixed.mixed_db_now(session)
    minute = clock.replace(second=0, microsecond=0)
    slots = {(minute + timedelta(minutes=offset)).strftime("%H:%M"): minute + timedelta(minutes=offset)
             for offset in range(6)}
    due = minute.strftime("%H:%M")
    rule, proof = UpstreamTriggerRow, UpstreamMixedSlotRow
    slot_for_rule = case(slots, value=rule.mixed_time_utc)
    bindings = (await session.scalars(select(rule).where(
        rule.enabled, rule.mixed_time_utc.in_(list(slots)), rule.mixed_time_utc.is_not(None),
        ~exists().where(proof.plan_id == rule.plan_id, proof.generation == rule.generation,
                        proof.slot == slot_for_rule,
                        or_(proof.decided_at.is_not(None), rule.mixed_time_utc != due)),
    ).order_by(slot_for_rule, rule.plan_id)
        .limit(_MIXED_LIMIT).with_for_update(of=rule, skip_locked=True).execution_options(populate_existing=True))).all()
    for binding in bindings:
        slot = slots.get(binding.mixed_time_utc)
        if slot is None or binding.updated_at >= slot:
            continue
        observed = await session.get(proof, (binding.plan_id, binding.generation, slot))
        if observed is not None and observed.decided_at is not None:
            continue
        policy = await _eligible_policy(session, binding)
        if slot == minute:
            await _decide(session, binding, slot, observed, policy)
        elif observed is None:
            await _observe(session, binding, slot, policy)
