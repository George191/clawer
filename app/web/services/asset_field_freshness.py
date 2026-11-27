"""Measure one timestamp, only with a pinned index and a newer settled receipt."""

from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.asset_field_freshness import (
    AssetFieldFreshnessReview, AssetFieldFreshnessRow, AssetFieldFreshnessStatus,
)
from app.web.api.models.asset_freshness import AssetFreshnessPolicyRow, AssetQualityIncidentRow
from app.web.api.models.transfer_plans import TransferPlanRow, TransferRunRow
from app.web.core.config import settings
from app.web.services.asset_collected_stats import _fence_current, _visible
from app.web.services.asset_freshness import _drift, _identity, _lock_policy, asset_freshness_enabled
from app.web.services.asset_quality_incidents import (
    acknowledge_quality_incident_locked, list_quality_incidents, observe_quality_incident,
)
from app.web.services.transfer_runs import SANDBOX

TABLE = "field_freshness_fixture"
_KIND = "field_time"
_TTL = timedelta(minutes=3)
_LOCK = text("LOCK TABLE ONLY ts_transfer_sandbox.field_freshness_fixture IN SHARE MODE NOWAIT")
_CLOCK = text("SELECT clock_timestamp()")
_SAMPLE_SQL = """
    SELECT CASE WHEN pg_catalog.isfinite(source_recorded_at) THEN source_recorded_at END
    FROM ONLY ts_transfer_sandbox.field_freshness_fixture AS fixture ORDER BY source_recorded_at DESC LIMIT 1
"""
_INDEX = text("""
    SELECT x.oid, pg_catalog.pg_relation_filenode(x.oid), x.relname,
           CASE WHEN (i.indoption[0] & 1) = 1 THEN 'Forward' ELSE 'Backward' END
    FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class x ON x.oid=i.indexrelid
    JOIN pg_catalog.pg_am m ON m.oid=x.relam
    JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=i.indkey[0]
    JOIN pg_catalog.pg_opclass o ON o.oid=i.indclass[0]
    JOIN pg_catalog.pg_namespace ns ON ns.oid=o.opcnamespace
    WHERE i.indrelid=CAST(:oid AS oid) AND a.attname='source_recorded_at'
      AND a.atttypid='pg_catalog.timestamptz'::regtype AND a.attnotnull AND NOT a.attisdropped
      AND a.attgenerated='' AND i.indoption[0] IN (0,3)
      AND i.indisvalid AND i.indisready AND i.indislive AND i.indnkeyatts=1 AND i.indnatts=1
      AND i.indpred IS NULL AND i.indexprs IS NULL AND x.relkind='i'
      AND m.amname='btree' AND m.oid=403 AND ns.nspname='pg_catalog' AND o.opcname='timestamptz_ops'
    ORDER BY x.oid LIMIT 1
""")


def field_freshness_enabled() -> bool:
    return settings.TRANSFER_SANDBOX_FIELD_FRESHNESS_ENABLED and asset_freshness_enabled()


def _require_target(schema, table):
    if (schema, table) != (SANDBOX, TABLE):
        raise HTTPException(404, "Only the exact field-time sandbox asset is eligible")


async def _context(session, policy, *, lock_plan=False):
    identity = await _identity(session, TABLE)
    if policy is None or not policy.enabled or identity is None:
        return None, identity
    stmt = select(TransferPlanRow).where(TransferPlanRow.id == policy.plan_id)
    plan = await session.scalar(stmt.with_for_update(read=True, nowait=True) if lock_plan else stmt)
    return (plan if not _drift(policy, plan, identity) else None), identity


async def _index(session, identity):
    if identity is None:
        return None
    return (await session.execute(_INDEX, {"oid": identity[0]})).one_or_none()


def _pins(row, policy, identity, index):
    return (row is not None and policy is not None and index is not None
            and row.policy_generation == policy.generation
            and identity == (row.target_oid, row.target_filenode)
            and tuple(index[:2]) == (row.index_oid, row.index_filenode))


async def _latest_receipt(session, row, plan):
    result = await session.execute(select(TransferRunRow.id, TransferRunRow.recorded_at).where(
        TransferRunRow.plan_id == plan.id, TransferRunRow.plan_updated_at == plan.updated_at,
        TransferRunRow.write_mode == plan.write_mode,
        TransferRunRow.source_schema == SANDBOX, TransferRunRow.source_table == plan.source_table,
        TransferRunRow.target_schema == SANDBOX, TransferRunRow.target_table == TABLE,
        TransferRunRow.target_oid == row.target_oid, TransferRunRow.target_filenode == row.target_filenode,
        TransferRunRow.target_oid.is_not(None), TransferRunRow.target_filenode.is_not(None),
        text("helio_transfer_runs.rows_affected > 0"), TransferRunRow.recorded_at > row.reviewed_at,
    ).order_by(TransferRunRow.recorded_at.desc(), TransferRunRow.id.desc()).limit(2))
    items = result.all()
    # Equal clocks cannot establish which distinct transfer was newer.
    if not items or (len(items) == 2 and items[0].recorded_at == items[1].recorded_at):
        return None
    return items[0]


async def read_field_freshness(session: AsyncSession, schema: str, table: str) -> AssetFieldFreshnessStatus:
    _require_target(schema, table)
    if not field_freshness_enabled():
        return AssetFieldFreshnessStatus(enabled=False)
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
    if not await _visible(session, TABLE):
        return AssetFieldFreshnessStatus(enabled=False)
    policy = await session.get(AssetFreshnessPolicyRow, TABLE)
    plan, identity = await _context(session, policy)
    index = await _index(session, identity) if plan else None
    row = await session.get(AssetFieldFreshnessRow, TABLE)
    info = dict(enabled=True, policyGeneration=policy.generation if index else None,
                generation=row.generation if row else 0, maxLagSeconds=row.max_lag_seconds if row else None)
    if row is None:
        return AssetFieldFreshnessStatus(**info, reason=None if index else (
            "index_unavailable" if plan else "no_eligible_policy"))
    if not _pins(row, policy, identity, index) or not await _fence_current(session, policy):
        return AssetFieldFreshnessStatus(**info, state="superseded", reason="scope_changed")
    now = await session.scalar(_CLOCK)
    incidents = await list_quality_incidents(session, policy, field_only=True)
    info["incident"] = next(iter(incidents.active or incidents.history), None)
    receipt = await _latest_receipt(session, row, plan)
    if receipt is None:
        return AssetFieldFreshnessStatus(**info, state="unknown", reason="no_verified_receipt")
    if row.checked_at is None or (receipt.id, receipt.recorded_at) != (row.receipt_id, row.receipt_recorded_at):
        return AssetFieldFreshnessStatus(**info, state="unknown", reason="awaiting_measurement")
    valid_until = receipt.recorded_at + _TTL
    if not row.checked_at <= now < valid_until:
        return AssetFieldFreshnessStatus(**info, state="unknown", reason="observation_expired")
    lag = (receipt.recorded_at - row.field_recorded_at).total_seconds()
    stale = lag > row.max_lag_seconds
    return AssetFieldFreshnessStatus(
        **info, state="stale" if stale else "fresh", reason="field_lag" if stale else None,
        fieldRecordedAt=row.field_recorded_at, receiptRecordedAt=receipt.recorded_at, checkedAt=row.checked_at,
        validUntil=valid_until, lagSeconds=lag,
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
    if index is None or not index[1]:
        raise HTTPException(409, "An eligible field-time B-tree index is required")
    return policy, identity, index


async def review_field_freshness(session, schema, table, request: AssetFieldFreshnessReview):
    _require_target(schema, table)
    if not field_freshness_enabled():
        raise HTTPException(403, "Field-time checks are disabled")
    policy, identity, index = await _write_context(session)
    old = await session.get(AssetFieldFreshnessRow, TABLE)
    if (request.expectedGeneration, request.policyGeneration) != (old.generation if old else 0, policy.generation):
        raise HTTPException(409, "The field-time review changed; reload before continuing")
    if _pins(old, policy, identity, index) and old.max_lag_seconds == request.maxLagSeconds:
        await session.commit()
        return await read_field_freshness(session, schema, table)
    now = await session.scalar(_CLOCK)
    if old:
        await session.execute(update(AssetQualityIncidentRow).where(
            AssetQualityIncidentRow.target_table == TABLE, AssetQualityIncidentRow.kind == _KIND,
            AssetQualityIncidentRow.closed_at.is_(None),
        ).values(closed_at=now, close_reason="policy_reviewed"))
    values = dict(
        generation=request.expectedGeneration + 1, policy_generation=policy.generation,
        max_lag_seconds=request.maxLagSeconds, target_oid=identity[0], target_filenode=identity[1],
        index_oid=index[0], index_filenode=index[1], reviewed_at=now,
        field_recorded_at=None, receipt_recorded_at=None, receipt_id=None, checked_at=None,
    )
    if old is None:
        session.add(AssetFieldFreshnessRow(target_table=TABLE, **values))
    else:
        for key, value in values.items():
            setattr(old, key, value)
    await session.commit()
    return await read_field_freshness(session, schema, table)


async def acknowledge_field_freshness(session, schema, table, incident: UUID, generation: int, actor: UUID):
    _require_target(schema, table)
    if not field_freshness_enabled():
        raise HTTPException(403, "Field-time checks are disabled")
    policy, identity, index = await _write_context(session)
    row = await session.get(AssetFieldFreshnessRow, TABLE)
    if not _pins(row, policy, identity, index) or row.generation != generation:
        raise HTTPException(409, "The field-time review changed; reload the incident")
    result = await acknowledge_quality_incident_locked(
        session, policy, incident, policy.generation, actor, await session.scalar(_CLOCK), field_only=True,
    )
    await session.commit()
    return result


def _verified_plan(document, index):
    if not isinstance(document, list) or len(document) != 1 or not isinstance(document[0], dict):
        return False
    root = document[0].get("Plan", {})
    if not isinstance(root, dict) or root.get("Parallel Aware") is not False:
        return False
    if any(key in root for key in ("Filter", "InitPlan", "SubPlan")):
        return False
    nodes = root.get("Plans", [])
    if (root.get("Node Type") != "Limit" or root.get("Plan Rows") != 1
        or not isinstance(nodes, list) or len(nodes) != 1 or not isinstance(nodes[0], dict)):
        return False
    leaf = nodes[0]
    return (leaf.get("Parallel Aware") is False and leaf.get("Node Type") in ("Index Scan", "Index Only Scan")
            and leaf.get("Index Name") == index[2]
            and leaf.get("Parent Relationship") == "Outer" and leaf.get("Alias") == "fixture"
            and leaf.get("Schema") == SANDBOX and leaf.get("Relation Name") == TABLE
            and leaf.get("Scan Direction") == index[3]
            and not any(name in leaf for name in ("Filter", "Index Cond", "Recheck Cond", "Plans", "InitPlan", "SubPlan")))


async def evaluate_field_freshness(session: AsyncSession, policy: AssetFreshnessPolicyRow):
    """Existing minute-tick optional savepoint; no business read without exact plan proof."""
    if not field_freshness_enabled() or policy.target_table != TABLE:
        return
    row = await session.get(AssetFieldFreshnessRow, TABLE)
    if row is None or row.policy_generation != policy.generation or not await _visible(session, TABLE):
        return
    plan, identity = await _context(session, policy, lock_plan=True)
    if plan is None or identity != (row.target_oid, row.target_filenode):
        return
    receipt = await _latest_receipt(session, row, plan)
    if receipt is None or (row.receipt_recorded_at is not None and receipt.recorded_at <= row.receipt_recorded_at):
        return
    await session.execute(_LOCK)
    identity = await _identity(session, TABLE)
    index = await _index(session, identity)
    if not _pins(row, policy, identity, index):
        return
    # Re-read under the write fence to avoid sampling across a concurrent target commit.
    receipt = await _latest_receipt(session, row, plan)
    now = await session.scalar(_CLOCK)
    if (receipt is None or receipt.recorded_at > now
        or (row.receipt_recorded_at is not None and receipt.recorded_at <= row.receipt_recorded_at)
        or now - receipt.recorded_at >= _TTL or (row.checked_at is not None and now <= row.checked_at)):
        return
    document = await session.scalar(text("EXPLAIN (FORMAT JSON, VERBOSE TRUE, ANALYZE FALSE) " + _SAMPLE_SQL))
    if not _verified_plan(document, index):
        return
    field_time = await session.scalar(text(_SAMPLE_SQL))
    checked_at = await session.scalar(_CLOCK)
    if field_time is None or field_time > receipt.recorded_at or checked_at < now or checked_at - receipt.recorded_at >= _TTL:
        return
    lag = (receipt.recorded_at - field_time).total_seconds()
    stale = lag > row.max_lag_seconds
    row.field_recorded_at, row.receipt_recorded_at, row.checked_at = field_time, receipt.recorded_at, checked_at
    row.receipt_id = receipt.id
    await observe_quality_incident(session, policy, _KIND, checked_at, healthy=not stale,
                                   failure="field_lag" if stale else None)
