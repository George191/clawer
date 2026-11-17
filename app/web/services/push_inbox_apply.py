"""Manual, idempotent apply into one pinned synthetic append-only sink."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.push_inbox import RECENT_LIMIT
from app.web.api.models.push_inbox_apply import (
    MAX_PUSH_APPLY_EVENTS, MAX_PUSH_EMPTY_CHECKS, PUSH_APPLY_SCHEMA, PUSH_APPLY_TARGET,
    PushApplyBindingRow, PushApplyRequest, PushApplyResult, PushApplyResultRow, PushApplyStatus,
)
from app.web.core.config import settings
from app.web.services.push_inbox import _state, configured, inbox_opted_in
from app.web.services.transfer_runs import _require_index_range_plan

_SOURCE_NAME = "ontology_push_inbox_receipts"
_SOURCE = f'"public"."{_SOURCE_NAME}"'
_TARGET = f'"{PUSH_APPLY_SCHEMA}"."{PUSH_APPLY_TARGET}"'
_SOURCE_SHAPE = {
    "id": "uuid", "request_id": "uuid", "ordinal": "bigint", "body_digest": "character varying(64)",
    "schema_version": "integer", "entity_id": "bigint", "fixture_value": "character varying(56)",
    "accepted_at": "timestamp with time zone",
}
_TARGET_SHAPE = {"ordinal": "bigint", "acceptance_id": "uuid", "entity_id": "bigint", "fixture_value": "text"}
_RELATION = text("""
    SELECT c.oid::bigint, pg_catalog.pg_relation_filenode(c.oid)::bigint
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_catalog.pg_am am ON am.oid=c.relam AND am.amname='heap'
    WHERE n.nspname=:schema AND c.relname=:name AND c.relkind='r' AND c.relpersistence='p'
      AND NOT c.relispartition AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity AND NOT c.relhasrules
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE c.oid IN (i.inhrelid,i.inhparent))
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid)
      AND (NOT :sink OR (SELECT count(*) FROM pg_catalog.pg_index i WHERE i.indrelid=c.oid)=1)
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k WHERE k.conrelid=c.oid
                      AND k.contype NOT IN ('n','p','u') AND (:sink OR k.contype<>'c'))
""")
_COLUMNS = text("""
    SELECT attname::text, pg_catalog.format_type(atttypid,atttypmod), attnotnull AND attgenerated='' AND attidentity=''
    FROM pg_catalog.pg_attribute
    WHERE attrelid=CAST(:oid AS oid) AND attnum>0 AND NOT attisdropped LIMIT 9
""")
_INDEX = text("""
    SELECT ic.relname::text
    FROM pg_catalog.pg_index i
    JOIN pg_catalog.pg_class ic ON ic.oid=i.indexrelid
    JOIN pg_catalog.pg_am am ON am.oid=ic.relam AND am.amname='btree'
    JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attname='ordinal'
    JOIN pg_catalog.pg_opclass op ON op.oid=i.indclass[0]
    WHERE i.indrelid=CAST(:oid AS oid) AND i.indisvalid AND i.indisready AND i.indislive
      AND i.indisunique AND i.indimmediate AND (NOT :sink OR i.indisprimary)
      AND i.indnatts=1 AND i.indkey[0]=a.attnum AND i.indexprs IS NULL AND i.indpred IS NULL AND i.indoption[0] & 1 = 0
      AND op.opcname='int8_ops' AND op.opcnamespace='pg_catalog'::regnamespace
    LIMIT 1
""")
_RANGE = (f"FROM ONLY {_SOURCE} WHERE ordinal > :start AND ordinal <= :end ORDER BY ordinal "
          f"LIMIT {MAX_PUSH_APPLY_EVENTS + 1}")
_PREFIX = ("SELECT ordinal, schema_version=1 AND entity_id>0 AND fixture_value ~ '^fixture-[a-z0-9-]{1,48}$' AS valid "
           + _RANGE)
_COPY = "SELECT ordinal, id, entity_id, fixture_value " + _RANGE


@dataclass(frozen=True)
class _Pin:
    oid: int
    filenode: int
    ordinal_index: str


def apply_opted_in() -> bool:
    return settings.PUSH_INBOX_SANDBOX_APPLY_ENABLED and inbox_opted_in()


def _result(row: PushApplyResultRow) -> PushApplyResult:
    return PushApplyResult(
        id=row.id, status=row.outcome, fromExclusive=str(row.from_exclusive), throughInclusive=str(row.through_inclusive),
        reviewedAcceptedThrough=str(row.reviewed_accepted_through), eventsApplied=row.events_applied,
        checkedAt=row.recorded_at, appliedAt=row.recorded_at if row.outcome == "applied" else None,
        hasMoreReviewed=row.through_inclusive < row.reviewed_accepted_through,
    )


async def _limits(session: AsyncSession, *, read_only: bool = False) -> None:
    if read_only:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL search_path = pg_catalog, public, pg_temp"))
    await session.execute(text("SET LOCAL statement_timeout='1500ms'"))
    await session.execute(text("SET LOCAL lock_timeout='500ms'"))


async def _pin(session: AsyncSession, *, sink: bool) -> _Pin | None:
    schema, name, shape = (PUSH_APPLY_SCHEMA, PUSH_APPLY_TARGET, _TARGET_SHAPE) if sink else (
        "public", _SOURCE_NAME, _SOURCE_SHAPE,
    )
    relation = (await session.execute(_RELATION, {"schema": schema, "name": name, "sink": sink})).one_or_none()
    if relation is None or relation[1] is None:
        return None
    attrs = (await session.execute(_COLUMNS, {"oid": relation[0]})).all()
    if {key: sql_type for key, sql_type, ordinary in attrs if ordinary} != shape or len(attrs) != len(shape):
        return None
    index = await session.scalar(_INDEX, {"oid": relation[0], "sink": sink})
    return _Pin(relation[0], relation[1], index) if index is not None else None


def _same_binding(binding: PushApplyBindingRow, source: _Pin | None, target: _Pin | None) -> bool:
    return bool(source and target and (source.oid, source.filenode, target.oid, target.filenode) == (
        binding.source_oid, binding.source_filenode, binding.target_oid, binding.target_filenode,
    ))


async def _binding(session: AsyncSession, *, lock: bool) -> PushApplyBindingRow | None:
    query = select(PushApplyBindingRow).where(PushApplyBindingRow.singleton.is_(True))
    return await session.scalar(query.with_for_update() if lock else query)


async def _locked_pins(session: AsyncSession) -> tuple[_Pin, _Pin]:
    # The acceptance state and apply binding have already been locked in that order.
    # Check kinds first so malformed replacements cannot route the actual LOCK to views/foreign tables.
    before_target, before_source = await _pin(session, sink=True), await _pin(session, sink=False)
    if before_target is None or before_source is None:
        raise HTTPException(409, "The exact synthetic inbox relations are not eligible")
    await session.execute(text(f"LOCK TABLE ONLY {_TARGET} IN SHARE ROW EXCLUSIVE MODE NOWAIT"))
    await session.execute(text(f"LOCK TABLE ONLY {_SOURCE} IN SHARE MODE NOWAIT"))
    target, source = await _pin(session, sink=True), await _pin(session, sink=False)
    if target != before_target or source != before_source:
        raise HTTPException(409, "The exact synthetic inbox relations changed while being reviewed")
    return source, target


async def _head(session: AsyncSession, relation: str, pin: _Pin) -> int:
    query = f"SELECT ordinal FROM ONLY {relation} ORDER BY ordinal DESC LIMIT 1"
    await _require_index_range_plan(session, query, {}, pin.ordinal_index, ranged=False)
    result = await session.scalar(text(query))
    if result is not None and result < 1:
        raise HTTPException(409, "The exact synthetic inbox contains an invalid ordinal")
    return result if result is not None else 0


def _require_apply() -> None:
    if not apply_opted_in() or not configured():
        raise HTTPException(403, "Synthetic Push apply is disabled or not provisioned")


async def read_push_apply(session: AsyncSession) -> PushApplyStatus:
    if not apply_opted_in():
        return PushApplyStatus(state="disabled", available=False)
    if not configured():
        return PushApplyStatus(state="unconfigured", available=False)
    await _limits(session, read_only=True)
    state = await _state(session, lock=False)
    if state is None:
        return PushApplyStatus(state="unconfigured", available=False)
    binding = await _binding(session, lock=False)
    if binding is not None and state.database_marker != binding.database_marker:
        return PushApplyStatus(state="blocked", available=True)
    source, target = await _pin(session, sink=False), await _pin(session, sink=True)
    if binding is None:
        return PushApplyStatus(
            state="unreviewed" if source and target else "blocked", available=True, acceptedThrough=str(state.accepted_count),
        )
    recent = (await session.scalars(select(PushApplyResultRow).where(
        PushApplyResultRow.database_marker == binding.database_marker,
        PushApplyResultRow.generation == binding.generation,
    ).order_by(PushApplyResultRow.recorded_at.desc(), PushApplyResultRow.id.desc()).limit(RECENT_LIMIT))).all()
    ready = _same_binding(binding, source, target) and binding.applied_through <= state.accepted_count
    return PushApplyStatus(
        state="ready" if ready else "blocked", available=True, canReplay=True, generation=1,
        acceptedThrough=str(state.accepted_count), appliedThrough=str(binding.applied_through), reviewedAt=binding.reviewed_at,
        recent=[_result(row) for row in recent],
    )


async def review_push_apply(session: AsyncSession, actor: UUID) -> PushApplyStatus:
    _require_apply()
    async with session.begin():
        await _limits(session)
        state = await _state(session, lock=True)
        if state is None:
            raise HTTPException(403, "Synthetic Push apply is disabled or not provisioned")
        if await _binding(session, lock=True) is not None:
            raise HTTPException(409, "The existing synthetic apply binding cannot be reset or reviewed again")
        source, target = await _locked_pins(session)
        await session.execute(text("SET LOCAL enable_seqscan=off"))
        if await _head(session, _SOURCE, source) != state.accepted_count:
            raise HTTPException(409, "The accepted inbox head does not match its immutable receipts")
        # An ordinal of 0 (or below) is also a nonempty table, not an available destination.
        empty_query = f"SELECT ordinal FROM ONLY {_TARGET} ORDER BY ordinal LIMIT 1"
        await _require_index_range_plan(session, empty_query, {}, target.ordinal_index, ranged=False)
        if await session.scalar(text(empty_query)) is not None:
            raise HTTPException(409, "The exact synthetic target must be empty when first reviewed")
        at = await session.scalar(text("SELECT pg_catalog.clock_timestamp()"))
        session.add(PushApplyBindingRow(
            database_marker=state.database_marker, source_oid=source.oid, source_filenode=source.filenode,
            target_oid=target.oid, target_filenode=target.filenode, reviewed_by=actor, reviewed_at=at,
        ))
        return PushApplyStatus(state="ready", available=True, canReplay=True, generation=1,
                               acceptedThrough=str(state.accepted_count), appliedThrough="0", reviewedAt=at)


async def _append(session: AsyncSession, index: str, start: int, end: int) -> int:
    params = {"start": start, "end": end}
    await _require_index_range_plan(session, _PREFIX, params, index, ranged=True)
    source = (await session.execute(text(_PREFIX), params)).all()
    ordinals = list(range(start + 1, end + 1))
    if [row.ordinal for row in source] != ordinals or not all(row.valid for row in source):
        raise HTTPException(409, "The reviewed acceptance range is incomplete or invalid")
    # Server-side exact range copy avoids handling or logging the payload values as application parameters.
    await _require_index_range_plan(session, _COPY, params, index, ranged=True)
    written = (await session.scalars(text(f"""
        INSERT INTO {_TARGET}(ordinal,acceptance_id,entity_id,fixture_value)
        {_COPY} RETURNING ordinal
    """), params)).all()
    if sorted(written) != ordinals:
        raise HTTPException(409, "The target did not acknowledge the full reviewed range")
    return len(written)


async def apply_push_range(session: AsyncSession, request: PushApplyRequest, actor: UUID) -> PushApplyResult:
    _require_apply()
    start, upper = int(request.expectedAppliedThrough), int(request.expectedAcceptedThrough)
    async with session.begin():
        await _limits(session)
        state = await _state(session, lock=True)
        if state is None:
            raise HTTPException(403, "Synthetic Push apply is disabled or not provisioned")
        binding = await _binding(session, lock=True)
        if binding is None:
            raise HTTPException(409, "Review the fixed synthetic target before applying")
        if binding.database_marker != state.database_marker:
            raise HTTPException(403, "Synthetic Push apply is disabled or not provisioned")
        before = await session.scalar(select(PushApplyResultRow).where(PushApplyResultRow.request_id == request.requestId))
        if before is not None:
            if (before.database_marker != binding.database_marker or before.generation != request.expectedGeneration
                or before.from_exclusive != start or before.reviewed_accepted_through != upper):
                raise HTTPException(409, "This request ID was admitted for another reviewed range")
            return _result(before)
        if (binding.generation != request.expectedGeneration or start != binding.applied_through
            or not start <= upper <= state.accepted_count):
            raise HTTPException(409, "Review the current accepted and applied ordinals before applying")
        if upper == start and state.accepted_count != start:
            raise HTTPException(409, "New accepted events exist beyond the reviewed empty range; reload before checking")
        source, target = await _locked_pins(session)
        if not _same_binding(binding, source, target):
            raise HTTPException(409, "The reviewed synthetic inbox relations were rewritten; no reset is allowed")
        await session.execute(text("SET LOCAL enable_seqscan=off"))
        if (await _head(session, _SOURCE, source) != state.accepted_count
            or await _head(session, _TARGET, target) != binding.applied_through):
            raise HTTPException(409, "The accepted or applied head does not match its durable ordinal")
        end = min(start + MAX_PUSH_APPLY_EVENTS, upper)
        if end == start:
            if binding.empty_checks >= MAX_PUSH_EMPTY_CHECKS:
                raise HTTPException(429, "The synthetic no-data check limit has been reached")
            binding.empty_checks += 1
            outcome, count = "no_new_data", 0
        else:
            outcome, count = "applied", await _append(session, source.ordinal_index, start, end)
            binding.applied_through = end
        at = await session.scalar(text("SELECT pg_catalog.clock_timestamp()"))
        result = PushApplyResultRow(
            request_id=request.requestId, database_marker=binding.database_marker, generation=binding.generation,
            outcome=outcome, from_exclusive=start, through_inclusive=end, reviewed_accepted_through=upper,
            events_applied=count, recorded_by=actor, recorded_at=at,
        )
        session.add(result)
        await session.flush()
        return _result(result)
