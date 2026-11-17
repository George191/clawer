"""Exact verified native bigint range reader for the local managed snapshot pilot."""

import json
from uuid import UUID

from fastapi import HTTPException

from app.web.services.postgres_sync_source import SourceIdentityMismatch, _require_identity_marker, _saved_loopback_source

_RELATION = "batch_sync_range_fixture.records"
_LOCK = f"LOCK TABLE ONLY {_RELATION} IN SHARE MODE NOWAIT"
_SAMPLE = f"""
    SELECT r.id, CASE WHEN pg_catalog.octet_length(r.value) <= 8128 THEN r.value END AS value
    FROM ONLY {_RELATION} AS r WHERE r.id >= $1::bigint AND r.id <= $2::bigint ORDER BY r.id ASC LIMIT 101
"""
_CATALOG = """
    SELECT c.oid, pg_catalog.pg_relation_filenode(c.oid), ic.oid, pg_catalog.pg_relation_filenode(ic.oid), ic.relname::text
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_catalog.pg_am a ON a.oid=c.relam AND a.amname='heap'
    JOIN pg_catalog.pg_index i ON i.indrelid=c.oid
    JOIN pg_catalog.pg_class ic ON ic.oid=i.indexrelid AND ic.relam=403 AND ic.relkind='i'
    JOIN pg_catalog.pg_attribute k ON k.attrelid=c.oid AND k.attnum=i.indkey[0]
    JOIN pg_catalog.pg_opclass op ON op.oid=i.indclass[0] AND op.opcmethod=ic.relam
    WHERE n.nspname='batch_sync_range_fixture' AND c.relname='records'
      AND c.relkind='r' AND c.relpersistence='p' AND NOT c.relispartition
      AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity AND NOT c.relhasrules
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits h WHERE c.oid IN (h.inhrelid,h.inhparent))
      AND pg_catalog.has_schema_privilege(n.oid,'USAGE') AND pg_catalog.has_table_privilege(c.oid,'SELECT')
      AND k.attname='id' AND k.atttypid=20 AND k.attnotnull AND k.attnum>0 AND NOT k.attisdropped
      AND i.indisprimary AND i.indisunique AND i.indimmediate AND i.indisvalid AND i.indisready AND i.indislive
      AND NOT i.indcheckxmin AND i.indnkeyatts=1 AND i.indnatts=1 AND i.indpred IS NULL AND i.indexprs IS NULL
      AND i.indoption[0]=0 AND op.opcdefault AND op.opcintype=20 AND op.opcname='int8_ops'
      AND op.opcnamespace='pg_catalog'::regnamespace
      AND ARRAY(SELECT f.attname::text || ':' || f.atttypid::text FROM pg_catalog.pg_attribute f
                 WHERE f.attrelid=c.oid AND f.attnum>0 AND NOT f.attisdropped ORDER BY f.attname::text COLLATE "C")
           = ARRAY['id:20','value:25']
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute f WHERE f.attrelid=c.oid AND f.attnum>0
                      AND NOT f.attisdropped AND (f.attgenerated<>'' OR NOT f.attnotnull OR f.atttypmod<>-1))
"""


async def _locked_scope(connection, marker: UUID):
    await _require_identity_marker(connection, marker)
    before = await connection.fetchrow(_CATALOG)
    if before is None or any(not item for item in before):
        raise SourceIdentityMismatch()
    await connection.execute(_LOCK)
    after = await connection.fetchrow(_CATALOG)
    if after != before or not await connection.fetchval("""
        SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_locks WHERE pid=pg_catalog.pg_backend_pid()
                       AND locktype='relation' AND relation=$1::oid AND mode='ShareLock' AND granted)
    """, before[0]):
        raise SourceIdentityMismatch()
    return after


async def review_range_snapshot_source(connections, connection_id: str, revision, marker: UUID) -> tuple[int,int,int,int]:
    async with _saved_loopback_source(connections, connection_id, revision) as connection:
        row = await _locked_scope(connection, marker)
        return tuple(row)[:4]


def _index_plan(document, index: str, lower: int, upper: int) -> bool:
    if isinstance(document, str):
        document = json.loads(document)
    if not isinstance(document, list) or len(document) != 1 or not isinstance(document[0], dict):
        return False
    root = document[0].get("Plan")
    if not isinstance(root, dict) or root.get("Node Type") != "Limit" or root.get("Parallel Aware") is not False:
        return False
    plans = root.get("Plans")
    if not isinstance(plans, list) or len(plans) != 1 or not isinstance(plans[0], dict):
        return False
    leaf = plans[0]
    need = f"((r.id >= '{lower}'::bigint) AND (r.id <= '{upper}'::bigint))"
    forbidden = ("Filter", "One-Time Filter", "Recheck Cond", "Subplan Name", "InitPlan", "SubPlan")
    return (leaf.get("Node Type") in ("Index Scan", "Index Only Scan") and leaf.get("Parallel Aware") is False
            and leaf.get("Parent Relationship") == "Outer" and leaf.get("Scan Direction") == "Forward"
            and leaf.get("Schema") == "batch_sync_range_fixture" and leaf.get("Relation Name") == "records"
            and leaf.get("Alias") == "r" and leaf.get("Index Name") == index and leaf.get("Index Cond") == need
            and "Plans" not in leaf and not any(flag in node for node in (root, leaf) for flag in forbidden))


async def read_range_snapshot(connections, connection_id: str, *, expected_updated_at, marker: UUID,
                              pin: tuple[int,int,int,int], lower: int, upper: int) -> list[dict]:
    async with _saved_loopback_source(connections, connection_id, expected_updated_at) as connection:
        row = await _locked_scope(connection, marker)
        if tuple(row)[:4] != pin:
            raise SourceIdentityMismatch()
        await connection.execute("SET LOCAL plan_cache_mode=force_custom_plan")
        plan = await connection.fetchval("EXPLAIN (FORMAT JSON, VERBOSE TRUE, ANALYZE FALSE) " + _SAMPLE, lower, upper)
        if not _index_plan(plan, row[4], lower, upper):
            raise HTTPException(422, "No exact bounded ID range index plan is available; no source rows were selected")
        rows = await connection.fetch(_SAMPLE, lower, upper)
        if len(rows) > 100:
            raise HTTPException(422, "The source exceeds the 100-row batch limit; no batch was read")
        if any(item["value"] is None for item in rows):
            raise HTTPException(422, "A source row exceeds the 8 KiB batch row limit")
        return [dict(item) for item in rows]
