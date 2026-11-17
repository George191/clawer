"""Bounded column metadata and main relation heap observations for the sandbox."""

from datetime import datetime, timedelta
from hashlib import sha256
import json

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.asset_freshness import (
    AssetFreshnessPolicyRow, AssetHeapSizePolicy, AssetQualityAssessment, AssetQualityChecks,
    AssetQualityObservationRow, AssetQualityTransition, AssetQualityTransitionRow,
    AssetSchemaBaseline, AssetSchemaColumn, AssetSchemaPreview,
)
from app.web.services.asset_quality_incidents import observe_quality_incident
from app.web.services.transfer_runs import SANDBOX

_MAX_COLUMNS = 128
_HISTORY_LIMIT = 25
_STALE_AFTER = timedelta(minutes=3)
_UNAVAILABLE_SQLSTATES = {"42P01", "42704", "55P03", "57014"}

# Only names, formatted built-in types, typmods, null and primary key order are
# collected. There is no attdefault, expression, row data or dynamic table SQL.
_SCHEMA = text("""
    SELECT a.attname AS name, pg_catalog.format_type(a.atttypid, a.atttypmod) AS type,
           a.atttypmod AS typmod, NOT a.attnotnull AS nullable,
           p.key_position AS primary_key_position,
           tn.nspname = 'pg_catalog' AND (t.typtype = 'b' OR t.typname = 'name') AS built_in
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
    JOIN pg_catalog.pg_type t ON t.oid = a.atttypid
    JOIN pg_catalog.pg_namespace tn ON tn.oid = t.typnamespace
    LEFT JOIN LATERAL (
        SELECT key.key_position FROM pg_catalog.pg_index i
        CROSS JOIN LATERAL pg_catalog.unnest(i.indkey) WITH ORDINALITY AS key(attnum, key_position)
        WHERE i.indrelid = c.oid AND i.indisprimary AND i.indisvalid AND i.indisready AND key.attnum = a.attnum
          AND key.key_position <= i.indnkeyatts
    ) p ON true
    WHERE n.nspname = :schema AND c.relname = :name AND c.oid::bigint = :oid
      AND pg_catalog.pg_relation_filenode(c.oid)::bigint = :filenode
      AND a.attnum > 0 AND NOT a.attisdropped
    ORDER BY a.attnum LIMIT :limit
""")
_HEAP = text("""
    SELECT pg_catalog.pg_relation_size(c.oid, 'main')
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema AND c.relname = :name AND c.oid::bigint = :oid
      AND pg_catalog.pg_relation_filenode(c.oid)::bigint = :filenode
""")


def _params(policy: AssetFreshnessPolicyRow) -> dict:
    return {"schema": SANDBOX, "name": policy.target_table,
            "oid": policy.target_oid, "filenode": policy.target_filenode}


def _matches_baseline(policy: AssetFreshnessPolicyRow) -> bool:
    return bool(policy.schema_baseline
                and policy.schema_baseline["target"] == [policy.target_oid, policy.target_filenode])


async def read_schema_preview(session: AsyncSession, policy: AssetFreshnessPolicyRow) -> AssetSchemaPreview | None:
    """Collect a bounded reviewed snapshot and bind its fingerprint to the physical table."""
    rows = (await session.execute(_SCHEMA, {**_params(policy), "limit": _MAX_COLUMNS + 1})).mappings().all()
    if not rows or len(rows) > _MAX_COLUMNS or any(not row["built_in"] for row in rows):
        return None
    columns = [AssetSchemaColumn(
        name=row["name"], type=row["type"], typmod=row["typmod"], nullable=row["nullable"],
        primaryKeyPosition=row["primary_key_position"],
    ) for row in rows]
    body = {"target": [policy.target_oid, policy.target_filenode],
            "columns": [column.model_dump() for column in columns]}
    fingerprint = sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return AssetSchemaPreview(fingerprint=fingerprint, columns=columns)


def enabled_heap(policy: AssetFreshnessPolicyRow) -> AssetHeapSizePolicy | None:
    """Return configured heap boundaries without JSON number precision loss."""
    lower, upper = policy.heap_min_bytes, policy.heap_max_bytes
    return (AssetHeapSizePolicy(minBytes=str(lower) if lower is not None else None,
                                maxBytes=str(upper) if upper is not None else None)
            if lower is not None or upper is not None else None)


def captured_schema(policy: AssetFreshnessPolicyRow, context_reason: str | None) -> AssetSchemaBaseline | None:
    """Expose the captured baseline only in the exact current authorized table context."""
    if context_reason or not _matches_baseline(policy):
        return None
    return AssetSchemaBaseline.model_validate(policy.schema_baseline["preview"])


def _bytes(value: int | None) -> str | None:
    return str(value) if value is not None else None


async def _current_check(
    session: AsyncSession, policy: AssetFreshnessPolicyRow, kind: str, enabled: bool,
    context_reason: str | None, now: datetime,
) -> AssetQualityAssessment:
    if not enabled:
        return AssetQualityAssessment(state="not_configured")
    if not policy.enabled:
        return AssetQualityAssessment(state="paused")
    sample = await session.get(AssetQualityObservationRow, (policy.target_table, kind), populate_existing=True)
    current = sample if sample and sample.generation == policy.generation else None
    if context_reason:
        return AssetQualityAssessment(state="unknown", reason=context_reason,
                                      checkedAt=current.checked_at if current else None)
    history_rows = (await session.scalars(select(AssetQualityTransitionRow).where(
        AssetQualityTransitionRow.target_table == policy.target_table,
        AssetQualityTransitionRow.kind == kind, AssetQualityTransitionRow.generation == policy.generation,
    ).order_by(AssetQualityTransitionRow.checked_at.desc(), AssetQualityTransitionRow.id.desc())
        .limit(_HISTORY_LIMIT))).all()
    history = [AssetQualityTransition(id=item.id, generation=item.generation, state=item.state,
                                      reason=item.reason, checkedAt=item.checked_at, heapBytes=_bytes(item.heap_bytes))
               for item in history_rows]
    if current is None:
        return AssetQualityAssessment(state="not_checked", history=history)
    valid_until = current.checked_at + _STALE_AFTER
    if not current.checked_at <= now <= valid_until:
        return AssetQualityAssessment(state="unknown", reason="evaluation_stale",
                                      checkedAt=current.checked_at, history=history)
    return AssetQualityAssessment(state=current.state, reason=current.reason, checkedAt=current.checked_at,
                                  validUntil=valid_until if current.state != "unknown" else None,
                                  heapBytes=_bytes(current.heap_bytes), history=history)


async def read_quality_checks(
    session: AsyncSession, policy: AssetFreshnessPolicyRow, context_reason: str | None, now: datetime,
) -> AssetQualityChecks:
    """Read independent checks and their current-generation transition histories."""
    return AssetQualityChecks(
        schema=await _current_check(session, policy, "schema", policy.schema_check_enabled, context_reason, now),
        heapSize=await _current_check(session, policy, "heap_size", enabled_heap(policy) is not None, context_reason, now),
    )


async def _observe(
    session: AsyncSession, policy: AssetFreshnessPolicyRow, kind: str, now: datetime,
    state: str, reason: str | None, heap_bytes: int | None = None,
) -> None:
    current = await session.get(AssetQualityObservationRow, (policy.target_table, kind), populate_existing=True)
    fields = dict(generation=policy.generation, state=state, reason=reason, checked_at=now, heap_bytes=heap_bytes)
    if current is None or (current.generation, current.state, current.reason) != (policy.generation, state, reason):
        session.add(AssetQualityTransitionRow(target_table=policy.target_table, kind=kind, **fields))
    if current is None:
        session.add(AssetQualityObservationRow(target_table=policy.target_table, kind=kind, **fields))
    else:
        for field, value in fields.items():
            setattr(current, field, value)


async def _schema_result(session: AsyncSession, policy: AssetFreshnessPolicyRow) -> tuple[str, str | None]:
    if not _matches_baseline(policy):
        return "unknown", "schema_baseline_required"
    try:
        async with session.begin_nested():
            preview = await read_schema_preview(session, policy)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) not in _UNAVAILABLE_SQLSTATES:
            raise
        preview = None
    if preview is None:
        return "unknown", "schema_unavailable"
    if preview.fingerprint != policy.schema_baseline["preview"]["fingerprint"]:
        return "unhealthy", "schema_drift"
    return "healthy", None


async def _heap_result(session: AsyncSession, policy: AssetFreshnessPolicyRow) -> tuple[str, str | None, int | None]:
    try:
        async with session.begin_nested():
            value = (await session.execute(_HEAP, _params(policy))).scalar_one_or_none()
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) not in _UNAVAILABLE_SQLSTATES:
            raise
        value = None
    if value is None:
        return "unknown", "heap_size_unavailable", None
    if policy.heap_min_bytes is not None and value < policy.heap_min_bytes:
        return "unhealthy", "below_minimum", value
    if policy.heap_max_bytes is not None and value > policy.heap_max_bytes:
        return "unhealthy", "above_maximum", value
    return "healthy", None, value


async def evaluate_asset_quality(
    session: AsyncSession, policy: AssetFreshnessPolicyRow, context_reason: str | None, now: datetime,
) -> None:
    """Independently sample enabled metadata checks under the existing locked policy tick."""
    if policy.schema_check_enabled:
        state, reason = (("unknown", context_reason) if context_reason else await _schema_result(session, policy))
        await _observe(session, policy, "schema", now, state, reason)
        await observe_quality_incident(session, policy, "schema", now, healthy=state == "healthy",
                                      failure="schema_drift" if state == "unhealthy" else None)
    if policy.heap_min_bytes is not None or policy.heap_max_bytes is not None:
        result = (("unknown", context_reason, None) if context_reason else await _heap_result(session, policy))
        await _observe(session, policy, "heap_size", now, *result)
        state, reason, _ = result
        await observe_quality_incident(session, policy, "heap_size", now, healthy=state == "healthy",
                                      failure=reason if state == "unhealthy" else None)
