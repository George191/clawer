"""Track last confirmed internal writes without reading warehouse business rows."""

from datetime import datetime, timedelta, timezone
import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.storage.postgres_client import get_pg_client
from app.web.api.models.asset_freshness import (
    AssetFreshnessAssessment, AssetFreshnessCandidate, AssetFreshnessChange, AssetFreshnessPolicy,
    AssetFreshnessPolicyRow, AssetFreshnessResponse, AssetFreshnessTransition, AssetFreshnessTransitionRow,
    AssetQualityIncident, AssetQualityIncidentAck, AssetSchemaCaptureRequest, AssetSchemaPreview, AssetSchemaPreviewRequest,
)
from app.web.api.models.transfer_plans import TransferPlanRow, TransferRunRow
from app.web.core.config import settings
from app.web.services.asset_quality import (
    captured_schema, enabled_heap, evaluate_asset_quality, read_quality_checks, read_schema_preview,
)
from app.web.services.asset_quality_incidents import (
    acknowledge_quality_incident_locked, list_quality_incidents, observe_quality_incident, supersede_quality_incidents,
)
from app.web.services.transfer_runs import SANDBOX

_BATCH_LIMIT = 25
_PERIOD = timedelta(minutes=1)
_STALE_AFTER = timedelta(minutes=3)
_HISTORY_LIMIT = 25
_log = logging.getLogger(__name__)

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
    return (plan is not None and plan.source_node is None and plan.source_schema == SANDBOX
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
    plan = await session.get(TransferPlanRow, row.plan_id, populate_existing=True)
    context_reason = _drift(row, plan, identity) if flags else "checking_disabled"
    now = _utc_now()
    policy = AssetFreshnessPolicy(planId=row.plan_id, planUpdatedAt=row.plan_updated_at,
                                  thresholdMinutes=row.threshold_minutes, enabled=row.enabled,
                                  generation=row.generation, updatedAt=row.updated_at,
                                  schemaCheckEnabled=row.schema_check_enabled,
                                  schemaBaseline=captured_schema(row, context_reason), heapSize=enabled_heap(row))
    if not row.enabled:
        assessment = AssetFreshnessAssessment(state="paused")
    else:
        reason = context_reason
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
    checks = await read_quality_checks(session, row, context_reason, now)
    incidents = await list_quality_incidents(session, row)
    return AssetFreshnessResponse(available=available, policy=policy, assessment=assessment,
                                  history=history, checks=checks, incidents=incidents)


async def get_asset_freshness_candidates(session: AsyncSession, schema: str, table: str) -> list[AssetFreshnessCandidate]:
    """List bounded exact-route draft candidates; never inspect table contents."""
    _check_schema(schema)
    if not asset_freshness_enabled() or await _identity(session, table) is None:
        return []
    plans = (await session.scalars(select(TransferPlanRow).where(
        TransferPlanRow.target_schema == SANDBOX, TransferPlanRow.target_table == table,
        TransferPlanRow.source_schema == SANDBOX,
        TransferPlanRow.source_node.is_(None),
    ).order_by(TransferPlanRow.name, TransferPlanRow.id).limit(100))).all()
    return [AssetFreshnessCandidate(id=p.id, name=p.name, updatedAt=p.updated_at) for p in plans]


async def put_asset_freshness(
    session: AsyncSession, schema: str, table: str, change: AssetFreshnessChange,
) -> AssetFreshnessResponse:
    """Optimistically configure, resume or pause a fixed target's checker."""
    _check_schema(schema)
    row = await _lock_policy(session, table)
    if change.expectedGeneration != (row.generation if row else None):
        raise HTTPException(409, "The freshness policy changed; reload before updating it")
    now = _utc_now()
    if not change.enabled:
        if row is None:
            raise HTTPException(404, "Asset freshness policy does not exist")
        if row.plan_id != change.planId or row.plan_updated_at != change.expectedPlanUpdatedAt:
            raise HTTPException(409, "The freshness policy plan binding changed; reload before pausing it")
        if row.enabled:
            await supersede_quality_incidents(session, row, now, "policy_paused")
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
        if change.schemaCheckEnabled is not None:
            values["schema_check_enabled"] = change.schemaCheckEnabled
        if "heapSize" in change.model_fields_set:
            bounds = change.heapSize
            values["heap_min_bytes"] = int(bounds.minBytes) if bounds and bounds.minBytes is not None else None
            values["heap_max_bytes"] = int(bounds.maxBytes) if bounds and bounds.maxBytes is not None else None
        if row is None:
            inserted = (await session.execute(insert(AssetFreshnessPolicyRow).values(
                target_table=table, **values,
            ).on_conflict_do_nothing().returning(AssetFreshnessPolicyRow.target_table))).scalar_one_or_none()
            if inserted is None:
                raise HTTPException(409, "The freshness policy changed; reload before updating it")
        else:
            await supersede_quality_incidents(session, row, now, "policy_reviewed")
            for field, value in values.items():
                setattr(row, field, value)
    await session.commit()
    return await get_asset_freshness(session, schema, table)


async def _lock_policy(session: AsyncSession, table: str) -> AssetFreshnessPolicyRow | None:
    return (await session.scalars(select(AssetFreshnessPolicyRow).where(
        AssetFreshnessPolicyRow.target_table == table,
    ).with_for_update().execution_options(populate_existing=True))).one_or_none()


async def _review_schema(
    session: AsyncSession, schema: str, table: str, generation: int, *, recording: bool,
) -> tuple[AssetFreshnessPolicyRow, AssetSchemaPreview]:
    _check_schema(schema)
    if not asset_freshness_enabled():
        raise HTTPException(403, "Sandbox asset quality checks are disabled")
    row = (await _lock_policy(session, table) if recording else
           await session.get(AssetFreshnessPolicyRow, table, populate_existing=True))
    if row is None:
        raise HTTPException(404, "Asset freshness policy does not exist")
    if row.generation != generation:
        raise HTTPException(409, "The quality policy changed; reload and review the columns again")
    if not row.enabled or not row.schema_check_enabled:
        raise HTTPException(409, "Enable the schema check before reviewing a baseline")
    statement = select(TransferPlanRow).where(TransferPlanRow.id == row.plan_id)
    if recording:
        statement = statement.with_for_update(read=True)
    plan = (await session.scalars(statement.execution_options(populate_existing=True))).one_or_none()
    if _drift(row, plan, await _identity(session, table)):
        raise HTTPException(409, "The approved plan or physical target changed; review its current binding")
    preview = await read_schema_preview(session, row)
    if preview is None:
        raise HTTPException(422, "Schema review requires 1–128 columns with supported built-in types")
    return row, preview


async def preview_schema_baseline(
    session: AsyncSession, schema: str, table: str, request: AssetSchemaPreviewRequest,
) -> AssetSchemaPreview:
    """Preview metadata without saving or recapturing the approved baseline."""
    _, preview = await _review_schema(session, schema, table, request.expectedGeneration, recording=False)
    return preview


async def capture_schema_baseline(
    session: AsyncSession, schema: str, table: str, request: AssetSchemaCaptureRequest,
) -> AssetFreshnessResponse:
    """Capture exactly the earlier preview, rejecting drift after operator review."""
    row, preview = await _review_schema(session, schema, table, request.expectedGeneration, recording=True)
    if preview.fingerprint != request.expectedFingerprint:
        raise HTTPException(409, "The table columns changed after review; preview its metadata again")
    now = _utc_now()
    await supersede_quality_incidents(session, row, now, "schema_baseline_captured")
    row.schema_baseline = {"target": [row.target_oid, row.target_filenode],
                           "preview": {**preview.model_dump(mode="json"), "capturedAt": now.isoformat()}}
    row.generation += 1
    row.updated_at = now
    row.next_check_at = now
    await session.commit()
    return await get_asset_freshness(session, schema, table)


async def acknowledge_quality_incident(
    session: AsyncSession, schema: str, table: str, incident_id: UUID, request: AssetQualityIncidentAck, actor_id: UUID,
) -> AssetQualityIncident:
    """Record an explicit acknowledgement under the check policy's serialization lock."""
    _check_schema(schema)
    if not asset_freshness_enabled():
        raise HTTPException(403, "Sandbox asset quality checks are disabled")
    row = await _lock_policy(session, table)
    if row is None:
        raise HTTPException(404, "Asset freshness policy does not exist")
    incident = await acknowledge_quality_incident_locked(
        session, row, incident_id, request.expectedGeneration, actor_id, _utc_now(),
    )
    await session.commit()
    return incident


async def _evaluate_one(session: AsyncSession, row: AssetFreshnessPolicyRow, now: datetime) -> str:
    plan = await session.get(TransferPlanRow, row.plan_id, populate_existing=True)
    identity = await _identity(session, row.target_table)
    reason = _drift(row, plan, identity)
    binding_current = reason is None
    await evaluate_asset_quality(session, row, reason, now)
    receipt = None
    if reason is None:
        receipt = (await session.execute(select(TransferRunRow.id, TransferRunRow.recorded_at).where(
            TransferRunRow.plan_id == row.plan_id, TransferRunRow.plan_updated_at == row.plan_updated_at,
            TransferRunRow.target_schema == SANDBOX, TransferRunRow.target_table == row.target_table,
            TransferRunRow.target_oid == row.target_oid, TransferRunRow.target_filenode == row.target_filenode,
            TransferRunRow.target_oid.is_not(None), TransferRunRow.target_filenode.is_not(None),
            text("helio_transfer_runs.rows_affected > 0"), TransferRunRow.recorded_at <= now,
        ).order_by(TransferRunRow.recorded_at.desc(), TransferRunRow.id.desc()).limit(1))).one_or_none()
        if receipt is None:
            reason = "no_verified_write"
    if reason is not None:
        state = "unknown"
    else:
        state = "overdue" if now - receipt.recorded_at > timedelta(minutes=row.threshold_minutes) else "healthy"
    await observe_quality_incident(session, row, "freshness", now, healthy=state == "healthy",
                                  failure="write_overdue" if state == "overdue" else None)
    if (row.state, row.reason) != (state, reason):
        session.add(AssetFreshnessTransitionRow(
            target_table=row.target_table, generation=row.generation, state=state, reason=reason, checked_at=now,
            receipt_id=receipt.id if receipt else None, receipt_recorded_at=receipt.recorded_at if receipt else None,
        ))
    row.state, row.reason, row.checked_at, row.next_check_at = state, reason, now, now + _PERIOD
    row.receipt_id = receipt.id if receipt else None
    row.receipt_recorded_at = receipt.recorded_at if receipt else None
    if binding_current and settings.TRANSFER_SANDBOX_COLLECTED_STATS_ENABLED:
        from app.web.services.asset_collected_stats import collect_asset_stats

        await collect_asset_stats(session, row, now)
    if settings.TRANSFER_SANDBOX_JOB_QUALITY_ENABLED:
        from app.web.services.asset_job_quality import evaluate_daily_job_quality, job_quality_enabled

        if job_quality_enabled():
            try:
                async with session.begin_nested():
                    await session.execute(text("SET LOCAL statement_timeout='750ms'"))
                    await evaluate_daily_job_quality(session, row, identity, now)
                    await session.execute(text("SET LOCAL statement_timeout='3s'"))
            except Exception:
                _log.warning("Optional Daily UTC quality check did not settle")
    if settings.TRANSFER_SANDBOX_WEEKLY_JOB_QUALITY_ENABLED:
        from app.web.services.asset_weekly_job_quality import evaluate_weekly_job_quality, weekly_job_quality_enabled

        if weekly_job_quality_enabled():
            try:
                async with session.begin_nested():
                    await session.execute(text("SET LOCAL statement_timeout='750ms'"))
                    await evaluate_weekly_job_quality(session, row, identity, now)
                    await session.execute(text("SET LOCAL statement_timeout='3s'"))
            except Exception:
                _log.warning("Optional Weekly UTC quality check did not settle")
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
    if settings.TRANSFER_SANDBOX_FIELD_FRESHNESS_ENABLED or settings.TRANSFER_SANDBOX_MIN_ROWS_ENABLED:
        from app.web.services.asset_field_freshness import TABLE, evaluate_field_freshness
        from app.web.services.asset_min_rows import evaluate_min_rows

        if (field_policy := next((row for row in rows if row.target_table == TABLE), None)) is not None:
            checks = []
            if settings.TRANSFER_SANDBOX_FIELD_FRESHNESS_ENABLED:
                checks.append(("field-time freshness", evaluate_field_freshness))
            if settings.TRANSFER_SANDBOX_MIN_ROWS_ENABLED:
                checks.append(("minimum-row", evaluate_min_rows))
            # Run last: each successful SHARE fence is retained only through
            # the bounded checks for this same fixture and outer commit.
            for label, evaluate in checks:
                try:
                    async with session.begin_nested():
                        await session.execute(text("SET LOCAL statement_timeout='750ms'"))
                        await evaluate(session, field_policy)
                        await session.execute(text("SET LOCAL statement_timeout='3s'"))
                except Exception:
                    _log.warning("Optional %s check did not settle", label)
    await session.commit()
    return counts


async def dispatch_asset_freshness_checks() -> dict[str, int]:
    """Run checks inline on the existing minute tick without dispatching external work."""
    async with get_pg_client().session() as session:
        return await evaluate_due_asset_freshness(session)
