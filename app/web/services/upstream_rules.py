"""Pure, shared witness semantics for flat and depth-two upstream rules."""

from collections.abc import Collection
from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.transfer_plans import TransferRunRow
from app.web.api.models.transfer_upstream import TransferCommitEventRow, UpstreamGroup, UpstreamTriggerJobRow

EVENT_WINDOW = timedelta(minutes=5)

_SYSTEM_ACTORS = {uuid5(NAMESPACE_URL, f"asiral:transfer-{name}:local") for name in ("scheduler", "upstream")}


def canonical_group(root: str, nested: str | None, pair: list[UUID] | None,
                    parents: Collection[UUID]) -> UpstreamGroup | None:
    if nested is None and pair is None:
        return None
    if (root not in ("AND", "OR") or nested not in ("AND", "OR") or nested == root
        or pair is None or len(pair) != 2 or len(set(pair)) != 2 or pair != sorted(pair)
        or len(parents) != 3 or len(set(parents)) != 3 or not set(pair).issubset(parents)):
        raise ValueError("Invalid persisted nested upstream rule")
    return UpstreamGroup(operator=nested, parentPlanIds=tuple(pair))


def valid_witness(root: str, nested: str | None, pair: list[UUID] | None,
                  parents: Collection[UUID], chosen: list[UUID], *, historic_flat: bool = False) -> bool:
    if root not in ("AND", "OR") or not chosen or len(chosen) != len(set(chosen)):
        return False
    try:
        group = canonical_group(root, nested, pair, parents)
    except ValueError:
        return False
    if group is None:
        if historic_flat:
            return len(chosen) <= 3 if root == "AND" else len(chosen) == 1
        return (set(chosen) == set(parents) if root == "AND"
                else len(chosen) == 1 and chosen[0] in parents)
    included = set(chosen)
    members = set(group.parentPlanIds)
    single = set(parents) - members
    if root == "OR":
        return included in (members, single)
    return len(included) == 2 and single.issubset(included) and len(included & members) == 1


def witness_id(
    plan_id: UUID, generation: int, ids: list[int], *, root: str | None = None, nested: str | None = None,
    pair: list[UUID] | None = None, parents: list[UUID] | None = None,
) -> UUID:
    key = ":".join(str(identity) for identity in sorted(ids))
    prefix = f"asiral:upstream:{plan_id}:{generation}:{key}"
    if nested is not None:
        prefix += f":nested:{root}:{nested}:" + ",".join(map(str, pair)) + ":" + ",".join(map(str, parents))
    return uuid5(NAMESPACE_URL, prefix)


def exact_nested_job(job: UpstreamTriggerJobRow, events: list[TransferCommitEventRow]) -> bool:
    return (job.rule_parent_ids is not None and job.rule_parent_ids == sorted(job.rule_parent_ids)
            and valid_witness(job.operator, job.group_operator, job.group_parent_ids, job.rule_parent_ids,
                              [event.parent_plan_id for event in events])
            and max(event.id for event in events) == job.event_id
            and witness_id(
                job.plan_id, job.generation, [event.id for event in events], root=job.operator,
                nested=job.group_operator, pair=job.group_parent_ids, parents=job.rule_parent_ids,
            ) == job.id
            and max(event.recorded_at for event in events) + EVENT_WINDOW == job.expires_at)


async def positive_nested_events(
    session: AsyncSession, events: list[TransferCommitEventRow], *, parents_by_id=None,
) -> bool:
    """The selected witness must link to exact immutable positive manual parent receipts."""
    receipts = {row.id: row for row in (await session.scalars(select(TransferRunRow).where(
        TransferRunRow.id.in_({event.parent_receipt_id for event in events}),
    ))).all()}
    return all(
        (row := receipts.get(event.parent_receipt_id)) is not None
        and row.plan_id == event.parent_plan_id and row.plan_updated_at == event.parent_updated_at
        and row.recorded_at == event.recorded_at and row.write_mode == "upsert" and row.rows_affected > 0
        and row.initiated_by not in _SYSTEM_ACTORS and row.source_schema == row.target_schema == "ts_transfer_sandbox"
        and (row.target_oid, row.target_filenode) == (event.target_oid, event.target_node)
        and (parents_by_id is None or ((parent := parents_by_id.get(event.parent_plan_id)) is not None
             and (row.source_table, row.target_table) == (parent.source_table, parent.target_table)))
        for event in events
    )
