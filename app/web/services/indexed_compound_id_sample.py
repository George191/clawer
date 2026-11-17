"""Bounded Explorer IDs using an exact reviewed native compound PK."""

from ipaddress import ip_address
import re

from anyio import fail_after
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.web.api.models.indexed_id_sample import IndexedCompoundSampleRequest, IndexedSampleCapability, IndexedSampleResult
from app.web.core.config import settings
from app.web.core.config.indexed_sample import IndexedCompoundSampleScope
from app.web.services.etl_layers import _LINEAGE_METADATA_JOIN, _LINEAGE_SCOPE, _LINEAGE_VISIBLE_SQL
from app.web.services.index_preview_sandbox import _approved_plan, _release_preview_transaction
from app.web.services.indexed_id_sample import _limits, _qualified, _reviewed_database


_SCOPE = text(f"""
    SELECT ic.relname::text
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_am heap ON heap.oid = c.relam AND heap.amname = 'heap'
    {_LINEAGE_METADATA_JOIN}
    JOIN pg_catalog.pg_index i ON i.indrelid = c.oid
    JOIN pg_catalog.pg_class ic ON ic.oid = i.indexrelid AND ic.relkind = 'i' AND ic.relam = 403
    JOIN pg_catalog.pg_constraint p ON p.conindid = ic.oid AND p.conrelid = c.oid AND p.contype = 'p'
    WHERE n.nspname = :schema AND c.relname = :table AND {_LINEAGE_VISIBLE_SQL}
      AND c.relkind = 'r' AND c.relpersistence = 'p' AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity
      AND NOT c.relhasrules AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits h WHERE c.oid IN (h.inhparent,h.inhrelid))
      AND c.oid = CAST(:heap_oid AS oid) AND pg_catalog.pg_relation_filenode(c.oid) = CAST(:heap_node AS oid)
      AND ic.oid = CAST(:index_oid AS oid) AND pg_catalog.pg_relation_filenode(ic.oid) = CAST(:index_node AS oid)
      AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
      AND i.indisprimary AND i.indisunique AND i.indimmediate AND i.indisvalid AND i.indisready AND i.indislive
      AND NOT i.indcheckxmin AND i.indnkeyatts = 3 AND i.indnatts = 3 AND NOT p.condeferrable AND p.convalidated
      AND i.indpred IS NULL AND i.indexprs IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM (VALUES (0,'tenant_id'),(1,'group_id'),(2,'id')) AS k(pos,name)
        WHERE NOT EXISTS (
          SELECT 1 FROM pg_catalog.pg_attribute a
          JOIN pg_catalog.pg_opclass op ON op.oid=i.indclass[k.pos] AND op.opcmethod=ic.relam
          WHERE a.attrelid=c.oid AND a.attname=k.name AND a.attnum=i.indkey[k.pos] AND a.attnum=p.conkey[k.pos+1]
            AND a.attnum>0 AND a.attnotnull AND NOT a.attisdropped AND a.attgenerated=''
            AND a.atttypid='pg_catalog.int8'::pg_catalog.regtype
            AND i.indoption[k.pos]=0 AND i.indcollation[k.pos]=0 AND op.opcdefault AND op.opcintype=a.atttypid
            AND op.opcname='int8_ops' AND op.opcnamespace='pg_catalog'::pg_catalog.regnamespace
            AND pg_catalog.has_column_privilege(c.oid,a.attnum,'SELECT')
        )
      )
""")
_ATOM = re.compile(
    r'(?:r\.)?(?:"(tenant_id|group_id|id)"|(tenant_id|group_id|id)) (>=|<=|=) '
    r"(?:'(-?(?:0|[1-9][0-9]{0,18}))'|(-?(?:0|[1-9][0-9]{0,18})))(?:::(?:bigint|int8))?"
)


def _allowed(schema: str, table: str) -> IndexedCompoundSampleScope | None:
    scope = settings.ONTOLOGY_EXPLORER_INDEXED_COMPOUND_SAMPLE_PROFILE
    if (not settings.ONTOLOGY_EXPLORER_INDEXED_COMPOUND_SAMPLE_ENABLED or settings.ENVIRONMENT != "local"
        or "ENVIRONMENT" not in settings.model_fields_set
        or settings.ONTOLOGY_EXPLORER_INDEXED_SAMPLE_DATABASE_MARKER is None
        or scope is None or (scope.schema_name, scope.table) != (schema, table)):
        return None
    return scope


def _origin(session: AsyncSession, scope: IndexedCompoundSampleScope) -> tuple[str, int] | None:
    engine = session.bind
    if not isinstance(engine, AsyncEngine) or engine.url.query or engine.url.drivername != "postgresql+asyncpg":
        return None
    try:
        address = ip_address(engine.url.host)
    except (ValueError, TypeError):
        return None
    port = engine.url.port or 5432
    if not address.is_loopback or "%" in str(address) or not 1 <= port <= 65535 or engine.url.database != scope.database:
        return None
    return str(address), port


async def _catalog(session: AsyncSession, scope: IndexedCompoundSampleScope) -> str | None:
    return await session.scalar(_SCOPE, {
        **_LINEAGE_SCOPE, "schema": scope.schema_name, "table": scope.table,
        "heap_oid": scope.heapOid, "heap_node": scope.heapFilenode, "index_oid": scope.indexOid, "index_node": scope.indexFilenode,
    })


async def _pinned(session: AsyncSession, scope: IndexedCompoundSampleScope, origin: tuple[str, int]) -> str | None:
    await _limits(session)
    if await session.scalar(text("""
        SELECT pg_catalog.inet_server_addr() = CAST(:address AS inet) AND pg_catalog.inet_server_port() = :port
    """), {"address": origin[0], "port": origin[1]}) is not True or not await _reviewed_database(session, scope):
        return None
    index = await _catalog(session, scope)
    if index is None:
        return None
    await session.execute(text(f"LOCK TABLE ONLY {_qualified(scope)} IN SHARE MODE NOWAIT"))
    locked = await session.scalar(text("""
        SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_locks
            WHERE pid=pg_catalog.pg_backend_pid() AND locktype='relation'
              AND relation=CAST(:identity AS oid) AND mode='ShareLock' AND granted)
    """), {"identity": scope.heapOid})
    return index if locked and await _catalog(session, scope) == index else None


def _compiled(scope: IndexedCompoundSampleScope, request: IndexedCompoundSampleRequest):
    return (
        f"SELECT r.id FROM ONLY {_qualified(scope)} AS r "
        "WHERE r.tenant_id = CAST(:tenant AS pg_catalog.int8) AND r.group_id = CAST(:group_id AS pg_catalog.int8) "
        "AND r.id >= CAST(:lo AS pg_catalog.int8) AND r.id <= CAST(:hi AS pg_catalog.int8) ORDER BY r.id ASC LIMIT 21",
        {"tenant": int(request.tenantId), "group_id": int(request.groupId), "lo": int(request.idFrom), "hi": int(request.idTo)},
    )


def _conditions(raw: object, params: dict[str, int]) -> bool:
    if not isinstance(raw, str) or len(raw) > 512:
        return False
    atoms = raw.split(" AND ")
    if len(atoms) != 4:
        return False
    actual = set()
    for atom in atoms:
        part = _ATOM.fullmatch(atom.strip("() "))
        if part is None:
            return False
        quoted, bare, operator, quoted_value, value = part.groups()
        actual.add((quoted or bare, operator, int(quoted_value or value)))
    return actual == {
        ("tenant_id", "=", params["tenant"]), ("group_id", "=", params["group_id"]),
        ("id", ">=", params["lo"]), ("id", "<=", params["hi"]),
    }


async def indexed_compound_sample_capability(session: AsyncSession, schema: str, table: str) -> IndexedSampleCapability:
    scope = _allowed(schema, table)
    origin = _origin(session, scope) if scope else None
    if scope is None or origin is None:
        return IndexedSampleCapability(available=False)
    try:
        with fail_after(2):
            return IndexedSampleCapability(available=await _pinned(session, scope, origin) is not None)
    except (DBAPIError, TimeoutError, OSError):
        return IndexedSampleCapability(available=False)
    finally:
        await _release_preview_transaction(session)


async def sample_indexed_compound_ids(
    session: AsyncSession, schema: str, table: str, request: IndexedCompoundSampleRequest,
) -> IndexedSampleResult:
    blocked = IndexedSampleResult(state="blocked", reason="scope_unavailable")
    scope = _allowed(schema, table)
    origin = _origin(session, scope) if scope else None
    if scope is None or origin is None:
        return blocked
    try:
        index = await _pinned(session, scope, origin)
        if index is None:
            return blocked
        sql, params = _compiled(scope, request)
        plan = await session.scalar(text("EXPLAIN (FORMAT JSON, VERBOSE TRUE, ANALYZE FALSE) " + sql), params)
        if (_approved_plan(plan, index, schema=schema, table=table, only_key="id") is None
            or not _conditions(plan[0]["Plan"]["Plans"][0]["Index Cond"], params)):
            return IndexedSampleResult(state="blocked", reason="unsupported_plan")
        ids = (await session.scalars(text(sql), params)).all()
        return IndexedSampleResult(state="sampled", reason="index_path", ids=[str(key) for key in ids[:20]],
                                   hasMore=len(ids) > 20)
    except (DBAPIError, TimeoutError, OSError):
        return IndexedSampleResult(state="blocked", reason="query_unavailable")
    finally:
        await _release_preview_transaction(session)
