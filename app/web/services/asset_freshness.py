"""Track last confirmed internal writes without reading warehouse business rows."""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.storage.postgres_client import get_pg_client
from app.web.api.models.asset_freshness import (
    AssetFreshnessAssessment, AssetFreshnessCandidate, AssetFreshnessChange, AssetFreshnessPolicy,
    AssetFreshnessPolicyRow, AssetFreshnessResponse, AssetFreshnessTransition, AssetFreshnessTransitionRow,
)
from app.web.api.models.transfer_plans import TransferPlanRow, TransferRunRow
from app.web.core.config import settings
from app.web.services.transfer_runs import SANDBOX

_BATCH_LIMIT = 25
_PERIOD = timedelta(minutes=1)
_STALE_AFTER = timedelta(minutes=3)
_HISTORY_LIMIT = 25

# Keep identity checks catalog-only. A relation rewrite changes its filenode
# even when its logical name is reused.
_TARGET_IDENTITY = text("""
    SELECT c.oid::bigint, pg_catalog.pg_relation_filenode(c.oid)::bigint
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_am a ON a.oid = c.relam AND a.amname = 'heap'
    WHERE n.nspname = :schema AND c.relname = :name
      AND c.relkind = 'r' AND c.relpersistence = 'p'
      AND NOT c.relispartition AND NOT c.relrowsecurity AND NOT c.relhasrules
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE c.oid IN (i.inhrelid, i.inhparent))
""")


def asset_freshness_enabled() -> bool:
    """Require a specifically configured local test environment and both feature gates."""
    return ("ENVIRONMENT" in settings.model_fields_set and settings.ENVIRONMENT == "local"
            and settings.TRANSFER_SANDBOX_ENABLED and settings.TRANSFER_SANDBOX_FRESHNESS_ENABLED)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _check_schema(schema: str) -> None:
    if schema != SANDBOX:
        raise HTTPException(404, "Asset write-cadence checks are not available outside the transfer sandbox")


async def _identity(session: AsyncSession, table: str) -> tuple[int, int] | None:
    row = (await session.execute(_TARGET_IDENTITY, {"schema": SANDBOX, "name": table})).one_or_none()
    return (row[0], row[1]) if row is not None and row[1] is not None else None


def _eligible(plan: TransferPlanRow | None, table: str) -> bool:
    return (plan is not None and plan.source_schema == SANDBOX
            and plan.target_schema == SANDBOX and plan.target_table == table)


def _drift(policy: AssetFreshnessPolicyRow, plan: TransferPlanRow | None,
           identity: tuple[int, int] | None) -> str | None:
    if not _eligible(plan, policy.target_table) or plan.updated_at != policy.plan_updated_at:
        return "plan_changed"
    if identity != (policy.target_oid, policy.target_filenode):
        return "target_changed"
    return None


def _evidence(row: AssetFreshnessPolicyRow | AssetFreshnessTransitionRow) -> dict:
    return {"state": row.state, "reason": row.reason, "checkedAt": row.checked_at,
            "receiptRecordedAt": row.receipt_recorded_at, "receiptId": row.receipt_id}


async def get_asset_freshness(session: AsyncSession, schema: str, table: str) -> AssetFreshnessResponse:
    """Return a current, scope-checked state and the most recent recorded transitions."""
    _check_schema(schema)
    row = await session.get(AssetFreshnessPolicyRow, table, populate_existing=True)
    flags = asset_freshness_enabled()
    identity = await _identity(session, table) if flags else None
    available = flags and identity is not None
    if row is None:
        return AssetFreshnessResponse(available=available,
                                      assessment=AssetFreshnessAssessment(state="not_configured"))
    policy = AssetFreshnessPolicy(planId=row.plan_id, planUpdatedAt=row.plan_updated_at,
                                  thresholdMinutes=row.threshold_minutes, enabled=row.enabled,
                                  generation=row.generation, updatedAt=row.updated_at)
    if not row.enabled:
        assessment = AssetFreshnessAssessment(state="paused")
    else:
        plan = await session.get(TransferPlanRow, row.plan_id, populate_existing=True)
        reason = _drift(row, plan, identity) if flags else "checking_disabled"
        now = _utc_now()
        valid_until = row.checked_at + _STALE_AFTER if row.checked_at else None
        if row.state == "healthy" and row.receipt_recorded_at and valid_until:
            valid_until = min(valid_until, row.receipt_recorded_at + timedelta(minutes=row.threshold_minutes))
        if reason is None and valid_until and not row.checked_at <= now <= valid_until:
            reason = "evaluation_stale"
        assessment = (AssetFreshnessAssessment(state="unknown", reason=reason, checkedAt=row.checked_at)
                      if reason else AssetFreshnessAssessment(
                          **_evidence(row), validUntil=valid_until if row.state in ("healthy", "overdue") else None,
                      ))
    history_rows = (await session.scalars(select(AssetFreshnessTransitionRow).where(
        AssetFreshnessTransitionRow.target_table == table,
    ).order_by(AssetFreshnessTransitionRow.checked_at.desc(), AssetFreshnessTransitionRow.id.desc())
        .limit(_HISTORY_LIMIT))).all()
    history = [AssetFreshnessTransition(id=entry.id, generation=entry.generation, **_evidence(entry))
               for entry in history_rows]
    return AssetFreshnessResponse(available=available, policy=policy, assessment=assessment, history=history)


async def get_asset_freshness_candidates(session: AsyncSession, schema: str, table: str) -> list[AssetFreshnessCandidate]:
    """List bounded exact-route draft candidates; never inspect table contents."""
    _check_schema(schema)
    if not asset_freshness_enabled() or await _identity(session, table) is None:
        return []
    plans = (await session.scalars(select(TransferPlanRow).where(
        TransferPlanRow.target_schema == SANDBOX, TransferPlanRow.target_table == table,
        TransferPlanRow.source_schema == SANDBOX,
    ).order_by(TransferPlanRow.name, TransferPlanRow.id).limit(100))).all()
    return [AssetFreshnessCandidate(id=p.id, name=p.name, updatedAt=p.updated_at) for p in plans]


async def put_asset_freshness(
    session: AsyncSession, schema: str, table: str, change: AssetFreshnessChange,
) -> AssetFreshnessResponse:
    """Optimistically configure, resume or pause a fixed target's checker."""
    _check_schema(schema)
    row = (await session.scalars(select(AssetFreshnessPolicyRow).where(
        AssetFreshnessPolicyRow.target_table == table,
    ).with_for_update().execution_options(populate_existing=True))).one_or_none()
    if change.expectedGeneration != (row.generation if row else None):
        raise HTTPException(409, "The freshness policy changed; reload before updating it")
    now = _utc_now()
    if not change.enabled:
        if row is None:
            raise HTTPException(404, "Asset freshness policy does not exist")
        if row.plan_id != change.planId or row.plan_updated_at != change.expectedPlanUpdatedAt:
            raise HTTPException(409, "The freshness policy plan binding changed; reload before pausing it")
        if row.enabled:
            row.enabled = False
            row.generation += 1
            row.updated_at = now
            row.state = "paused"
            row.reason = None
            row.checked_at = None
            row.receipt_id = None
            row.receipt_recorded_at = None
    else:
        if not asset_freshness_enabled():
            raise HTTPException(403, "Sandbox asset freshness checks are disabled")
        plan = (await session.scalars(select(TransferPlanRow).where(
            TransferPlanRow.id == change.planId,
        ).with_for_update(read=True).execution_options(populate_existing=True))).one_or_none()
        if not _eligible(plan, table):
            raise HTTPException(422, "Choose a local sandbox transfer plan targeting this exact table")
        if plan.updated_at != change.expectedPlanUpdatedAt:
            raise HTTPException(409, "The transfer plan changed; choose its current reviewed revision")
        identity = await _identity(session, table)
        if identity is None:
            raise HTTPException(422, "The exact sandbox target is not an eligible physical warehouse table")
        values = dict(plan_id=plan.id, plan_updated_at=plan.updated_at,
                      target_oid=identity[0], target_filenode=identity[1], enabled=True,
                      generation=row.generation + 1 if row else 1, threshold_minutes=change.thresholdMinutes,
                      updated_at=now, next_check_at=now, state="not_checked", reason=None,
                      checked_at=None, receipt_id=None, receipt_recorded_at=None)
        if row is None:
            inserted = (await session.execute(insert(AssetFreshnessPolicyRow).values(
                target_table=table, **values,
            ).on_conflict_do_nothing().returning(AssetFreshnessPolicyRow.target_table))).scalar_one_or_none()
            if inserted is None:
                raise HTTPException(409, "The freshness policy changed; reload before updating it")
        else:
            for field, value in values.items():
                setattr(row, field, value)
    await session.commit()
    return await get_asset_freshness(session, schema, table)


async def _evaluate_one(session: AsyncSession, row: AssetFreshnessPolicyRow, now: datetime) -> str:
    plan = await session.get(TransferPlanRow, row.plan_id, populate_existing=True)
    reason = _drift(row, plan, await _identity(session, row.target_table))
    receipt = None
    if reason is None:
        receipt = (await session.execute(select(TransferRunRow.id, TransferRunRow.recorded_at).where(
            TransferRunRow.plan_id == row.plan_id, TransferRunRow.plan_updated_at == row.plan_updated_at,
            TransferRunRow.target_schema == SANDBOX, TransferRunRow.target_table == row.target_table,
            TransferRunRow.target_oid == row.target_oid, TransferRunRow.target_filenode == row.target_filenode,
            TransferRunRow.target_oid.is_not(None), TransferRunRow.target_filenode.is_not(None),
            text("ontology_transfer_runs.rows_affected > 0"), TransferRunRow.recorded_at <= now,
        ).order_by(TransferRunRow.recorded_at.desc(), TransferRunRow.id.desc()).limit(1))).one_or_none()
        if receipt is None:
            reason = "no_verified_write"
    if reason is not None:
        state = "unknown"
    else:
        state = "overdue" if now - receipt.recorded_at > timedelta(minutes=row.threshold_minutes) else "healthy"
    if (row.state, row.reason) != (state, reason):
        session.add(AssetFreshnessTransitionRow(
            target_table=row.target_table, generation=row.generation, state=state, reason=reason, checked_at=now,
            receipt_id=receipt.id if receipt else None, receipt_recorded_at=receipt.recorded_at if receipt else None,
        ))
    row.state, row.reason, row.checked_at, row.next_check_at = state, reason, now, now + _PERIOD
    row.receipt_id = receipt.id if receipt else None
    row.receipt_recorded_at = receipt.recorded_at if receipt else None
    return state


async def evaluate_due_asset_freshness(session: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
    """Evaluate one indexed batch under SKIP LOCKED; errors never publish optimistic state."""
    counts = {"checked": 0, "healthy": 0, "overdue": 0, "unknown": 0}
    if not asset_freshness_enabled():
        return counts
    now = now or _utc_now()
    await session.execute(text("SET LOCAL statement_timeout = '3s'"))
    await session.execute(text("SET LOCAL lock_timeout = '750ms'"))
    rows = (await session.scalars(select(AssetFreshnessPolicyRow).where(
        AssetFreshnessPolicyRow.enabled, AssetFreshnessPolicyRow.next_check_at <= now,
    ).order_by(AssetFreshnessPolicyRow.next_check_at, AssetFreshnessPolicyRow.target_table)
        .limit(_BATCH_LIMIT).with_for_update(skip_locked=True))).all()
    for row in rows:
        state = await _evaluate_one(session, row, now)
        counts["checked"] += 1
        counts[state] += 1
    await session.commit()
    return counts


async def dispatch_asset_freshness_checks() -> dict[str, int]:
    """Run checks inline on the existing minute tick without dispatching external work."""
    async with get_pg_client().session() as session:
        return await evaluate_due_asset_freshness(session)
