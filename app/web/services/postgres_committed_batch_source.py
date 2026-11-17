"""Read one complete committed publication from the fixed synthetic publisher."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from uuid import UUID

from app.web.services.data_connections import DataConnections
from app.web.services.postgres_sync_source import (
    SourceIdentityMismatch, _lookup_uses_primary_index, _require_identity_marker, _saved_loopback_source,
)

SOURCE_SCHEMA = "batch_sync_stream"
SOURCE_TABLE = "batches"
MAX_BATCH_ROWS = 50
MAX_BATCH_BYTES = 65536

_SHAPES = {
    "stream_head": [("singleton", "bool", True), ("ordinal", "int8", False)],
    "batches": [("ordinal", "int8", True), ("row_count", "int4", False), ("manifest", "text", False)],
    "batch_rows": [("ordinal", "int8", True), ("id", "int8", True), ("value", "text", False)],
}
# Fixed names only. Both catalog checks and data reads run in the same source RR transaction.
_LOCK = ("LOCK TABLE ONLY batch_sync_stream.stream_head, ONLY batch_sync_stream.batches, "
         "ONLY batch_sync_stream.batch_rows IN ACCESS SHARE MODE NOWAIT")
_ROWS = f"""SELECT id, value FROM ONLY batch_sync_stream.batch_rows
            WHERE ordinal = $1::bigint ORDER BY id LIMIT {MAX_BATCH_ROWS + 1}"""
_LATEST = "SELECT ordinal FROM ONLY batch_sync_stream.batches ORDER BY ordinal DESC LIMIT 1"
_HEADER = """SELECT row_count, CASE WHEN octet_length(manifest)=64 THEN manifest END AS manifest
             FROM ONLY batch_sync_stream.batches WHERE ordinal=$1::bigint LIMIT 1"""


@dataclass(frozen=True)
class CommittedSourceBatch:
    published_through: int
    ordinal: int | None
    digest: str | None
    rows: list[dict]


def _manifest(rows: list[dict]) -> str:
    # Sorted, delimited UTF-8 hex framing matches the publisher and is unambiguous.
    raw = ";".join(f"{row['id']}:{row['value'].encode('utf-8').hex()}" for row in rows)
    return sha256(raw.encode("ascii")).hexdigest()



def _publication_index_plan(explain, index_name: str, *, exact_ordinal: bool) -> bool:
    if isinstance(explain, str):
        explain = json.loads(explain)
    if not _lookup_uses_primary_index(explain, index_name):
        return False
    scan = explain[0]["Plan"]["Plans"][0]
    if "Filter" in scan or "Recheck Cond" in scan:
        return False
    return not exact_ordinal or bool(scan.get("Index Cond"))


async def _catalog(connection) -> dict:
    tables = await connection.fetch("""
        SELECT c.relname::text AS name, c.oid, pg_catalog.pg_relation_filenode(c.oid) AS node,
               pi.relname::text AS index_name,
               ARRAY(SELECT i.indkey[position]::int FROM generate_series(0, i.indnkeyatts-1) position ORDER BY position) AS keys
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_am h ON h.oid = c.relam AND h.amname = 'heap'
        JOIN pg_catalog.pg_index i ON i.indrelid = c.oid
        JOIN pg_catalog.pg_class pi ON pi.oid = i.indexrelid
        JOIN pg_catalog.pg_am b ON b.oid = pi.relam AND b.amname = 'btree'
        JOIN pg_catalog.pg_roles r ON r.rolname = current_user
        WHERE n.nspname = 'batch_sync_stream' AND c.relname IN ('stream_head','batches','batch_rows')
          AND pg_catalog.current_setting('server_encoding') = 'UTF8'
          AND c.relkind = 'r' AND c.relpersistence = 'p' AND NOT c.relispartition
          AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity AND NOT c.relhasrules
          AND NOT r.rolsuper AND NOT pg_catalog.pg_has_role(c.relowner, 'MEMBER')
          AND NOT pg_catalog.has_schema_privilege(n.oid, 'CREATE')
          AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
          AND pg_catalog.has_table_privilege(c.oid, 'SELECT')
          AND NOT pg_catalog.has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits x WHERE c.oid IN (x.inhparent, x.inhrelid))
          AND i.indisprimary AND i.indisvalid AND i.indisready AND i.indislive AND i.indimmediate
          AND i.indexprs IS NULL AND i.indpred IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM unnest(i.indclass) oc(oid)
              JOIN pg_catalog.pg_opclass o ON o.oid = oc.oid
              WHERE NOT o.opcdefault OR o.opcnamespace <> 'pg_catalog'::regnamespace
          )
    """)
    if len(tables) != len(_SHAPES) or {r["name"] for r in tables} != _SHAPES.keys():
        raise SourceIdentityMismatch()
    metadata = {r["name"]: dict(r) for r in tables}
    attrs = await connection.fetch("""
        SELECT a.attrelid AS oid, a.attnum AS position, a.attname::text AS name,
               t.typname::text AS kind, a.attnotnull, a.attgenerated = '' AND a.attidentity = '' AS ordinary, a.attisdropped,
               pg_catalog.has_column_privilege(a.attrelid, a.attnum, 'INSERT,UPDATE') AS writable
        FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_type t ON t.oid = a.atttypid AND t.typnamespace = 'pg_catalog'::regnamespace
        WHERE a.attrelid = ANY($1::oid[]) AND a.attnum > 0 ORDER BY a.attrelid, a.attnum
    """, [r["oid"] for r in tables])
    for name, row in metadata.items():
        cols = [a for a in attrs if a["oid"] == row["oid"]]
        if not row["node"] or len(cols) != len(_SHAPES[name]):
            raise SourceIdentityMismatch()
        if any(not a["attnotnull"] or not a["ordinary"] or a["attisdropped"] or a["writable"]
               for a in cols):
            raise SourceIdentityMismatch()
        real = [(a["name"], a["kind"], a["position"] in row["keys"]) for a in cols]
        if (real != _SHAPES[name]
            or row["keys"] != [a["position"] for a, shape in zip(cols, _SHAPES[name], strict=True) if shape[2]]):
            raise SourceIdentityMismatch()
    return metadata


async def _pin(connection) -> tuple[str, dict]:
    try:
        before = await _catalog(connection)
        await connection.execute(_LOCK)
        after = await _catalog(connection)
        held = await connection.fetchval("""
            SELECT count(*) = 3 FROM pg_catalog.pg_locks
            WHERE pid = pg_catalog.pg_backend_pid() AND locktype = 'relation' AND granted
              AND mode = 'AccessShareLock' AND relation = ANY($1::oid[])
        """, [r["oid"] for r in before.values()])
    except Exception as error:
        if getattr(error, "sqlstate", None) in {"42P01", "42501", "42703", "55P03"}:
            raise SourceIdentityMismatch() from None
        raise
    if not held or after != before:
        raise SourceIdentityMismatch()
    scope = [[name, row["oid"], row["node"]] for name, row in sorted(after.items())]
    proof = json.dumps(scope, separators=(",", ":")).encode("ascii")
    return sha256(b"ontology-committed-batch-stream/v1\x00" + proof).hexdigest(), after


async def review_committed_batch_source(
    connections: DataConnections, connection_id: str, revision: datetime, marker: UUID,
) -> str:
    """Review only the safe marker and exact physical catalog pins; no business rows."""
    async with _saved_loopback_source(connections, connection_id, revision) as source:
        await _require_identity_marker(source, marker)
        digest, _ = await _pin(source)
        return digest


async def read_committed_batch(
    connections: DataConnections, connection_id: str, *, expected_updated_at: datetime,
    identity_marker: UUID, stream_digest: str, cursor: int,
) -> CommittedSourceBatch:
    """Read exactly cursor+1; never conflate a missing committed batch with an empty stream."""
    if not isinstance(cursor, int) or cursor < 0:
        raise SourceIdentityMismatch()
    async with _saved_loopback_source(connections, connection_id, expected_updated_at) as source:
        await _require_identity_marker(source, identity_marker)
        actual_digest, metadata = await _pin(source)
        if stream_digest != actual_digest:
            raise SourceIdentityMismatch()
        heads = await source.fetch(
            "SELECT singleton, ordinal FROM ONLY batch_sync_stream.stream_head LIMIT 2",
        )
        if len(heads) != 1 or not heads[0]["singleton"] or heads[0]["ordinal"] < cursor:
            raise SourceIdentityMismatch()
        head = heads[0]["ordinal"]
        await source.execute("SET LOCAL enable_seqscan = off; SET LOCAL enable_bitmapscan = off")
        header_index = metadata["batches"]["index_name"]
        latest_plan = await source.fetchval("EXPLAIN (FORMAT JSON) " + _LATEST)
        if not _publication_index_plan(latest_plan, header_index, exact_ordinal=False):
            raise SourceIdentityMismatch()
        latest = await source.fetchval(_LATEST)
        if (latest if latest is not None else 0) != head:
            raise SourceIdentityMismatch()
        if cursor == head:
            return CommittedSourceBatch(head, None, None, [])
        ordinal = cursor + 1
        header_plan = await source.fetchval("EXPLAIN (FORMAT JSON) " + _HEADER, ordinal)
        if not _publication_index_plan(header_plan, header_index, exact_ordinal=True):
            raise SourceIdentityMismatch()
        header = await source.fetchrow(_HEADER, ordinal)
        if not header or not 1 <= header["row_count"] <= MAX_BATCH_ROWS or not header["manifest"]:
            raise SourceIdentityMismatch()
        plan = await source.fetchval("EXPLAIN (FORMAT JSON) " + _ROWS, ordinal)
        if not _publication_index_plan(plan, metadata["batch_rows"]["index_name"], exact_ordinal=True):
            raise SourceIdentityMismatch()
        rows = await source.fetch(f"""
            WITH sample AS MATERIALIZED ({_ROWS}), sized AS (
                SELECT id, value, sum(octet_length(value)::bigint + 8) OVER () AS measured FROM sample
            )
            SELECT id, CASE WHEN measured <= $2::bigint THEN value END AS value FROM sized ORDER BY id
        """, ordinal, MAX_BATCH_BYTES)
        if len(rows) != header["row_count"] or any(r["value"] is None for r in rows):
            raise SourceIdentityMismatch()
        payload = [dict(row) for row in rows]
        manifest = _manifest(payload)
        if manifest != header["manifest"]:
            raise SourceIdentityMismatch()
        return CommittedSourceBatch(head, ordinal, manifest, payload)
