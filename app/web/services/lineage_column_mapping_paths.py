"""Visible bounded paths of explicit ordinal mappings; never query business data."""

from collections import deque
from dataclasses import dataclass
import re

from anyio import fail_after
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.lineage_column_mapping_paths import ColumnMappingNode, ColumnMappingPath, ColumnMappingPaths
from app.web.api.models.lineage_neighbors import LineageDirection
from app.web.services.etl_layers import _LINEAGE_METADATA_JOIN, _LINEAGE_SCOPE, _LINEAGE_VISIBLE_SQL, is_ordinary_schema
from app.storage.postgres_client import get_pg_client
from app.etl.schema.catalog_metadata import RELATION_RECORDS, catalog_parameters



class ColumnMappingRootUnavailable(Exception):
    """No exact visible/readable root; the response does not say why."""


_MAX_HOPS = 3
_MAX_NODES = 20
_MAX_PEERS = 6
_MAX_FIELDS = 64
_FIELD = r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"

_READABLE_REGISTRY = text("""
    SELECT c.oid::bigint FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_catalog.pg_am heap ON heap.oid=c.relam AND heap.amname='heap' AND heap.amtype='t'
    WHERE n.nspname='app' AND c.relname='helio_object_relations'
      AND c.relkind='r' AND c.relpersistence='p'
      AND NOT (c.relrowsecurity OR c.relforcerowsecurity OR c.relhasrules)
      AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhparent=c.oid OR i.inhrelid=c.oid
      )
      AND pg_catalog.has_schema_privilege(n.oid,'USAGE')
      AND pg_catalog.has_table_privilege(c.oid,'SELECT')
""")
_REGISTRY_LOCK_HELD = text("""
    SELECT EXISTS (
        SELECT 1 FROM pg_catalog.pg_locks l
        WHERE l.pid=pg_catalog.pg_backend_pid() AND l.locktype='relation'
          AND l.mode='AccessShareLock' AND l.granted AND l.relation=CAST(:oid AS oid)
    )
""")

_ROOT = text(f"""
    SELECT c.oid::bigint AS oid, a.attnum::int AS attnum
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attname=:column
    {_LINEAGE_METADATA_JOIN}
    WHERE n.nspname=:schema AND c.relname=:table AND {_LINEAGE_VISIBLE_SQL}
      AND a.attnum>0 AND NOT a.attisdropped
      AND pg_catalog.has_schema_privilege(n.oid,'USAGE')
      AND pg_catalog.has_column_privilege(c.oid,a.attnum,'SELECT')
""")


def _valid_mapping_side(array: str, relation: str) -> str:
    # NULL, noncanonical, missing, revoked, or duplicated fields invalidate
    # the whole relation, even if the selected ordinal itself is readable.
    return f"""
        NOT EXISTS (
            SELECT 1
            FROM pg_catalog.unnest({array}) AS f(name)
            LEFT JOIN pg_catalog.pg_attribute field
              ON field.attrelid={relation} AND field.attname=f.name
             AND field.attnum>0 AND NOT field.attisdropped
            GROUP BY f.name, field.attnum
            HAVING f.name IS NULL OR f.name !~ '{_FIELD}' OR field.attnum IS NULL OR count(*)<>1
                OR NOT pg_catalog.has_column_privilege({relation},field.attnum,'SELECT')
        )
    """


def _mapping_sql(direction: LineageDirection):
    own, peer = ("source", "target") if direction == "outgoing" else ("target", "source")
    return text(f"""
        WITH bounded_mapping AS MATERIALIZED (
            SELECT d.{own}_fields AS own_fields, d.{peer}_fields AS peer_fields,
                   d.{peer}_schema AS peer_schema, d.{peer}_table AS peer_table
            FROM (SELECT * FROM {RELATION_RECORDS}) d
            WHERE d.enabled AND d.{own}_schema=:schema AND d.{own}_table=:table
              AND pg_catalog.array_ndims(d.{own}_fields)=1 AND pg_catalog.array_ndims(d.{peer}_fields)=1
              AND pg_catalog.cardinality(d.{own}_fields) BETWEEN 1 AND {_MAX_FIELDS}
              AND pg_catalog.cardinality(d.{own}_fields)=pg_catalog.cardinality(d.{peer}_fields)
              AND :column=ANY(d.{own}_fields)
        ), neighbors AS (
            SELECT DISTINCT n.nspname::text AS schema, c.relname::text AS "table", a.attname::text AS "column",
                            c.oid::bigint AS oid, a.attnum::int AS attnum
            FROM bounded_mapping d
            JOIN pg_catalog.pg_namespace n ON n.nspname=d.peer_schema
            JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid AND c.relname=d.peer_table
            {_LINEAGE_METADATA_JOIN}
            JOIN LATERAL ROWS FROM (
                 pg_catalog.unnest(d.own_fields), pg_catalog.unnest(d.peer_fields)
            ) WITH ORDINALITY AS pair(own_name, peer_name, ordinal) ON pair.own_name=:column
            JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attname=pair.peer_name
            WHERE {_LINEAGE_VISIBLE_SQL}
              AND a.attnum>0 AND NOT a.attisdropped
              AND pg_catalog.has_schema_privilege(n.oid,'USAGE')
              AND {_valid_mapping_side('d.own_fields', 'CAST(:own_oid AS oid)')}
              AND {_valid_mapping_side('d.peer_fields', 'c.oid')}
              AND NOT EXISTS (
                  SELECT 1 FROM ROWS FROM (
                      pg_catalog.unnest(CAST(:seen_oids AS bigint[])),
                      pg_catalog.unnest(CAST(:seen_attnums AS integer[]))
                  ) seen(oid,attnum)
                  WHERE seen.oid=c.oid::bigint AND seen.attnum=a.attnum::int
              )
        )
        SELECT schema,"table","column",oid,attnum FROM neighbors
        ORDER BY schema COLLATE "C","table" COLLATE "C","column" COLLATE "C"
        LIMIT :cap
    """)


@dataclass(frozen=True)
class _WalkNode:
    key: tuple[int, int]
    event: ColumnMappingNode


async def read_column_mapping_paths(
    session: AsyncSession, schema: str, table: str, column: str, direction: LineageDirection,
) -> ColumnMappingPaths:
    if (direction not in ("incoming", "outgoing") or not is_ordinary_schema(schema)
        or any(re.fullmatch(_FIELD, name, re.ASCII) is None for name in (table, column))):
        raise ColumnMappingRootUnavailable()
    scope = {**_LINEAGE_SCOPE, "schema": schema, "table": table, "column": column}
    try:
        with fail_after(3), session.no_autoflush:
            async with session.begin():
                await _read_parameters(session)
                original_root = (await session.execute(_ROOT, await catalog_parameters(scope))).one_or_none()
                if original_root is None:
                    raise ColumnMappingRootUnavailable()
                async with get_pg_client().session() as metadata_preflight:
                    registry = await metadata_preflight.scalar(_READABLE_REGISTRY)
                if registry is None:
                    return ColumnMappingPaths(status="unavailable", direction=direction)
            async with session.begin(), get_pg_client().session() as metadata_session:
                await _read_parameters(session)
                await _read_parameters(metadata_session)
                # Lock before acquiring the RR snapshot. A same-OID RLS/rule
                # change or replacement after preflight cannot be overlooked.
                await metadata_session.execute(text("LOCK TABLE ONLY app.helio_object_relations IN ACCESS SHARE MODE NOWAIT"))
                metadata = await catalog_parameters(scope, session=metadata_session, relations=True)
                root = (await session.execute(_ROOT, metadata)).one_or_none()
                if root != original_root:
                    raise ColumnMappingRootUnavailable()
                if (await metadata_session.scalar(_READABLE_REGISTRY) != registry
                    or await metadata_session.scalar(_REGISTRY_LOCK_HELD, {"oid": registry}) is not True):
                    return ColumnMappingPaths(status="unavailable", direction=direction)
                first = _WalkNode((root.oid, root.attnum), ColumnMappingNode(schema=schema, table=table, column=column))
                queue = deque([([first], 0)])
                seen = {first.key}
                paths = []
                incomplete = False
                sql = _mapping_sql(direction)
                while queue:
                    nodes, depth = queue.popleft()
                    last = nodes[-1]
                    # At the hop/node horizon, only ask whether another visible unique node exists.
                    capacity = min(_MAX_PEERS, _MAX_NODES - len(seen)) if depth < _MAX_HOPS else 0
                    peers = (await session.execute(sql, {
                        **metadata, **last.event.model_dump(), "own_oid": last.key[0],
                        "seen_oids": [key[0] for key in seen], "seen_attnums": [key[1] for key in seen],
                        "cap": capacity + 1,
                    })).mappings().all()
                    incomplete = incomplete or len(peers) > capacity
                    for peer in peers[:capacity]:
                        next_node = _WalkNode(
                            (peer["oid"], peer["attnum"]),
                            ColumnMappingNode(schema=peer["schema"], table=peer["table"], column=peer["column"]),
                        )
                        seen.add(next_node.key)
                        witness = [*nodes, next_node]
                        # Incoming search walks backwards; wire always follows actual declared arrows.
                        oriented = witness if direction == "outgoing" else reversed(witness)
                        paths.append(ColumnMappingPath(nodes=[node.event for node in oriented]))
                        queue.append((witness, depth + 1))
                return ColumnMappingPaths(
                    status="incomplete" if incomplete else "complete", direction=direction, paths=paths,
                )
    except (DBAPIError, TimeoutError):
        return ColumnMappingPaths(status="unavailable", direction=direction)



async def _read_parameters(session: AsyncSession) -> None:
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    await session.execute(text("SET LOCAL search_path=pg_catalog,pg_temp"))
    await session.execute(text("SET LOCAL statement_timeout='650ms'"))
    await session.execute(text("SET LOCAL lock_timeout='250ms'"))
