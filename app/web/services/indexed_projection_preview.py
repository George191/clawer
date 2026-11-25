"""Exact covering primary-key index preview for one reviewed ordinary asset."""

import json
import re
from uuid import NAMESPACE_URL, UUID, uuid5

from anyio import fail_after
from app.etl.schema.catalog_metadata import catalog_parameters
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.indexed_id_sample import (
    IndexedProjectionCapability, IndexedProjectionField, IndexedProjectionRequest, IndexedProjectionResult,
)
from app.web.core.config import settings
from app.web.core.config.indexed_sample import IndexedProjectionScope
from app.web.services.etl_layers import (
    _LINEAGE_METADATA_JOIN, _LINEAGE_SCOPE, _LINEAGE_VISIBLE_SQL, quote_identifier, requires_indexed_projection,
)
from app.web.services.index_preview_sandbox import _approved_plan, _release_preview_transaction
from app.web.services.indexed_compound_id_sample import _origin
from app.web.services.indexed_id_sample import _limits, _qualified, _reviewed_database

_SCOPE = text(f"""
    SELECT ic.relname::text
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_catalog.pg_am heap ON heap.oid=c.relam AND heap.amname='heap'
    {_LINEAGE_METADATA_JOIN}
    JOIN pg_catalog.pg_index i ON i.indrelid=c.oid
    JOIN pg_catalog.pg_class ic ON ic.oid=i.indexrelid AND ic.relkind='i'
    JOIN pg_catalog.pg_am btree ON btree.oid=ic.relam AND btree.amname='btree'
    JOIN pg_catalog.pg_constraint p ON p.conindid=ic.oid AND p.conrelid=c.oid AND p.contype='p'
    JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attname=:key AND a.attnum=i.indkey[0]
    JOIN pg_catalog.pg_opclass op ON op.oid=i.indclass[0] AND op.opcmethod=ic.relam
    WHERE n.nspname=:schema AND c.relname=:table AND {_LINEAGE_VISIBLE_SQL}
      AND c.relkind='r' AND c.relpersistence='p' AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity
      AND NOT c.relhasrules AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits h WHERE c.oid IN (h.inhparent,h.inhrelid))
      AND c.oid=CAST(:heap_oid AS oid) AND pg_catalog.pg_relation_filenode(c.oid)=CAST(:heap_node AS oid)
      AND ic.oid=CAST(:index_oid AS oid) AND pg_catalog.pg_relation_filenode(ic.oid)=CAST(:index_node AS oid)
      AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
      AND i.indisprimary AND i.indisunique AND i.indimmediate AND i.indisvalid AND i.indisready AND i.indislive
      AND NOT i.indcheckxmin AND NOT p.condeferrable AND p.convalidated
      AND i.indnkeyatts=1 AND i.indnatts=:width AND i.indpred IS NULL AND i.indexprs IS NULL
      AND i.indoption[0]=0 AND i.indcollation[0]=0
      AND a.attnum=p.conkey[1] AND a.attnum>0 AND a.attnotnull AND NOT a.attisdropped AND a.attgenerated=''
      AND a.atttypid='pg_catalog.int8'::pg_catalog.regtype AND a.atttypmod=-1
      AND op.opcdefault AND op.opcname='int8_ops' AND op.opcintype=a.atttypid
      AND op.opcnamespace='pg_catalog'::pg_catalog.regnamespace
      AND pg_catalog.has_column_privilege(c.oid,a.attnum,'SELECT')
      AND NOT EXISTS (
          SELECT 1
          FROM ROWS FROM (pg_catalog.unnest(CAST(:include_names AS text[])),
                          pg_catalog.unnest(CAST(:include_types AS text[]))) WITH ORDINALITY f(name,kind,pos)
          WHERE NOT EXISTS (
              SELECT 1 FROM pg_catalog.pg_attribute d
              WHERE d.attrelid=c.oid AND d.attnum=i.indkey[f.pos::integer] AND d.attname=f.name
                AND d.attnum>0 AND NOT d.attisdropped AND d.attgenerated=''
                AND pg_catalog.has_column_privilege(c.oid,d.attnum,'SELECT')
                AND ((f.kind='boolean' AND d.atttypid='pg_catalog.bool'::pg_catalog.regtype AND d.atttypmod=-1)
                  OR (f.kind='varchar(32)' AND d.atttypid='pg_catalog.varchar'::pg_catalog.regtype AND d.atttypmod=36))
          )
      )
""")
_NATIVE = text("""
    SELECT pg_catalog.inet_server_addr()=CAST(:address AS inet) AND pg_catalog.inet_server_port()=:port
       AND pg_catalog.current_database()=:database AND session_user::text=:user AND current_user::text=:user
""")
_HELD = text("""
    SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_locks
        WHERE pid=pg_catalog.pg_backend_pid() AND locktype='relation' AND relation=CAST(:identity AS oid)
          AND mode='ShareLock' AND granted)
""")


def _allowed(schema: str, table: str) -> IndexedProjectionScope | None:
    scope = settings.ONTOLOGY_EXPLORER_INDEXED_PROJECTION_PROFILE
    if (not settings.ONTOLOGY_EXPLORER_INDEXED_PROJECTION_ENABLED or settings.ENVIRONMENT != "local"
            or "ENVIRONMENT" not in settings.model_fields_set or not requires_indexed_projection(schema, table)
            or scope is None or (scope.schema_name, scope.table) != (schema, table)):
        return None
    return scope


def _columns(scope: IndexedProjectionScope) -> list[str]:
    return [scope.keyColumn, *(field.name for field in scope.include)]


def _fields(scope: IndexedProjectionScope) -> list[IndexedProjectionField]:
    return [IndexedProjectionField(name=scope.keyColumn, type="bigint"),
            *(IndexedProjectionField(name=field.name, type=field.type) for field in scope.include)]


def _revision(scope: IndexedProjectionScope) -> UUID:
    binding = json.dumps(scope.model_dump(mode="json"), separators=(",", ":"), sort_keys=True)
    return uuid5(NAMESPACE_URL, "asiral:explorer-indexed-projection:v1:" + binding)


async def _catalog(session: AsyncSession, scope: IndexedProjectionScope) -> str | None:
    return (await session.execute(_SCOPE, await catalog_parameters({
        **_LINEAGE_SCOPE, "schema": scope.schema_name, "table": scope.table, "key": scope.keyColumn,
        "heap_oid": scope.heapOid, "heap_node": scope.heapFilenode, "index_oid": scope.indexOid,
        "index_node": scope.indexFilenode, "width": 1 + len(scope.include),
        "include_names": [field.name for field in scope.include], "include_types": [field.type for field in scope.include],
    }))).scalar_one_or_none()


async def _pinned(session: AsyncSession, scope: IndexedProjectionScope, origin: tuple[str, int]) -> str | None:
    await _limits(session)
    if await session.scalar(_NATIVE, {
        "address": origin[0], "port": origin[1], "database": scope.database, "user": session.bind.url.username,
    }) is not True or not await _reviewed_database(session, scope, marker=scope.marker):
        return None
    index = await _catalog(session, scope)
    if index is None:
        return None
    await session.execute(text(f"LOCK TABLE ONLY {_qualified(scope)} IN SHARE MODE NOWAIT"))
    locked = await session.scalar(_HELD, {"identity": scope.heapOid})
    return index if locked and await _catalog(session, scope) == index else None


def _compiled(scope: IndexedProjectionScope, request: IndexedProjectionRequest) -> tuple[str, dict[str, int]]:
    key = f"r.{quote_identifier(scope.keyColumn)}"
    cols = ", ".join(f"r.{quote_identifier(name)}" for name in _columns(scope))
    if request.idEquals is not None:
        predicate, params = f"{key} = CAST(:exact AS pg_catalog.int8)", {"exact": int(request.idEquals)}
    else:
        predicate = f"{key} >= CAST(:lo AS pg_catalog.int8) AND {key} <= CAST(:hi AS pg_catalog.int8)"
        params = {"lo": int(request.idFrom), "hi": int(request.idTo)}
    return f"SELECT {cols} FROM ONLY {_qualified(scope)} AS r WHERE {predicate} ORDER BY {key} ASC LIMIT 21", params


def _verified(raw, index: str, scope: IndexedProjectionScope, params: dict[str, int]) -> bool:
    plan = _approved_plan(raw, index, schema=scope.schema_name, table=scope.table)
    if plan is None or plan.scan != "index_only":
        return False
    root = raw[0]["Plan"]
    leaf = root["Plans"][0]
    names = _columns(scope)
    for node in (root, leaf):
        output = node.get("Output")
        if (not isinstance(output, list) or len(output) != len(names)
                or any(value not in (name, f'"{name}"', f"r.{name}", f'r."{name}"') for value, name in zip(output, names))):
            return False
    cond = leaf["Index Cond"]
    if not isinstance(cond, str) or len(cond) > 512:
        return False
    key = re.escape(scope.keyColumn)
    pattern = re.compile(rf'(?:r\.)?(?:"{key}"|{key}) (>=|<=|=) '
                         r"(?:'(-?(?:0|[1-9][0-9]{0,18}))'|(-?(?:0|[1-9][0-9]{0,18})))(?:::(?:bigint|int8))?")
    atoms = cond.split(" AND ")
    expected = {("=", params["exact"])} if "exact" in params else {(">=", params["lo"]), ("<=", params["hi"])}
    if len(atoms) != len(expected):
        return False
    actual = set()
    for atom in atoms:
        match = pattern.fullmatch(atom.strip("() "))
        if match is None:
            return False
        op, quoted, bare = match.groups()
        actual.add((op, int(quoted or bare)))
    return actual == expected


def _project(rows, scope: IndexedProjectionScope, params: dict[str, int]) -> list[list[str | bool | None]] | None:
    if len(rows) > 21:
        return None
    result, previous = [], None
    lower = params.get("exact", params.get("lo"))
    upper = params.get("exact", params.get("hi"))
    for row in rows:
        key = row[0]
        if (len(row) != len(scope.include) + 1 or type(key) is not int or not lower <= key <= upper
                or (previous is not None and key <= previous)):
            return None
        previous = key
        for value, field in zip(row[1:], scope.include):
            if value is None:
                continue
            if field.type == "boolean" and type(value) is not bool:
                return None
            if field.type == "varchar(32)" and (type(value) is not str or len(value) > 32
                                                or len(value.encode("utf-8")) > 128 or "\0" in value):
                return None
        result.append([str(key), *row[1:]])
    return result


async def indexed_projection_capability(session: AsyncSession, schema: str, table: str) -> IndexedProjectionCapability:
    scope = _allowed(schema, table)
    origin = _origin(session, scope) if scope is not None else None
    if scope is None or origin is None:
        return IndexedProjectionCapability(available=False)
    try:
        with fail_after(2):
            if await _pinned(session, scope, origin) is not None and _allowed(schema, table) == scope:
                return IndexedProjectionCapability(available=True, fields=_fields(scope),
                                                   projectionRevision=_revision(scope))
    except (DBAPIError, MultipleResultsFound, TimeoutError, OSError):
        pass
    finally:
        await _release_preview_transaction(session)
    return IndexedProjectionCapability(available=False)


async def preview_indexed_projection(
    session: AsyncSession, schema: str, table: str, request: IndexedProjectionRequest,
) -> IndexedProjectionResult:
    blocked = IndexedProjectionResult(state="blocked", reason="scope_unavailable")
    scope = _allowed(schema, table)
    origin = _origin(session, scope) if scope is not None else None
    if scope is None or origin is None or request.expectedProjectionRevision != _revision(scope):
        return blocked
    try:
        index = await _pinned(session, scope, origin)
        if index is None:
            return blocked
        sql, params = _compiled(scope, request)
        explain = await session.scalar(text("EXPLAIN (FORMAT JSON, VERBOSE TRUE, ANALYZE FALSE) " + sql), params)
        if not _verified(explain, index, scope, params):
            return IndexedProjectionResult(state="blocked", reason="unsupported_plan")
        if (_allowed(schema, table) != scope or await _catalog(session, scope) != index
                or not await _reviewed_database(session, scope, marker=scope.marker)):
            return blocked
        rows = _project((await session.execute(text(sql), params)).all(), scope, params)
        if rows is None:
            return IndexedProjectionResult(state="blocked", reason="query_unavailable")
        if (_allowed(schema, table) != scope or await _catalog(session, scope) != index
                or not await _reviewed_database(session, scope, marker=scope.marker)):
            return blocked
        return IndexedProjectionResult(state="sampled", reason="index_path", rows=rows[:20], hasMore=len(rows) > 20,
                                       fields=_fields(scope), projectionRevision=request.expectedProjectionRevision)
    except (DBAPIError, MultipleResultsFound, TimeoutError, UnicodeError, OSError):
        return IndexedProjectionResult(state="blocked", reason="query_unavailable")
    finally:
        await _release_preview_transaction(session)
