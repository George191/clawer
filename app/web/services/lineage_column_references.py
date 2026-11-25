"""Exact direct column references from visible PostgreSQL/ontology metadata only."""

from anyio import fail_after
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from app.storage.postgres_client import get_pg_client
from app.etl.schema.catalog_metadata import RELATION_RECORDS, catalog_parameters

from app.web.api.models.lineage_neighbors import LineageColumnReferences, LineageDirection
from app.web.services.etl_layers import (
    _LINEAGE_METADATA_JOIN, _LINEAGE_SCOPE, _LINEAGE_VISIBLE_SQL,
)

_LIMIT = 12
_CANONICAL_COLUMN = r"^[a-z_][a-z0-9_]{0,62}$"
_READABLE_REGISTRY = text("""
    SELECT coalesce(pg_catalog.has_table_privilege(
        pg_catalog.to_regclass('app.ontology_object_relations'), 'SELECT'
    ), false) AND pg_catalog.has_schema_privilege('app','USAGE')
""")
_ROOT = text(f"""
    SELECT c.oid AS oid, a.attnum AS attnum
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    {_LINEAGE_METADATA_JOIN}
    JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attname = :column
    WHERE n.nspname = :schema AND c.relname = :table AND {_LINEAGE_VISIBLE_SQL}
      AND a.attnum > 0 AND NOT a.attisdropped
      AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
      AND pg_catalog.has_column_privilege(c.oid, a.attnum, 'SELECT')
""")
_PEER = f"""
    {_LINEAGE_VISIBLE_SQL} AND a.attnum > 0 AND NOT a.attisdropped
      AND a.attname ~ '{_CANONICAL_COLUMN}' AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
      AND pg_catalog.has_column_privilege(c.oid, a.attnum, 'SELECT')
      AND (c.oid <> CAST(:oid AS oid) OR a.attnum <> :attnum)
"""


def _mapped_fields_valid(relation_oid: str, fields: str) -> str:
    # Check the whole enabled mapping even if only one ordinal is requested.
    return f"""
        NOT EXISTS (
            SELECT 1 FROM pg_catalog.unnest({fields}) v(name)
            LEFT JOIN pg_catalog.pg_attribute f ON f.attrelid = {relation_oid} AND f.attname = v.name
                AND f.attnum > 0 AND NOT f.attisdropped
            GROUP BY v.name, f.attnum
            HAVING f.attnum IS NULL OR count(*) <> 1 OR v.name !~ '{_CANONICAL_COLUMN}'
        )
    """


def _references(direction: LineageDirection, has_registry: bool):
    own, peer, own_rel, peer_rel, own_cols, peer_cols = (
        ("source", "target", "conrelid", "confrelid", "conkey", "confkey")
        if direction == "outgoing" else
        ("target", "source", "confrelid", "conrelid", "confkey", "conkey")
    )
    declared = f"""
        SELECT n.nspname::text, c.relname::text, a.attname::text, 'object_relation'
        FROM (SELECT * FROM {RELATION_RECORDS}) d
        JOIN pg_catalog.pg_namespace n ON n.nspname = d.{peer}_schema
        JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid AND c.relname = d.{peer}_table
        {_LINEAGE_METADATA_JOIN}
        JOIN LATERAL pg_catalog.unnest(d.{own}_fields) WITH ORDINALITY src(name, ordinal)
          ON src.name = :column
        JOIN LATERAL pg_catalog.unnest(d.{peer}_fields) WITH ORDINALITY dst(name, ordinal)
          ON dst.ordinal = src.ordinal
        JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attname = dst.name
        WHERE d.enabled AND d.{own}_schema = :schema AND d.{own}_table = :table
          AND pg_catalog.array_ndims(d.{own}_fields) = 1 AND pg_catalog.array_ndims(d.{peer}_fields) = 1
          AND pg_catalog.cardinality(d.{own}_fields) > 0
          AND pg_catalog.cardinality(d.{own}_fields) = pg_catalog.cardinality(d.{peer}_fields)
          AND {_PEER}
          AND {_mapped_fields_valid('CAST(:oid AS oid)', f'd.{own}_fields')}
          AND {_mapped_fields_valid('c.oid', f'd.{peer}_fields')}
        UNION ALL
    """ if has_registry else ""
    return text(f"""
        WITH mapped(schema, "table", "column", kind) AS (
            {declared}
            SELECT n.nspname::text, c.relname::text, a.attname::text, 'foreign_key'
            FROM pg_catalog.pg_constraint f
            JOIN pg_catalog.pg_class c ON c.oid = f.{peer_rel}
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            {_LINEAGE_METADATA_JOIN}
            JOIN LATERAL pg_catalog.unnest(f.{own_cols}) WITH ORDINALITY src(attnum, ordinal) ON src.attnum = :attnum
            JOIN LATERAL pg_catalog.unnest(f.{peer_cols}) WITH ORDINALITY dst(attnum, ordinal) ON dst.ordinal = src.ordinal
            JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = dst.attnum
            WHERE f.contype = 'f' AND f.convalidated AND f.conparentid = 0 AND f.{own_rel} = CAST(:oid AS oid)
              AND {_PEER}
        )
        SELECT schema, "table", "column", pg_catalog.array_agg(DISTINCT kind ORDER BY kind) AS kinds
        FROM mapped GROUP BY schema, "table", "column"
        ORDER BY schema COLLATE "C", "table" COLLATE "C", "column" COLLATE "C"
        LIMIT :limit
    """)


async def read_lineage_column_references(
    session: AsyncSession, schema: str, table: str, column: str, direction: LineageDirection,
) -> LineageColumnReferences:
    params = {**_LINEAGE_SCOPE, "schema": schema, "table": table, "column": column}
    try:
        with fail_after(3):
            async with get_pg_client().session() as metadata_session:
                registry = bool(await metadata_session.scalar(_READABLE_REGISTRY))
                metadata = await catalog_parameters(params, session=metadata_session, relations=registry)
                await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
                await session.execute(text("SET LOCAL search_path=pg_catalog"))
                root = (await session.execute(_ROOT, metadata)).one_or_none()
                if root is None:
                    raise HTTPException(404, "Table or column reference is unavailable")
                result = await session.execute(_references(direction, registry), {
                    **metadata, "oid": root.oid, "attnum": root.attnum, "limit": _LIMIT + 1,
                })
                rows = result.mappings().all()
    except (TimeoutError, DBAPIError):
        raise HTTPException(503, "Column references are temporarily unavailable") from None
    return LineageColumnReferences(
        direction=direction, column=column, references=[dict(row) for row in rows[:_LIMIT]], truncated=len(rows) > _LIMIT,
    )
