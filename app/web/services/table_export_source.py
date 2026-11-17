"""Bounded reader for the pre-provisioned, synthetic-only PostgreSQL export source."""

import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

SOURCE_LABEL = "ts_table_export_sandbox.records"
_SOURCE_SCHEMA = "ts_table_export_sandbox"
MAX_CAPTION_BYTES = 8192
MAX_EXPORT_ROWS = 100

_RELATION_SQL = text("""
    SELECT c.oid, c.relname::text AS name
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_catalog.pg_am am ON am.oid = c.relam AND am.amname = 'heap'
    WHERE n.nspname = :schema AND c.relname IN ('identity', 'records')
      AND c.relkind = 'r' AND c.relpersistence = 'p' AND NOT c.relispartition
      AND NOT c.relrowsecurity AND NOT c.relhasrules
      AND NOT EXISTS (SELECT FROM pg_catalog.pg_inherits i WHERE c.oid IN (i.inhparent, i.inhrelid))
""")
_COLUMNS_SQL = text("""
    SELECT a.attname::text AS name, a.atttypid::bigint AS type_oid, a.attnum
    FROM pg_catalog.pg_attribute a
    WHERE a.attrelid = CAST(:oid AS oid) AND a.attnum > 0 AND NOT a.attisdropped
      AND a.attnotnull AND a.attgenerated = '' AND a.atttypmod = -1
""")
_PRIMARY_SQL = text("""
    SELECT idx.relname::text
    FROM pg_catalog.pg_index ix
    JOIN pg_catalog.pg_class idx ON idx.oid = ix.indexrelid
    JOIN pg_catalog.pg_am am ON am.oid = idx.relam AND am.amname = 'btree'
    JOIN pg_catalog.pg_opclass opc ON opc.oid = ix.indclass[0] AND opc.opcdefault
    WHERE ix.indrelid = CAST(:oid AS oid) AND ix.indisprimary
      AND ix.indisvalid AND ix.indisready AND ix.indislive AND ix.indnkeyatts = 1
      AND ix.indpred IS NULL AND ix.indexprs IS NULL AND ix.indkey[0] = :position
      AND opc.opcnamespace = 'pg_catalog'::regnamespace
""")
_IDENTITY_SQL = text('SELECT marker FROM ONLY ts_table_export_sandbox."identity" WHERE singleton IS TRUE LIMIT 2')
# Never materialize or transfer unbounded text to the sender. The 101st row
# is a sentinel: this pilot rejects oversize sources rather than truncating.
_READ_SQL = f"""
    SELECT id, CASE WHEN pg_catalog.octet_length(caption) <= {MAX_CAPTION_BYTES}
                    THEN caption END AS caption
    FROM ONLY {SOURCE_LABEL} ORDER BY id LIMIT {MAX_EXPORT_ROWS + 1}
"""


def _valid_primary_plan(explain: object, index_name: str) -> bool:
    if isinstance(explain, str):
        try:
            explain = json.loads(explain)
        except ValueError:
            return False
    if not isinstance(explain, list) or len(explain) != 1 or not isinstance(explain[0], dict):
        return False
    plan = explain[0].get("Plan")
    if not isinstance(plan, dict) or plan.get("Node Type") != "Limit":
        return False
    scans = plan.get("Plans")
    if not isinstance(scans, list) or len(scans) != 1 or not isinstance(scans[0], dict):
        return False
    scan = scans[0]
    return (scan.get("Node Type") in ("Index Scan", "Index Only Scan")
            and scan.get("Index Name") == index_name and not scan.get("Plans"))


def _invalid_source() -> HTTPException:
    return HTTPException(409, "The approved synthetic export source does not match its provisioned identity or schema")


async def _read_source(session: AsyncSession, marker: UUID) -> list[tuple[int, str]]:
    # Only these fixed SQL statements can touch source tables. Verify both
    # relations before using them and verify the same OIDs after taking locks.
    if await session.scalar(text("SHOW server_encoding")) != "UTF8":
        raise _invalid_source()
    rows = (await session.execute(_RELATION_SQL, {"schema": _SOURCE_SCHEMA})).mappings().all()
    by_name = {row["name"]: row["oid"] for row in rows}
    if set(by_name) != {"identity", "records"}:
        raise _invalid_source()
    await session.execute(text(
        'LOCK TABLE ONLY ts_table_export_sandbox."identity", '
        'ONLY ts_table_export_sandbox.records IN ACCESS SHARE MODE NOWAIT'
    ))
    pinned = (await session.execute(text("""
        SELECT relation FROM pg_catalog.pg_locks
        WHERE pid = pg_catalog.pg_backend_pid() AND locktype = 'relation'
          AND mode = 'AccessShareLock' AND granted
          AND relation IN (CAST(:identity AS oid), CAST(:records AS oid))
    """), by_name)).scalars().all()
    if set(pinned) != set(by_name.values()):
        raise _invalid_source()
    for relation, shape, primary in (
        ("identity", {"singleton": 16, "marker": 2950}, "singleton"),
        ("records", {"id": 20, "caption": 25}, "id"),
    ):
        oid = by_name[relation]
        cols = (await session.execute(_COLUMNS_SQL, {"oid": oid})).mappings().all()
        projected = {item["name"]: item for item in cols if item["name"] in shape}
        if {name: item["type_oid"] for name, item in projected.items()} != shape:
            raise _invalid_source()
        index_name = await session.scalar(_PRIMARY_SQL, {"oid": oid, "position": projected[primary]["attnum"]})
        if not index_name:
            raise _invalid_source()

    identities = (await session.execute(_IDENTITY_SQL)).scalars().all()
    if identities != [marker]:
        raise _invalid_source()
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    explain = await session.scalar(text("EXPLAIN (FORMAT JSON) " + _READ_SQL))
    if not _valid_primary_plan(explain, index_name):
        raise _invalid_source()
    result = (await session.execute(text(_READ_SQL))).all()
    if len(result) > MAX_EXPORT_ROWS:
        raise HTTPException(422, "The synthetic source exceeds 100 rows; nothing was exported")
    if any(row.caption is None for row in result):
        raise HTTPException(422, "A source caption exceeds 8 KiB; nothing was exported")
    return [(row.id, row.caption) for row in result]


async def read_table_export_source(session: AsyncSession, *, marker: UUID) -> list[tuple[int, str]]:
    """Read only the fixed approved source, failing closed on identity, shape, or bounds."""
    if not isinstance(marker, UUID):
        raise _invalid_source()
    try:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await session.execute(text("SET LOCAL search_path = pg_catalog, pg_temp"))
        await session.execute(text("SET LOCAL statement_timeout = '2s'"))
        await session.execute(text("SET LOCAL lock_timeout = '750ms'"))
        return await _read_source(session, marker)
    except DBAPIError as failure:
        sqlstate = getattr(failure.orig, "sqlstate", None)
        if sqlstate in ("55P03", "57014", "40001"):
            raise HTTPException(503, "The approved synthetic export source is temporarily busy") from None
        raise _invalid_source() from None
