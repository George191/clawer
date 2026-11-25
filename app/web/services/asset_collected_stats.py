"""Bounded replay of collected, exact physical PostgreSQL catalog observations."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import logging

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.asset_collected_stats import AssetCollectedStatRow, AssetCollectedStats, AssetStatsObservation
from app.web.api.models.asset_freshness import AssetFreshnessPolicyRow
from app.web.api.models.transfer_plans import TransferPlanRow
from app.web.core.config import settings
from app.web.services.etl_layers import quote_identifier
from app.web.services.transfer_runs import SANDBOX

_STALE_AFTER = timedelta(minutes=3)
_log = logging.getLogger(__name__)
_VISIBLE = text("""
    SELECT NOT EXISTS (SELECT 1 FROM app.ontology_objects WHERE schema_name=:schema AND table_name=:table
                       AND is_active AND visibility='hidden')
""")
_SAMPLE = text("""
    SELECT pg_catalog.clock_timestamp() AS captured_at, c.reltuples::text AS estimate,
           greatest(pg_catalog.pg_stat_get_last_analyze_time(c.oid),
                    pg_catalog.pg_stat_get_last_autoanalyze_time(c.oid)) AS analyzed_at,
           pg_catalog.pg_stat_get_db_stat_reset_time(d.oid) AS reset_at
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_catalog.pg_am a ON a.oid=c.relam AND a.amname='heap'
    JOIN pg_catalog.pg_database d ON d.datname=pg_catalog.current_database()
    WHERE n.nspname=:schema AND c.relname=:table AND c.oid=CAST(:identity AS oid)
      AND pg_catalog.pg_relation_filenode(c.oid)=CAST(:filenode AS oid)
      AND c.relkind='r' AND c.relpersistence='p' AND NOT c.relispartition
      AND NOT c.relrowsecurity AND NOT c.relhasrules
      AND NOT EXISTS (SELECT 1 FROM app.ontology_objects o WHERE o.schema_name=:schema AND o.table_name=:table
                      AND o.is_active AND o.visibility='hidden')
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE c.oid IN (i.inhrelid, i.inhparent))
""")


def collected_stats_enabled() -> bool:
    from app.web.services.asset_freshness import asset_freshness_enabled

    return settings.TRANSFER_SANDBOX_COLLECTED_STATS_ENABLED and asset_freshness_enabled()


def _estimate(raw: str | None) -> str | None:
    if raw is None or len(raw) > 32:
        return None
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    # reltuples is float4; guard exponent and reject unknown, non-finite and negative values.
    if not value.is_finite() or value < 0 or (value and not -46 <= value.adjusted() <= 38):
        return None
    rendered = format(value, "f")
    return "0" if not value else rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _view(row: AssetCollectedStatRow | None) -> AssetStatsObservation | None:
    if row is None or row.state != "captured":
        return None
    return AssetStatsObservation(capturedAt=row.captured_at, policyGeneration=row.policy_generation,
                                 planUpdatedAt=row.plan_updated_at, estimatedRows=row.estimated_rows,
                                 lastAnalyzedAt=row.analyzed_at)


def _same_physical(row: AssetCollectedStatRow, policy: AssetFreshnessPolicyRow) -> bool:
    return (row.target_oid, row.target_filenode) == (policy.target_oid, policy.target_filenode)


def _same_review(row: AssetCollectedStatRow, policy: AssetFreshnessPolicyRow) -> bool:
    return (_same_physical(row, policy) and (row.policy_generation, row.plan_id, row.plan_updated_at)
            == (policy.generation, policy.plan_id, policy.plan_updated_at))


async def _visible(session: AsyncSession, table: str) -> bool:
    return bool(await session.scalar(_VISIBLE, {"schema": SANDBOX, "table": table}))


async def _probe_catalog(session: AsyncSession, policy: AssetFreshnessPolicyRow):
    table = quote_identifier(policy.target_table)
    await session.execute(text(f'LOCK TABLE ONLY "{SANDBOX}".{table} IN ACCESS SHARE MODE NOWAIT'))
    return (await session.execute(_SAMPLE, {
        "schema": SANDBOX, "table": policy.target_table, "identity": policy.target_oid, "filenode": policy.target_filenode,
    })).one_or_none()


async def _save(session: AsyncSession, table: str, slot: str, values: dict) -> None:
    await session.execute(insert(AssetCollectedStatRow).values(target_table=table, slot=slot, **values)
                          .on_conflict_do_update(index_elements=["target_table", "slot"], set_=values))


def _archive(row: AssetCollectedStatRow) -> dict:
    fields = (
        "policy_generation", "plan_id", "plan_updated_at", "target_oid", "target_filenode", "captured_at", "evaluation_at",
        "state", "estimated_rows", "analyzed_at", "analyze_floor", "reset_at",
    )
    return {name: getattr(row, name) for name in fields}


def _max_stamp(*stamps: datetime | None) -> datetime | None:
    return max((stamp for stamp in stamps if stamp is not None), default=None)


async def collect_asset_stats(session: AsyncSession, policy: AssetFreshnessPolicyRow, evaluated_at: datetime) -> None:
    """Probe only inside the existing due policy's transaction and an isolated savepoint."""
    try:
        async with session.begin_nested():
            await session.execute(text("SET LOCAL statement_timeout = '750ms'"))
            if await _visible(session, policy.target_table):
                sample = await _probe_catalog(session, policy)
                if sample is not None:
                    await _record_sample(session, policy, evaluated_at, sample)
            await session.execute(text("SET LOCAL statement_timeout = '3s'"))
    except Exception:
        # Scope-specific statistics cannot roll back distinct freshness/quality results.
        _log.warning("Synthetic catalog statistics capture did not settle")


async def _record_sample(session: AsyncSession, policy: AssetFreshnessPolicyRow, evaluated_at: datetime, sample) -> None:
    previous = await session.get(AssetCollectedStatRow, (policy.target_table, "latest"), populate_existing=True)
    same = previous is not None and _same_physical(previous, policy)
    rewrite_in_place = (previous is not None and previous.target_oid == policy.target_oid and not same)
    floor = previous.analyze_floor if same else policy.updated_at if rewrite_in_place else None
    previous_reset = previous.reset_at if same else None
    captured, analyzed, observed_reset = sample.captured_at, sample.analyzed_at, sample.reset_at
    reset = _max_stamp(previous_reset, observed_reset)
    candidate = _estimate(sample.estimate)
    # A relation-specific counters reset appears as analyze disappearance/regression;
    # a database reset has an independent clock. Neither can turn retained reltuples
    # into a new current estimate until a genuinely later analysis is observed.
    invalid_clock = ((analyzed is not None and analyzed > captured)
                     or (observed_reset is not None and observed_reset > captured))
    regression = floor is not None and (analyzed is None or analyzed < floor)
    # PostgreSQL also moves the database reset clock when only a sibling
    # table's counters are reset. A surviving exact stored analyze clock
    # independently substantiates this physical table.
    unchanged_analyze = bool(same and previous.state == "captured" and analyzed is not None and analyzed == floor)
    database_reset = same and previous_reset != observed_reset and not unchanged_analyze
    still_reset = same and previous.state == "stats_reset"
    unproven_since_reset = (reset is not None and not unchanged_analyze and (analyzed is None or analyzed <= reset))
    no_measures = analyzed is None and candidate is None and not floor and not (same and previous.state == "captured")
    if no_measures and not invalid_clock and not still_reset and not rewrite_in_place:
        state, estimate, measured = "no_stats", None, None
    elif invalid_clock or regression or database_reset or still_reset or rewrite_in_place or unproven_since_reset:
        recovery_floor = _max_stamp(floor, reset)
        recovered = (not invalid_clock and analyzed is not None
                     and (recovery_floor is None or analyzed > recovery_floor))
        if not recovered:
            state, estimate, measured = "stats_reset", None, None
        else:
            state, estimate, measured = "captured", candidate, analyzed
    else:
        estimate, measured = candidate, analyzed
        state = "captured" if estimate is not None or measured is not None else "no_stats"
    if previous is not None and previous.state == "captured" and (
        not _same_review(previous, policy) or state != "captured"
    ):
        await _save(session, policy.target_table, "historical", _archive(previous))
    await _save(session, policy.target_table, "latest", dict(
        policy_generation=policy.generation, plan_id=policy.plan_id, plan_updated_at=policy.plan_updated_at,
        target_oid=policy.target_oid, target_filenode=policy.target_filenode, captured_at=captured,
        evaluation_at=evaluated_at, state=state, estimated_rows=estimate, analyzed_at=measured,
        analyze_floor=_max_stamp(floor, measured), reset_at=reset,
    ))


async def _fence_current(session: AsyncSession, policy: AssetFreshnessPolicyRow) -> bool:
    """Confirm that the exact current catalog name still opens the pinned relation."""
    from app.web.services.asset_freshness import _identity

    name = quote_identifier(policy.target_table)
    try:
        async with session.begin_nested():
            await session.execute(text(f'LOCK TABLE ONLY "{SANDBOX}".{name} IN ACCESS SHARE MODE NOWAIT'))
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) in {"42P01", "3F000", "42809"}:
            return False
        raise
    if await _identity(session, policy.target_table) != (policy.target_oid, policy.target_filenode):
        return False
    return bool(await session.scalar(text("""
        SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_locks WHERE pid=pg_catalog.pg_backend_pid()
                       AND locktype='relation' AND relation=CAST(:identity AS oid)
                       AND mode='AccessShareLock' AND granted)
    """), {"identity": policy.target_oid}))


async def read_asset_collected_stats(session: AsyncSession, schema: str, table: str) -> AssetCollectedStats:
    """Read previous evidence; no live statistics, ANALYZE, or target-row query."""
    from app.web.services.asset_freshness import _drift, _identity

    if schema != SANDBOX:
        raise HTTPException(404, "Collected statistics are available only for reviewed sandbox targets")
    if not collected_stats_enabled():
        return AssetCollectedStats(available=False, state="unavailable", reason="checking_disabled")
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL statement_timeout = '2s'"))
    await session.execute(text("SET LOCAL lock_timeout = '750ms'"))
    if not await _visible(session, table):
        return AssetCollectedStats(available=False, state="unavailable", reason="target_unavailable")
    identity = await _identity(session, table)
    policy = await session.get(AssetFreshnessPolicyRow, table)
    if policy is None:
        return (AssetCollectedStats(available=True, state="not_configured") if identity is not None else
                AssetCollectedStats(available=False, state="unavailable", reason="target_unavailable"))
    latest = await session.get(AssetCollectedStatRow, (table, "latest"))
    older = await session.get(AssetCollectedStatRow, (table, "historical"))
    plan = await session.get(TransferPlanRow, policy.plan_id)
    drift = _drift(policy, plan, identity)
    reason = ("policy_paused" if not policy.enabled else "policy_changed" if drift == "plan_changed" else drift)
    if reason is None and latest is not None and not _same_review(latest, policy):
        reason = "policy_changed"
    if reason is None and latest is None:
        return AssetCollectedStats(available=True, state="not_collected", historical=_view(older))
    if reason is None:
        if latest.state == "stats_reset":
            reason = "stats_reset"
        elif policy.checked_at is not None and policy.checked_at != latest.evaluation_at:
            reason = "probe_failed"
        else:
            valid_until = latest.captured_at + _STALE_AFTER
            if not latest.captured_at <= datetime.now(timezone.utc) <= valid_until:
                reason = "observation_stale"
            elif not await _fence_current(session, policy):
                reason = "target_changed"
            else:
                state = "no_stats" if latest.state == "no_stats" else "current"
                return AssetCollectedStats(available=True, state=state, current=(
                    _view(latest) or AssetStatsObservation(capturedAt=latest.captured_at,
                                                          policyGeneration=latest.policy_generation,
                                                          planUpdatedAt=latest.plan_updated_at)
                ), historical=_view(older), validUntil=valid_until)
    return AssetCollectedStats(available=True, state="unknown", reason=reason, historical=_view(latest) or _view(older))
