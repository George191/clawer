"""Exact allowlisted ordinary ID-only read; no planner fallback or generic filter execution."""

from anyio import fail_after
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.indexed_id_sample import (
    IndexedSampleCapability, IndexedSampleRequest, IndexedSampleResult,
)
from app.web.core.config import settings
from app.web.core.config.indexed_sample import IndexedSampleScope
from app.web.services.etl_layers import (
    _LINEAGE_METADATA_JOIN, _LINEAGE_SCOPE, _LINEAGE_VISIBLE_SQL, quote_identifier,
)
from app.web.services.index_preview_sandbox import _approved_plan, _release_preview_transaction

_LIMIT = 20
_MARKER_TABLE = "public.ontology_explorer_indexed_sample_identity"
_MARKER_CATALOG = text("""
    SELECT c.oid::bigint
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_am heap ON heap.oid = c.relam AND heap.amname = 'heap'
    JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attname = 'marker'
    JOIN pg_catalog.pg_index i ON i.indrelid = c.oid AND i.indisprimary AND i.indisvalid
      AND i.indnkeyatts = 1 AND i.indnatts = 1 AND i.indkey[0] = a.attnum
    WHERE pg_catalog.current_database() = :database
      AND (pg_catalog.inet_server_addr() << inet '127.0.0.0/8' OR pg_catalog.inet_server_addr() = inet '::1')
      AND n.nspname = 'public' AND c.relname = 'ontology_explorer_indexed_sample_identity'
      AND c.relkind = 'r' AND c.relpersistence = 'p' AND NOT c.relrowsecurity AND NOT c.relhasrules
      AND NOT c.relispartition
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits h WHERE c.oid IN (h.inhparent,h.inhrelid))
      AND a.attnum = 1 AND a.attnotnull AND NOT a.attisdropped AND a.attgenerated = ''
      AND a.atttypid = 'pg_catalog.uuid'::pg_catalog.regtype
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute b WHERE b.attrelid = c.oid AND b.attnum > 1
                      AND NOT b.attisdropped)
      AND pg_catalog.has_column_privilege(c.oid, a.attnum, 'SELECT')
""")
_SCOPE = text(f"""
    SELECT ic.relname::text
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_am heap ON heap.oid = c.relam AND heap.amname = 'heap'
    {_LINEAGE_METADATA_JOIN}
    JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attname = :key
    JOIN pg_catalog.pg_index i ON i.indrelid = c.oid AND i.indkey[0] = a.attnum
    JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid AND ic.relkind = 'i' AND ic.relam = 403
    JOIN pg_catalog.pg_constraint p ON p.conindid = ic.oid AND p.conrelid = c.oid AND p.contype = 'p'
    JOIN pg_catalog.pg_opclass op ON op.oid = i.indclass[0] AND op.opcmethod = ic.relam
    WHERE pg_catalog.current_database() = :database
      AND (pg_catalog.inet_server_addr() << inet '127.0.0.0/8'
           OR pg_catalog.inet_server_addr() = inet '::1')
      AND n.nspname = :schema AND c.relname = :table AND {_LINEAGE_VISIBLE_SQL}
      AND c.relkind = 'r' AND c.relpersistence = 'p' AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity
      AND NOT c.relhasrules AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits h WHERE h.inhparent = c.oid)
      AND c.oid = CAST(:heap_oid AS oid) AND pg_catalog.pg_relation_filenode(c.oid) = CAST(:heap_node AS oid)
      AND ic.oid = CAST(:index_oid AS oid) AND pg_catalog.pg_relation_filenode(ic.oid) = CAST(:index_node AS oid)
      AND a.attnum > 0 AND a.attnotnull AND NOT a.attisdropped AND a.attgenerated = ''
      AND a.atttypid = 'pg_catalog.int8'::pg_catalog.regtype
      AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
      AND pg_catalog.has_column_privilege(c.oid, a.attnum, 'SELECT')
      AND i.indisprimary AND i.indisunique AND i.indimmediate AND i.indisvalid AND i.indisready AND i.indislive
      AND NOT i.indcheckxmin AND i.indnkeyatts = 1 AND i.indnatts = 1
      AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indoption[0] = 0
      AND op.opcdefault AND op.opcintype = a.atttypid
      AND op.opcname = 'int8_ops' AND op.opcnamespace = 'pg_catalog'::pg_catalog.regnamespace
""")


def _allowed(schema: str, table: str) -> IndexedSampleScope | None:
    if (not settings.ONTOLOGY_EXPLORER_INDEXED_SAMPLE_ENABLED or settings.ENVIRONMENT != "local"
        or "ENVIRONMENT" not in settings.model_fields_set
        or settings.ONTOLOGY_EXPLORER_INDEXED_SAMPLE_DATABASE_MARKER is None):
        return None
    matches = [scope for scope in settings.ONTOLOGY_EXPLORER_INDEXED_SAMPLE_ALLOWLIST
               if (scope.schema_name, scope.table) == (schema, table)]
    return matches[0] if len(matches) == 1 else None


async def _reviewed_database(session: AsyncSession, scope: IndexedSampleScope) -> bool:
    expected = settings.ONTOLOGY_EXPLORER_INDEXED_SAMPLE_DATABASE_MARKER
    params = {"database": scope.database}
    oid = await session.scalar(_MARKER_CATALOG, params)
    if oid is None or expected is None:
        return False
    await session.execute(text(f"LOCK TABLE ONLY {_MARKER_TABLE} IN ACCESS SHARE MODE NOWAIT"))
    if await session.scalar(_MARKER_CATALOG, params) != oid:
        return False
    markers = (await session.scalars(text(f"SELECT marker FROM ONLY {_MARKER_TABLE} LIMIT 2"))).all()
    return len(markers) == 1 and markers[0] == expected


async def _catalog(session: AsyncSession, scope: IndexedSampleScope) -> str | None:
    return await session.scalar(_SCOPE, {
        **_LINEAGE_SCOPE, "database": scope.database, "schema": scope.schema_name, "table": scope.table,
        "key": scope.keyColumn, "heap_oid": scope.heapOid, "heap_node": scope.heapFilenode,
        "index_oid": scope.indexOid, "index_node": scope.indexFilenode,
    })


async def _limits(session: AsyncSession) -> None:
    await session.begin()
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY"))
    await session.execute(text("SET LOCAL statement_timeout='400ms'"))
    await session.execute(text("SET LOCAL lock_timeout='100ms'"))
    await session.execute(text("SET LOCAL search_path=pg_catalog"))
    await session.execute(text("SET LOCAL plan_cache_mode=force_custom_plan"))


async def indexed_sample_capability(session: AsyncSession, schema: str, table: str) -> IndexedSampleCapability:
    scope = _allowed(schema, table)
    if scope is None:
        return IndexedSampleCapability(available=False)
    try:
        with fail_after(2):
            await _limits(session)
            if await _reviewed_database(session, scope) and await _catalog(session, scope) is not None:
                return IndexedSampleCapability(available=True, keyColumn=scope.keyColumn)
    except (DBAPIError, TimeoutError):
        pass
    finally:
        await _release_preview_transaction(session)
    return IndexedSampleCapability(available=False)


def _qualified(scope: IndexedSampleScope) -> str:
    return f"{quote_identifier(scope.schema_name)}.{quote_identifier(scope.table)}"


def _compiled(scope: IndexedSampleScope, request: IndexedSampleRequest) -> tuple[str, dict[str, int]]:
    key = f"r.{quote_identifier(scope.keyColumn)}"
    if request.idEquals is not None:
        predicate = f"{key} = CAST(:exact AS pg_catalog.int8)"
        params = {"exact": int(request.idEquals)}
    else:
        predicate = f"{key} >= CAST(:lo AS pg_catalog.int8) AND {key} <= CAST(:hi AS pg_catalog.int8)"
        params = {"lo": int(request.idFrom), "hi": int(request.idTo)}
    return (
        f"SELECT {key} FROM ONLY {_qualified(scope)} AS r WHERE {predicate} ORDER BY {key} ASC LIMIT 21",
        params,
    )


async def sample_indexed_ids(
    session: AsyncSession, schema: str, table: str, request: IndexedSampleRequest,
) -> IndexedSampleResult:
    blocked = IndexedSampleResult(state="blocked", reason="scope_unavailable")
    scope = _allowed(schema, table)
    if scope is None:
        return blocked
    try:
        await _limits(session)
        if not await _reviewed_database(session, scope):
            return blocked
        index = await _catalog(session, scope)
        if index is None:
            return blocked
        # Exclude writers (including changes in heap size used by the planner),
        # ANALYZE and DDL across the proof and the identically bound read.
        await session.execute(text(f"LOCK TABLE ONLY {_qualified(scope)} IN SHARE MODE NOWAIT"))
        if await _catalog(session, scope) != index:
            return blocked
        sql, params = _compiled(scope, request)
        raw = await session.scalar(text("EXPLAIN (FORMAT JSON, VERBOSE TRUE, ANALYZE FALSE) " + sql), params)
        if _approved_plan(raw, index, schema=schema, table=table, only_key=scope.keyColumn) is None:
            return IndexedSampleResult(state="blocked", reason="unsupported_plan")
        ids = (await session.scalars(text(sql), params)).all()
        return IndexedSampleResult(state="sampled", reason="index_path", ids=[str(key) for key in ids[:_LIMIT]],
                                   hasMore=len(ids) > _LIMIT)
    except DBAPIError:
        return IndexedSampleResult(state="blocked", reason="query_unavailable")
    finally:
        await _release_preview_transaction(session)
