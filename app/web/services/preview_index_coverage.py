"""Catalog-only advisory for a single ordinary Explorer filter, not a query plan."""

from anyio import fail_after
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.etl import PreviewIndexCoverage, PreviewIndexCoverageRequest
from app.web.services.etl_layers import _LINEAGE_METADATA_JOIN, _LINEAGE_SCOPE, _LINEAGE_VISIBLE_SQL

_STRATEGIES = {"lt": [1], "lte": [2], "eq": [3], "gte": [4], "gt": [5], "between": [2, 4]}

# Only names authorized by ordinary physical Explorer scope are considered.
# One statement checks field permission ahead of its exact built-in type, then
# uses pg_index metadata only; no index storage, statistics, or business rows.
_COVERAGE = text(f"""
    WITH asset AS MATERIALIZED (
        SELECT c.oid FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_am heap ON heap.oid = c.relam
        {_LINEAGE_METADATA_JOIN}
        WHERE n.nspname = :schema AND c.relname = :table AND {_LINEAGE_VISIBLE_SQL}
          AND c.relkind = 'r' AND c.relpersistence = 'p' AND heap.amname = 'heap'
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhparent = c.oid)
    ), field AS MATERIALIZED (
        SELECT a.attrelid, a.attnum, a.atttypid FROM asset
        JOIN pg_catalog.pg_attribute a ON a.attrelid = asset.oid AND a.attname = :field
        WHERE a.attnum > 0 AND NOT a.attisdropped
          AND pg_catalog.has_column_privilege(asset.oid, a.attnum, 'SELECT')
    )
    SELECT CASE
        WHEN NOT EXISTS (SELECT 1 FROM asset) THEN 'missing'
        WHEN NOT EXISTS (SELECT 1 FROM field) THEN 'unavailable'
        WHEN NOT EXISTS (
            SELECT 1 FROM field WHERE atttypid IN (
                'pg_catalog.int2'::pg_catalog.regtype, 'pg_catalog.int4'::pg_catalog.regtype,
                'pg_catalog.int8'::pg_catalog.regtype, 'pg_catalog.date'::pg_catalog.regtype,
                'pg_catalog.timestamptz'::pg_catalog.regtype
            )
        ) THEN 'unsupported'
        WHEN EXISTS (
            SELECT 1 FROM field
            JOIN pg_catalog.pg_index i ON i.indrelid = field.attrelid AND i.indkey[0] = field.attnum
            JOIN pg_catalog.pg_class x ON x.oid = i.indexrelid AND x.relkind = 'i'
            JOIN pg_catalog.pg_am btree ON btree.oid = x.relam AND btree.oid = 403
            JOIN pg_catalog.pg_opclass k ON k.oid = i.indclass[0] AND k.opcmethod = btree.oid
            WHERE i.indisvalid AND i.indisready AND i.indislive AND NOT i.indcheckxmin
              AND i.indnkeyatts > 0 AND i.indpred IS NULL AND i.indexprs IS NULL
              AND k.opcdefault AND k.opcintype = field.atttypid
              AND k.opcnamespace = 'pg_catalog'::pg_catalog.regnamespace
              AND NOT EXISTS (
                  SELECT 1 FROM pg_catalog.unnest(CAST(:strategies AS smallint[])) strategy(number)
                  WHERE NOT EXISTS (
                      SELECT 1 FROM pg_catalog.pg_amop o
                      JOIN pg_catalog.pg_operator op ON op.oid = o.amopopr
                      WHERE o.amopfamily = k.opcfamily AND o.amopmethod = btree.oid
                        AND o.amoplefttype = field.atttypid AND o.amoprighttype = field.atttypid
                        AND o.amoppurpose = 's' AND o.amopstrategy = strategy.number
                        AND op.oprnamespace = 'pg_catalog'::pg_catalog.regnamespace
                  )
              )
        ) THEN 'candidate'
        ELSE 'no_candidate'
    END
""")


async def preview_index_coverage(
    session: AsyncSession, schema: str, table: str, payload: PreviewIndexCoverageRequest,
) -> PreviewIndexCoverage:
    try:
        with fail_after(2):
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout='750ms'"))
            state = await session.scalar(_COVERAGE, {
                **_LINEAGE_SCOPE, "schema": schema, "table": table, "field": payload.field,
                "strategies": _STRATEGIES[payload.operator],
            })
    except (TimeoutError, DBAPIError):
        await session.rollback()
        return PreviewIndexCoverage(state="unavailable")
    if state == "missing":
        raise HTTPException(404, "Table does not exist in this schema")
    return PreviewIndexCoverage(state=state)
