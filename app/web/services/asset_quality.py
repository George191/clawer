"""Bounded column metadata and main relation heap observations for the sandbox."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer
from sqlmodel import select

from app.web.api.models.asset_freshness import (
    AssetFreshnessPolicyRow, AssetHeapSizePolicy, AssetQualityAssessment, AssetQualityChecks,
    AssetQualityObservationRow, AssetQualityTransition, AssetQualityTransitionRow,
    AssetSchemaBaseline, AssetSchemaColumn, AssetSchemaDrift, AssetSchemaPreview,
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


# Only the optional detail is gated here; legacy evidence does not depend on
# the generic object registry being installed or readable.
_DRIFT_DETAIL = text("""
    SELECT o.schema_drift
    FROM public.ontology_asset_quality_observations o
    JOIN public.ontology_asset_freshness_policies p ON p.target_table = o.target_table
    JOIN public.ontology_transfer_plans pl ON pl.id = p.plan_id AND pl.updated_at = p.plan_updated_at
      AND pl.source_schema = :schema AND pl.target_schema = :schema AND pl.target_table = p.target_table
    JOIN pg_catalog.pg_namespace n ON n.nspname = :schema
    JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid AND c.relname = p.target_table
    JOIN pg_catalog.pg_am am ON am.oid = c.relam AND am.amname = 'heap'
    WHERE o.target_table = :name AND o.kind = 'schema' AND o.state = 'unhealthy'
      AND o.reason = 'schema_drift' AND o.checked_at = :checked_at
      AND o.generation = :generation AND p.generation = o.generation
      AND p.enabled AND p.schema_check_enabled
      AND pg_catalog.octet_length(o.schema_drift::text) <= 32768
      AND p.schema_baseline -> 'preview' ->> 'fingerprint' = :fingerprint
      AND p.target_oid = :oid AND p.target_filenode = :filenode
      AND c.oid::bigint = :oid AND pg_catalog.pg_relation_filenode(c.oid)::bigint = :filenode
      AND c.relkind = 'r' AND c.relpersistence = 'p' AND NOT c.relispartition
      AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity AND NOT c.relhasrules
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE c.oid IN (i.inhrelid, i.inhparent))
      AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
      AND pg_catalog.has_table_privilege(c.oid, 'SELECT')
      AND NOT EXISTS (
          SELECT 1 FROM public.ontology_objects r WHERE r.schema_name = n.nspname AND r.table_name = c.relname
            AND (r.is_active IS NOT TRUE OR r.visibility = 'hidden')
      )
""")


def _params(policy: AssetFreshnessPolicyRow) -> dict:
    return {"schema": SANDBOX, "name": policy.target_table,
            "oid": policy.target_oid, "filenode": policy.target_filenode}


def _matches_baseline(policy: AssetFreshnessPolicyRow) -> bool:
    return bool(isinstance(policy.schema_baseline, dict)
                and policy.schema_baseline.get("target") == [policy.target_oid, policy.target_filenode]
                and isinstance(policy.schema_baseline.get("preview"), dict)
                and "fingerprint" in policy.schema_baseline["preview"])


async def read_schema_preview(session: AsyncSession, policy: AssetFreshnessPolicyRow) -> AssetSchemaPreview | None:
    """Collect a bounded reviewed snapshot and bind its fingerprint to the physical table."""
    rows = (await session.execute(_SCHEMA, {**_params(policy), "limit": _MAX_COLUMNS + 1})).mappings().all()
    if not rows or len(rows) > _MAX_COLUMNS or any(not row["built_in"] for row in rows):
        return None
    columns = [AssetSchemaColumn(
        name=row["name"], type=row["type"], typmod=row["typmod"], nullable=row["nullable"],
        primaryKeyPosition=row["primary_key_position"],
    ) for row in rows]
    return AssetSchemaPreview(fingerprint=_fingerprint(policy, columns), columns=columns)


def _fingerprint(policy: AssetFreshnessPolicyRow, columns: list[AssetSchemaColumn]) -> str:
    body = {"target": [policy.target_oid, policy.target_filenode],
            "columns": [column.model_dump() for column in columns]}
    return sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _reviewed_preview(policy: AssetFreshnessPolicyRow) -> AssetSchemaPreview | None:
    data = policy.schema_baseline["preview"]
    columns = data.get("columns") if isinstance(data, dict) else None
    if not isinstance(columns, list) or not 1 <= len(columns) <= _MAX_COLUMNS:
        return None
    try:
        baseline = AssetSchemaPreview.model_validate(data)
    except ValidationError:
        return None
    if (len({column.name for column in baseline.columns}) != len(columns)
            or _fingerprint(policy, baseline.columns) != baseline.fingerprint):
        return None
    return baseline


def _diff_schema(baseline: AssetSchemaPreview, preview: AssetSchemaPreview) -> AssetSchemaDrift | None:
    before = {column.name: column for column in baseline.columns}
    after = {column.name: column for column in preview.columns}
    common = before.keys() & after.keys()
    changed = [name for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
    try:
        return AssetSchemaDrift.model_validate({
            "changes": [
                {"kind": "added" if name not in before else "removed" if name not in after else "changed",
                 "column": name, "before": before[name].model_dump() if name in before else None,
                 "after": after[name].model_dump() if name in after else None}
                for name in changed[:20]
            ],
            "relativeOrderChanged": ([name for name in before if name in common]
                                     != [name for name in after if name in common]),
            "truncated": len(changed) > 20,
        })
    except ValidationError:
        return None


def _schema_drift_binding(policy: AssetFreshnessPolicyRow, checked_at: datetime) -> dict:
    return {"generation": policy.generation, "target": [policy.target_oid, policy.target_filenode],
            "fingerprint": policy.schema_baseline["preview"]["fingerprint"],
            "checkedAt": checked_at.astimezone(timezone.utc).isoformat()}


async def _stored_schema_drift(
    session: AsyncSession, policy: AssetFreshnessPolicyRow, sample: AssetQualityObservationRow,
) -> AssetSchemaDrift | None:
    if (sample.kind != "schema" or sample.state != "unhealthy" or sample.reason != "schema_drift"
            or not _matches_baseline(policy)):
        return None
    baseline = _reviewed_preview(policy)
    if baseline is None:
        return None
    binding = _schema_drift_binding(policy, sample.checked_at)
    try:
        async with session.begin_nested():
            stored = await session.scalar(_DRIFT_DETAIL, {
                **_params(policy), "generation": policy.generation, "checked_at": sample.checked_at,
                "fingerprint": binding["fingerprint"],
            })
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) not in _UNAVAILABLE_SQLSTATES | {"42501", "42703"}:
            raise
        return None
    if not isinstance(stored, dict):
        return None
    try:
        if {key: value for key, value in stored.items() if key != "diagnostics"} != binding:
            return None
        diagnostics = AssetSchemaDrift.model_validate(stored["diagnostics"])
    except (KeyError, ValidationError):
        return None
    before = {column.name: column.model_dump() for column in baseline.columns}
    if any((change.before.model_dump() if change.before else None) != before.get(change.column)
           for change in diagnostics.changes):
        return None
    return diagnostics


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
    sample = await session.get(AssetQualityObservationRow, (policy.target_table, kind),
                               options=[defer(AssetQualityObservationRow.schema_drift)], populate_existing=True)
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
                                  heapBytes=_bytes(current.heap_bytes), history=history,
                                  schemaDrift=await _stored_schema_drift(session, policy, current))


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
    state: str, reason: str | None, heap_bytes: int | None = None, *,
    schema_drift: AssetSchemaDrift | None = None,
) -> None:
    current = await session.get(AssetQualityObservationRow, (policy.target_table, kind), populate_existing=True)
    fields = dict(generation=policy.generation, state=state, reason=reason, checked_at=now, heap_bytes=heap_bytes)
    if current is None or (current.generation, current.state, current.reason) != (policy.generation, state, reason):
        session.add(AssetQualityTransitionRow(target_table=policy.target_table, kind=kind, **fields))
    fields["schema_drift"] = ({**_schema_drift_binding(policy, now),
                               "diagnostics": schema_drift.model_dump()}
                              if schema_drift else None)
    if current is None:
        session.add(AssetQualityObservationRow(target_table=policy.target_table, kind=kind, **fields))
    else:
        for field, value in fields.items():
            setattr(current, field, value)


async def _schema_result(
    session: AsyncSession, policy: AssetFreshnessPolicyRow,
) -> tuple[str, str | None, AssetSchemaDrift | None]:
    if not _matches_baseline(policy):
        return "unknown", "schema_baseline_required", None
    try:
        async with session.begin_nested():
            preview = await read_schema_preview(session, policy)
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) not in _UNAVAILABLE_SQLSTATES:
            raise
        preview = None
    if preview is None:
        return "unknown", "schema_unavailable", None
    if preview.fingerprint != policy.schema_baseline["preview"]["fingerprint"]:
        baseline = _reviewed_preview(policy)
        return "unhealthy", "schema_drift", _diff_schema(baseline, preview) if baseline else None
    return "healthy", None, None


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
        state, reason, drift = (("unknown", context_reason, None) if context_reason
                                else await _schema_result(session, policy))
        await _observe(session, policy, "schema", now, state, reason, schema_drift=drift)
        await observe_quality_incident(session, policy, "schema", now, healthy=state == "healthy",
                                      failure="schema_drift" if state == "unhealthy" else None)
    if policy.heap_min_bytes is not None or policy.heap_max_bytes is not None:
        result = (("unknown", context_reason, None) if context_reason else await _heap_result(session, policy))
        await _observe(session, policy, "heap_size", now, *result)
        state, reason, _ = result
        await observe_quality_incident(session, policy, "heap_size", now, healthy=state == "healthy",
                                      failure=reason if state == "unhealthy" else None)
