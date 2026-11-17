"""Bounded EXPLAIN-first PostgreSQL preview of the exact synthetic Explorer fixture."""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.index_preview_sandbox import (
    IndexSandboxPlan, IndexSandboxPreview, IndexSandboxQuery, IndexSandboxRow, Reason,
)
from app.web.core.config import settings

INDEX_SANDBOX_SCHEMA = "ts_index_preview_sandbox"
INDEX_SANDBOX_TABLE = "records"

_RELATION = text("""
    SELECT c.oid FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_am am ON am.oid = c.relam
    WHERE n.nspname = 'ts_index_preview_sandbox' AND c.relname = 'records'
      AND c.relkind = 'r' AND c.relpersistence = 'p' AND am.amname = 'heap'
      AND NOT c.relispartition AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity AND NOT c.relhasrules
      AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE c.oid IN (i.inhparent, i.inhrelid))
      AND 2 = (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0)
      AND 2 = (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0
               AND NOT a.attisdropped AND a.attnotnull AND a.attgenerated = '' AND a.attidentity = ''
               AND ((a.attname = 'id' AND a.atttypid = 'pg_catalog.int8'::regtype)
                 OR (a.attname = 'label' AND a.atttypid = 'pg_catalog.varchar'::regtype AND a.atttypmod = 132)))
""")

_PRIMARY = text("""
    SELECT ic.relname FROM pg_index i
    JOIN pg_class ic ON ic.oid = i.indexrelid
    JOIN pg_am am ON am.oid = ic.relam
    JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attname = 'id'
    JOIN pg_opclass op ON op.oid = i.indclass[0]
    JOIN pg_namespace ns ON ns.oid = op.opcnamespace
    WHERE i.indrelid = CAST(:relation AS oid) AND ic.relkind = 'i' AND am.amname = 'btree'
      AND i.indisprimary AND i.indisunique AND i.indimmediate AND i.indisvalid AND i.indisready AND i.indislive
      AND i.indnkeyatts = 1 AND i.indkey[0] = a.attnum AND i.indpred IS NULL AND i.indexprs IS NULL
      AND i.indoption[0] & 1 = 0 AND op.opcname = 'int8_ops' AND ns.nspname = 'pg_catalog'
""")

_GUIDANCE: dict[Reason, str] = {
    "index_path": "PostgreSQL chose the ID primary-key index for this bounded query.",
    "table_unavailable": "The exact synthetic records table and column types are required.",
    "primary_index_required": "The synthetic table requires a valid ascending B-tree primary key on ID.",
    "unsupported_plan": "This query did not use the expected ID-index path. Use exact ID or narrow the ID range.",
    "query_unavailable": "The synthetic preview could not be checked safely. Retry after database activity settles.",
}


def index_sandbox_available() -> bool:
    return (settings.ONTOLOGY_INDEX_PREVIEW_SANDBOX_ENABLED
            and settings.ENVIRONMENT == "local" and "ENVIRONMENT" in settings.model_fields_set)


def _query(request: IndexSandboxQuery) -> tuple[str, dict[str, int]]:
    predicates: list[str] = []
    values = {"take": request.limit + 1}
    for param, operator, value in (
        ("exact", "=", request.idEquals), ("lo", ">=", request.idFrom), ("hi", "<=", request.idTo),
    ):
        if value is not None:
            predicates.append(f"r.id {operator} CAST(:{param} AS pg_catalog.int8)")
            values[param] = int(value)
    return (
        'SELECT r.id, r.label FROM ONLY "ts_index_preview_sandbox"."records" AS r WHERE '
        + " AND ".join(predicates) + " ORDER BY r.id ASC LIMIT CAST(:take AS pg_catalog.int4)",
        values,
    )


def _approved_plan(raw: Any, index: str) -> IndexSandboxPlan | None:
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], dict):
        return None
    root = raw[0].get("Plan")
    if not isinstance(root, dict) or root.get("Node Type") != "Limit" or root.get("Parallel Aware") is not False:
        return None
    children = root.get("Plans")
    if not isinstance(children, list) or len(children) != 1 or not isinstance(children[0], dict):
        return None
    leaf = children[0]
    scan = leaf.get("Node Type")
    if scan not in ("Index Scan", "Index Only Scan"):
        return None
    if (leaf.get("Parent Relationship") != "Outer" or leaf.get("Parallel Aware") is not False
            or leaf.get("Schema") != INDEX_SANDBOX_SCHEMA or leaf.get("Relation Name") != INDEX_SANDBOX_TABLE
            or leaf.get("Alias") != "r" or leaf.get("Index Name") != index or leaf.get("Scan Direction") != "Forward"
            or not leaf.get("Index Cond")
            or any(key in node for node in (root, leaf) for key in (
                "Filter", "One-Time Filter", "Recheck Cond", "Subplan Name", "InitPlan",
            )) or "Plans" in leaf):
        return None
    return IndexSandboxPlan(scan="index_only" if scan == "Index Only Scan" else "index")


def _blocked(query: IndexSandboxQuery, reason: Reason) -> IndexSandboxPreview:
    return IndexSandboxPreview(status="blocked", reason=reason, guidance=_GUIDANCE[reason], mode=query.mode)


async def run_index_preview(session: AsyncSession, request: IndexSandboxQuery) -> IndexSandboxPreview:
    """Prove the fixed path before fetching zero or at most 101 synthetic records."""
    if not index_sandbox_available():
        raise HTTPException(404, "Synthetic index preview is disabled")
    sql, params = _query(request)
    try:
        async with session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout = '400ms'"))
            await session.execute(text("SET LOCAL lock_timeout = '100ms'"))
            await session.execute(text("SET LOCAL search_path = pg_catalog"))
            # Give both executions the same bound-specific planning, independent of asyncpg's statement cache.
            await session.execute(text("SET LOCAL plan_cache_mode = force_custom_plan"))
            relation = await session.scalar(_RELATION)
            if relation is None:
                return _blocked(request, "table_unavailable")
            # Exclude DDL/index maintenance/ANALYZE across both plans; ordinary row writes remain permitted.
            await session.execute(text(
                'LOCK TABLE ONLY "ts_index_preview_sandbox"."records" IN SHARE UPDATE EXCLUSIVE MODE NOWAIT',
            ))
            if relation != await session.scalar(_RELATION):
                return _blocked(request, "table_unavailable")
            index = await session.scalar(_PRIMARY, {"relation": relation})
            if index is None:
                return _blocked(request, "primary_index_required")
            raw = await session.scalar(text("EXPLAIN (FORMAT JSON, VERBOSE TRUE, ANALYZE FALSE) " + sql), params)
            plan = _approved_plan(raw, index)
            if plan is None:
                return _blocked(request, "unsupported_plan")
            result = IndexSandboxPreview(
                status="allowed", reason="index_path", guidance=_GUIDANCE["index_path"], mode=request.mode, plan=plan,
            )
            if request.mode == "preview":
                rows = (await session.execute(text(sql), params)).all()
                result.hasMore = len(rows) > request.limit
                result.rows = [IndexSandboxRow(id=str(key), label=label) for key, label in rows[:request.limit]]
            return result
    except DBAPIError:
        return _blocked(request, "query_unavailable")
