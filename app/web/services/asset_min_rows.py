"""Measure only whether a reviewed local fixture reaches a bounded row minimum."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Integer, bindparam, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.asset_freshness import AssetFreshnessPolicyRow, AssetQualityIncidentRow
from app.web.api.models.asset_min_rows import AssetMinRowsReview, AssetMinRowsRow, AssetMinRowsStatus
from app.web.core.config import settings
from app.web.services.asset_collected_stats import _fence_current, _visible
from app.web.services.asset_field_freshness import TABLE, _CLOCK, _LOCK, _TTL, _context, _latest_receipt, _pins
from app.web.services.asset_freshness import _identity, _lock_policy, asset_freshness_enabled
from app.web.services.asset_quality_incidents import (
    acknowledge_quality_incident_locked, list_quality_incidents, observe_quality_incident,
)
from app.web.services.transfer_runs import SANDBOX

_KIND = "minimum_rows"
_REASON = "row_threshold"
_SAMPLE_SQL = "SELECT 1 AS present FROM ONLY ts_transfer_sandbox.field_freshness_fixture AS t ORDER BY t.id ASC LIMIT :n"
_PK = text("""
    SELECT x.oid, pg_catalog.pg_relation_filenode(x.oid), x.relname
    FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class x ON x.oid=i.indexrelid
    JOIN pg_catalog.pg_constraint p ON p.conindid=x.oid AND p.conrelid=i.indrelid AND p.contype='p'
    JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=i.indkey[0]
    JOIN pg_catalog.pg_opclass o ON o.oid=i.indclass[0] AND o.opcmethod=x.relam
    WHERE i.indrelid=CAST(:oid AS oid) AND a.attname='id' AND a.attnum>0 AND a.attnotnull AND NOT a.attisdropped
      AND a.atttypid='pg_catalog.int8'::pg_catalog.regtype AND a.attgenerated=''
      AND p.convalidated AND i.indisprimary AND i.indisunique AND i.indimmediate
      AND i.indisvalid AND i.indisready AND i.indislive AND NOT i.indcheckxmin
      AND i.indnkeyatts=1 AND i.indnatts=1 AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indoption[0]=0
      AND x.relkind='i' AND x.relam=403 AND o.opcdefault AND o.opcintype=a.atttypid
      AND o.opcname='int8_ops' AND o.opcnamespace='pg_catalog'::pg_catalog.regnamespace
""")


def min_rows_enabled() -> bool:
    return settings.TRANSFER_SANDBOX_MIN_ROWS_ENABLED and asset_freshness_enabled()


def _require_target(schema: str, table: str) -> None:
    if (schema, table) != (SANDBOX, TABLE):
        raise HTTPException(404, "Only the exact minimum-row sandbox asset is eligible")


async def _index(session, identity):
    if identity is None:
        return None
    row = (await session.execute(_PK, {"oid": identity[0]})).one_or_none()
    return row if row and row[1] else None


async def read_min_rows(session: AsyncSession, schema: str, table: str) -> AssetMinRowsStatus:
    _require_target(schema, table)
    if not min_rows_enabled():
        return AssetMinRowsStatus(enabled=False)
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
    if not await _visible(session, TABLE):
        return AssetMinRowsStatus(enabled=False)
    policy = await session.get(AssetFreshnessPolicyRow, TABLE)
    plan, identity = await _context(session, policy)
    index = await _index(session, identity) if plan else None
    row = await session.get(AssetMinRowsRow, TABLE)
    info = dict(enabled=True, policyGeneration=policy.generation if index else None,
                generation=row.generation if row else 0, minimumRows=row.minimum_rows if row else None)
    if row is None:
        return AssetMinRowsStatus(**info, reason=None if index else (
            "index_unavailable" if plan else "no_eligible_policy"))
    if not _pins(row, policy, identity, index) or not await _fence_current(session, policy):
        return AssetMinRowsStatus(**info, state="superseded", reason="scope_changed")
    incidents = await list_quality_incidents(session, policy, min_rows_only=True)
    info["incident"] = next(iter(incidents.active or incidents.history), None)
    receipt = await _latest_receipt(session, row, plan)
    if receipt is None:
        return AssetMinRowsStatus(**info, state="unknown", reason="no_verified_receipt")
    if row.checked_at is None or (receipt.id, receipt.recorded_at) != (row.receipt_id, row.receipt_recorded_at):
        return AssetMinRowsStatus(**info, state="unknown", reason="awaiting_measurement")
    now = await session.scalar(_CLOCK)
    valid_until = receipt.recorded_at + _TTL
    if not row.checked_at <= now < valid_until:
        return AssetMinRowsStatus(**info, state="unknown", reason="observation_expired")
    return AssetMinRowsStatus(
        **info, state="healthy" if row.at_least_minimum else "below_minimum",
        reason=None if row.at_least_minimum else _REASON, receiptRecordedAt=receipt.recorded_at,
        checkedAt=row.checked_at, validUntil=valid_until,
    )


async def _write_context(session):
    await session.execute(text("SET LOCAL statement_timeout='1500ms'"))
    await session.execute(text("SET LOCAL lock_timeout='750ms'"))
    if not await _visible(session, TABLE):
        raise HTTPException(404, "The exact sandbox asset is not eligible")
    policy = await _lock_policy(session, TABLE)
    plan, identity = await _context(session, policy, lock_plan=True)
    if plan is None:
        raise HTTPException(409, "Review the current asset freshness policy first")
    await session.execute(_LOCK)
    if await _identity(session, TABLE) != identity:
        raise HTTPException(409, "The sandbox asset changed")
    index = await _index(session, identity)
    if index is None:
        raise HTTPException(409, "An eligible ascending bigint ID primary key is required")
    return policy, identity, index


async def review_min_rows(session, schema, table, request: AssetMinRowsReview):
    _require_target(schema, table)
    if not min_rows_enabled():
        raise HTTPException(403, "Minimum-row checks are disabled")
    policy, identity, index = await _write_context(session)
    old = await session.get(AssetMinRowsRow, TABLE)
    if (request.expectedGeneration, request.policyGeneration) != (old.generation if old else 0, policy.generation):
        raise HTTPException(409, "The minimum-row review changed; reload before continuing")
    if _pins(old, policy, identity, index) and old.minimum_rows == request.minimumRows:
        await session.commit()
        return await read_min_rows(session, schema, table)
    now = await session.scalar(_CLOCK)
    if old:
        await session.execute(update(AssetQualityIncidentRow).where(
            AssetQualityIncidentRow.target_table == TABLE, AssetQualityIncidentRow.kind == _KIND,
            AssetQualityIncidentRow.closed_at.is_(None),
        ).values(closed_at=now, close_reason="policy_reviewed"))
    values = dict(generation=request.expectedGeneration + 1, policy_generation=policy.generation,
                  minimum_rows=request.minimumRows, target_oid=identity[0], target_filenode=identity[1],
                  index_oid=index[0], index_filenode=index[1], reviewed_at=now,
                  at_least_minimum=None, receipt_recorded_at=None, receipt_id=None, checked_at=None)
    if old is None:
        session.add(AssetMinRowsRow(target_table=TABLE, **values))
    else:
        for key, value in values.items():
            setattr(old, key, value)
    await session.commit()
    return await read_min_rows(session, schema, table)


async def acknowledge_min_rows(session, schema, table, incident: UUID, generation: int, actor: UUID):
    _require_target(schema, table)
    if not min_rows_enabled():
        raise HTTPException(403, "Minimum-row checks are disabled")
    policy, identity, index = await _write_context(session)
    row = await session.get(AssetMinRowsRow, TABLE)
    if not _pins(row, policy, identity, index) or row.generation != generation:
        raise HTTPException(409, "The minimum-row review changed; reload the incident")
    result = await acknowledge_quality_incident_locked(
        session, policy, incident, policy.generation, actor, await session.scalar(_CLOCK), min_rows_only=True,
    )
    await session.commit()
    return result


def _verified_plan(document, index):
    if not isinstance(document, list) or len(document) != 1 or not isinstance(document[0], dict):
        return False
    root = document[0].get("Plan")
    if not isinstance(root, dict) or root.get("Node Type") != "Limit" or root.get("Parallel Aware") is not False:
        return False
    children = root.get("Plans")
    if not isinstance(children, list) or len(children) != 1 or not isinstance(children[0], dict):
        return False
    leaf = children[0]
    forbidden = ("Filter", "One-Time Filter", "Index Cond", "Recheck Cond", "Subplan Name", "InitPlan", "SubPlan")
    return (
        leaf.get("Node Type") in ("Index Scan", "Index Only Scan") and leaf.get("Parallel Aware") is False
        and leaf.get("Parent Relationship") == "Outer" and leaf.get("Scan Direction") == "Forward"
        and leaf.get("Index Name") == index[2] and leaf.get("Schema") == SANDBOX and leaf.get("Relation Name") == TABLE
        and leaf.get("Alias") == "t" and "Plans" not in leaf
        and not any(key in node for node in (root, leaf) for key in forbidden)
    )


def _statement(explain: bool):
    prefix = "EXPLAIN (FORMAT JSON, VERBOSE TRUE, ANALYZE FALSE) " if explain else ""
    # Literal-executed typed integer binding intentionally fixes the actual
    # LIMIT in both PostgreSQL statements without forcing the planner.
    return text(prefix + _SAMPLE_SQL).bindparams(bindparam("n", type_=Integer, literal_execute=True))


async def evaluate_min_rows(session: AsyncSession, policy: AssetFreshnessPolicyRow) -> None:
    """Existing minute tick, exact receipt and physical/index fences, no actual count storage."""
    if not min_rows_enabled() or policy.target_table != TABLE:
        return
    row = await session.get(AssetMinRowsRow, TABLE)
    if row is None or row.policy_generation != policy.generation or not await _visible(session, TABLE):
        return
    plan, identity = await _context(session, policy, lock_plan=True)
    if plan is None or identity != (row.target_oid, row.target_filenode) or not 1 <= row.minimum_rows <= 100:
        return
    receipt = await _latest_receipt(session, row, plan)
    if receipt is None or (row.receipt_recorded_at is not None and receipt.recorded_at <= row.receipt_recorded_at):
        return
    await session.execute(_LOCK)
    identity = await _identity(session, TABLE)
    index = await _index(session, identity)
    if not _pins(row, policy, identity, index):
        return
    # Lock before rereading the newest receipt and choosing/executing the ID
    # prefix so analysis, writes and rewrites cannot change the approved path.
    receipt = await _latest_receipt(session, row, plan)
    now = await session.scalar(_CLOCK)
    if (receipt is None or receipt.recorded_at > now or now - receipt.recorded_at >= _TTL
        or (row.receipt_recorded_at is not None and receipt.recorded_at <= row.receipt_recorded_at)
        or (row.checked_at is not None and now <= row.checked_at)):
        return
    bound = {"n": row.minimum_rows}
    if not _verified_plan(await session.scalar(_statement(True), bound), index):
        return
    items = (await session.scalars(_statement(False), bound)).all()
    checked_at = await session.scalar(_CLOCK)
    if checked_at < now or checked_at - receipt.recorded_at >= _TTL or len(items) > row.minimum_rows:
        return
    if any(value != 1 for value in items):
        return
    healthy = len(items) == row.minimum_rows
    row.at_least_minimum, row.receipt_id, row.receipt_recorded_at, row.checked_at = (
        healthy, receipt.id, receipt.recorded_at, checked_at,
    )
    await observe_quality_incident(session, policy, _KIND, checked_at, healthy=healthy,
                                   failure=None if healthy else _REASON)
