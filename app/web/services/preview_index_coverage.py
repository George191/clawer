"""Catalog-only advisory for ordinary Explorer filter and sort shapes, not a query plan."""

from anyio import fail_after
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.etl import PreviewCompositeIndexCoverageRequest, PreviewIndexCoverage, PreviewIndexCoverageInput, PreviewSortIndexCoverageRequest
from app.web.services.etl_layers import _LINEAGE_METADATA_JOIN, _LINEAGE_SCOPE, _LINEAGE_VISIBLE_SQL

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
          AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhparent = c.oid)
    ), requested AS MATERIALIZED (
        SELECT q.field, q.operator,
               CASE q.operator
                   WHEN 'lt' THEN ARRAY[1] WHEN 'lte' THEN ARRAY[2] WHEN 'eq' THEN ARRAY[3]
                   WHEN 'gte' THEN ARRAY[4] WHEN 'gt' THEN ARRAY[5] WHEN 'between' THEN ARRAY[2, 4]
               END AS strategies
        FROM ROWS FROM (
            pg_catalog.unnest(CAST(:fields AS text[])),
            pg_catalog.unnest(CAST(:operators AS text[]))
        ) AS q(field, operator)
    ), fields AS MATERIALIZED (
        SELECT a.attnum, a.atttypid, a.attcollation, q.operator, q.strategies
        FROM asset
        JOIN pg_catalog.pg_attribute a ON a.attrelid = asset.oid
        JOIN requested q ON a.attname = q.field
        WHERE a.attnum > 0 AND NOT a.attisdropped
          AND pg_catalog.has_column_privilege(asset.oid, a.attnum, 'SELECT')
    )
    SELECT CASE
        WHEN NOT EXISTS (SELECT 1 FROM asset) THEN 'missing'
        WHEN (SELECT pg_catalog.count(*) FROM fields) <> :size THEN 'unavailable'
        WHEN EXISTS (
            SELECT 1 FROM fields WHERE atttypid NOT IN (
                'pg_catalog.int2'::pg_catalog.regtype, 'pg_catalog.int4'::pg_catalog.regtype,
                'pg_catalog.int8'::pg_catalog.regtype, 'pg_catalog.numeric'::pg_catalog.regtype,
                'pg_catalog.float4'::pg_catalog.regtype, 'pg_catalog.float8'::pg_catalog.regtype,
                'pg_catalog.text'::pg_catalog.regtype, 'pg_catalog.varchar'::pg_catalog.regtype,
                'pg_catalog.bpchar'::pg_catalog.regtype, 'pg_catalog.bool'::pg_catalog.regtype,
                'pg_catalog.uuid'::pg_catalog.regtype, 'pg_catalog.date'::pg_catalog.regtype,
                'pg_catalog.timestamp'::pg_catalog.regtype, 'pg_catalog.timestamptz'::pg_catalog.regtype
            )
        ) THEN 'unsupported'
        WHEN EXISTS (
            SELECT 1 FROM asset
            JOIN pg_catalog.pg_index i ON i.indrelid = asset.oid
            JOIN pg_catalog.pg_class x ON x.oid = i.indexrelid AND x.relkind = 'i'
            JOIN pg_catalog.pg_am btree ON btree.oid = x.relam AND btree.oid = 403
            WHERE i.indisvalid AND i.indisready AND i.indislive AND NOT i.indcheckxmin
              AND i.indnkeyatts >= :size AND i.indpred IS NULL AND i.indexprs IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM fields f
                  WHERE NOT EXISTS (
                      SELECT 1 FROM pg_catalog.generate_series(0, CAST(:size AS integer) - 1) AS prefix(pos)
                      JOIN pg_catalog.pg_opclass k ON k.oid = i.indclass[prefix.pos] AND k.opcmethod = btree.oid
                      WHERE i.indkey[prefix.pos] = f.attnum AND i.indcollation[prefix.pos] = f.attcollation
                        AND (f.operator = 'eq' OR prefix.pos = :size - 1)
                        AND k.opcdefault AND k.opcintype = CASE f.atttypid
                            WHEN 'pg_catalog.varchar'::pg_catalog.regtype THEN 'pg_catalog.text'::pg_catalog.regtype
                            ELSE f.atttypid END
                        AND k.opcnamespace = 'pg_catalog'::pg_catalog.regnamespace
                        AND NOT EXISTS (
                            SELECT 1 FROM pg_catalog.unnest(f.strategies) strategy(number)
                            WHERE NOT EXISTS (
                                SELECT 1 FROM pg_catalog.pg_amop o
                                JOIN pg_catalog.pg_operator op ON op.oid = o.amopopr
                                WHERE o.amopfamily = k.opcfamily AND o.amopmethod = btree.oid
                                  AND o.amoplefttype = k.opcintype AND o.amoprighttype = k.opcintype
                                  AND o.amoppurpose = 's' AND o.amopstrategy = strategy.number
                                  AND op.oprnamespace = 'pg_catalog'::pg_catalog.regnamespace
                            )
                        )
                  )
              )
        ) THEN 'candidate'
        ELSE 'no_candidate'
    END
""")


_SORT_COVERAGE = text(f"""
    WITH asset AS MATERIALIZED (
        SELECT c.oid FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_am heap ON heap.oid = c.relam
        {_LINEAGE_METADATA_JOIN}
        WHERE n.nspname = :schema AND c.relname = :table AND {_LINEAGE_VISIBLE_SQL}
          AND c.relkind = 'r' AND c.relpersistence = 'p' AND heap.amname = 'heap'
          AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhparent = c.oid)
    ), requested AS MATERIALIZED (
        SELECT q.field, q.direction, CAST(q.ordinality - 1 AS integer) AS pos
        FROM ROWS FROM (
            pg_catalog.unnest(CAST(:fields AS text[])),
            pg_catalog.unnest(CAST(:directions AS text[]))
        ) WITH ORDINALITY AS q(field, direction, ordinality)
    ), fields AS MATERIALIZED (
        SELECT a.attnum, a.atttypid, a.attcollation, q.direction, q.pos
        FROM asset
        JOIN pg_catalog.pg_attribute a ON a.attrelid = asset.oid
        JOIN requested q ON a.attname = q.field
        WHERE a.attnum > 0 AND NOT a.attisdropped
          AND pg_catalog.has_column_privilege(asset.oid, a.attnum, 'SELECT')
    )
    SELECT CASE
        WHEN NOT EXISTS (SELECT 1 FROM asset) THEN 'missing'
        WHEN (SELECT pg_catalog.count(*) FROM fields) <> :size THEN 'unavailable'
        WHEN EXISTS (
            SELECT 1 FROM fields WHERE atttypid NOT IN (
                'pg_catalog.int2'::pg_catalog.regtype, 'pg_catalog.int4'::pg_catalog.regtype,
                'pg_catalog.int8'::pg_catalog.regtype, 'pg_catalog.numeric'::pg_catalog.regtype,
                'pg_catalog.float4'::pg_catalog.regtype, 'pg_catalog.float8'::pg_catalog.regtype,
                'pg_catalog.text'::pg_catalog.regtype, 'pg_catalog.varchar'::pg_catalog.regtype,
                'pg_catalog.bpchar'::pg_catalog.regtype, 'pg_catalog.bool'::pg_catalog.regtype,
                'pg_catalog.uuid'::pg_catalog.regtype, 'pg_catalog.date'::pg_catalog.regtype,
                'pg_catalog.timestamp'::pg_catalog.regtype, 'pg_catalog.timestamptz'::pg_catalog.regtype
            )
        ) THEN 'unsupported'
        WHEN EXISTS (
            SELECT 1 FROM asset
            JOIN pg_catalog.pg_index i ON i.indrelid = asset.oid
            JOIN pg_catalog.pg_class x ON x.oid = i.indexrelid AND x.relkind = 'i'
            JOIN pg_catalog.pg_am btree ON btree.oid = x.relam AND btree.oid = 403
            WHERE i.indisvalid AND i.indisready AND i.indislive AND NOT i.indcheckxmin
              AND i.indnkeyatts >= :size AND i.indpred IS NULL AND i.indexprs IS NULL
              AND EXISTS (
                  -- A backward scan reverses every key, including its NULL ordering.
                  SELECT 1 FROM (VALUES (0), (3)) AS scan(flip)
                  WHERE NOT EXISTS (
                      SELECT 1 FROM fields f
                      WHERE NOT EXISTS (
                          SELECT 1 FROM pg_catalog.pg_opclass k
                          WHERE k.oid = i.indclass[f.pos] AND k.opcmethod = btree.oid
                            AND i.indkey[f.pos] = f.attnum AND i.indcollation[f.pos] = f.attcollation
                            AND i.indoption[f.pos] = ((CASE f.direction WHEN 'desc' THEN 3 ELSE 0 END) # scan.flip)
                            AND k.opcdefault AND k.opcnamespace = 'pg_catalog'::pg_catalog.regnamespace
                            AND k.opcintype = CASE f.atttypid
                                WHEN 'pg_catalog.varchar'::pg_catalog.regtype THEN 'pg_catalog.text'::pg_catalog.regtype
                                ELSE f.atttypid END
                      )
                  )
              )
        ) THEN 'candidate'
        ELSE 'no_candidate'
    END
""")


async def preview_index_coverage(
    session: AsyncSession, schema: str, table: str, payload: PreviewIndexCoverageInput,
) -> PreviewIndexCoverage:
    if isinstance(payload, PreviewSortIndexCoverageRequest):
        statement = _SORT_COVERAGE
        shape = {"size": len(payload.sorts), "fields": [sort.field for sort in payload.sorts],
                 "directions": [sort.direction for sort in payload.sorts]}
    else:
        terms = payload.terms if isinstance(payload, PreviewCompositeIndexCoverageRequest) else [payload]
        statement = _COVERAGE
        shape = {"size": len(terms), "fields": [term.field for term in terms],
                 "operators": [term.operator for term in terms]}
    try:
        with fail_after(2):
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout='750ms'"))
            state = await session.scalar(statement, {**_LINEAGE_SCOPE, "schema": schema, "table": table, **shape})
    except (TimeoutError, DBAPIError):
        await session.rollback()
        return PreviewIndexCoverage(state="unavailable")
    if state == "missing":
        raise HTTPException(404, "Table does not exist in this schema")
    return PreviewIndexCoverage(state=state)
