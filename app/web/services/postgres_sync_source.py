"""Bounded snapshot reader for the local managed PostgreSQL batch pilot."""

from contextlib import asynccontextmanager
from datetime import datetime
from ipaddress import ip_address
import json
import ssl
from typing import Any
from uuid import UUID

from anyio import fail_after
from fastapi import HTTPException

from app.web.api.models.data_connections import DataConnectionPayload
from app.web.services.data_connections import DataConnections, _CATALOG_RELATION_SCOPE, _driver
from app.web.services.synthetic_governance import PROTECTED_SYNTHETIC_SCHEMA

_MAX_ROWS = 100
_MAX_COLUMNS = 32
# 100 * 8 KiB, plus 32 column length fields per row and protocol framing,
# stays below 1 MiB. Oversized variable-length values never cross the wire.
_MAX_ROW_BYTES = 8 * 1024
_FIXED_BYTES_PER_COLUMN = 32
_KEY_TYPES = {20, 21, 23, 2950}  # bigint, smallint, integer, uuid
_LENGTH_TYPES = {17, 25, 1043}  # bytea, text, varchar
_TYPES = _KEY_TYPES | _LENGTH_TYPES | {16, 700, 701, 1082, 1083, 1114, 1184, 1700}


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or len(value.encode("utf-8")) > 63:
        raise HTTPException(422, "Invalid source identifier")
    return '"' + value.replace('"', '""') + '"'


def _lookup_uses_primary_index(explain: Any, expected_index: str) -> bool:
    if isinstance(explain, str):
        explain = json.loads(explain)
    nodes = explain[0].get("Plan", {}) if isinstance(explain, list) and len(explain) == 1 else {}
    children = nodes.get("Plans", [])
    return (
        nodes.get("Node Type") == "Limit"
        and len(children) == 1
        and children[0].get("Node Type") in {"Index Scan", "Index Only Scan"}
        and children[0].get("Index Name") == expected_index
        and not children[0].get("Plans")
    )


class SourceIdentityMismatch(HTTPException):
    """Missing, unsafe or unexpected identity marker; never include upstream values."""

    def __init__(self):
        super().__init__(409, "The saved PostgreSQL source identity could not be confirmed")


@asynccontextmanager
async def _saved_loopback_source(connections: DataConnections, connection_id: str, expected_updated_at: datetime):
    """One exact saved numeric-loopback session with a bounded read-only repeatable-read transaction."""
    stored = await connections._stored(connection_id)
    if stored["updated_at"] != expected_updated_at:
        raise HTTPException(409, "Saved PostgreSQL source changed; validate the sync plan again")
    if stored["engine"] != "postgresql":
        raise HTTPException(422, "Only saved PostgreSQL sources are supported")
    try:
        config = stored["config"]
        payload = DataConnectionPayload(name=stored["name"], engine="postgresql",
                                        config=json.loads(config) if isinstance(config, str) else config)
        address = ip_address(payload.config.host)
        local = address.is_loopback and "%" not in payload.config.host
    except (ValueError, TypeError):
        local = False
    if not local:
        raise HTTPException(422, "Batch sync requires a numeric loopback PostgreSQL source")
    credentials = await connections._credentials(payload, stored)
    password = credentials.get("password") if isinstance(credentials, dict) else None
    if not isinstance(password, str) or not password:
        raise HTTPException(503, "Stored PostgreSQL credentials are unavailable")

    driver = _driver("postgresql")
    connection = None
    try:
        with fail_after(7):
            config = payload.config
            connection = await driver.connect(
                host=str(address), port=config.port, database=config.database, user=config.username, password=password,
                ssl=ssl.create_default_context() if config.tls else False, timeout=3, command_timeout=2,
                server_settings={"search_path": "pg_catalog", "statement_timeout": "1500",
                                 "lock_timeout": "750", "default_transaction_read_only": "on"},
            )
            async with connection.transaction(readonly=True, isolation="repeatable_read"):
                yield connection
    except HTTPException:
        raise
    except Exception as error:
        code = getattr(error, "sqlstate", None)
        if isinstance(error, TimeoutError) or code in {"55P03", "57014"}:
            raise HTTPException(504, "PostgreSQL snapshot could not finish within the time budget") from None
        if code in {"42P01", "42703", "42501"}:
            raise HTTPException(409, "Source table, columns, or read permissions changed during the batch read") from None
        raise HTTPException(502, "PostgreSQL snapshot could not be read using the saved source") from None
    finally:
        if connection is not None:
            connection.terminate()


async def read_batch_sync_snapshot(
    connections: DataConnections, connection_id: str, *, expected_updated_at: datetime,
    schema: str, table: str, columns: list[str], key_column: str,
    identity_marker: UUID | None = None, relation_pin: tuple[int, int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read all rows of a small approved source table or fail with no partial batch."""
    if schema in (PROTECTED_SYNTHETIC_SCHEMA, "batch_sync_identity"):
        raise HTTPException(422, "The reserved synthetic schema is not a batch source")
    relation = f"{_identifier(schema)}.{_identifier(table)}"
    names = [_identifier(column) for column in columns]
    key = _identifier(key_column)
    if not 1 <= len(columns) <= _MAX_COLUMNS or len(set(columns)) != len(columns) or key_column not in columns:
        raise HTTPException(422, "Map between 1 and 32 distinct source columns including the primary key")

    async with _saved_loopback_source(connections, connection_id, expected_updated_at) as connection:
        if identity_marker is not None:
            await _require_identity_marker(connection, identity_marker)
            if relation_pin is None:
                raise SourceIdentityMismatch()
        table_state = await connection.fetchrow(f"""
            SELECT c.oid, pg_catalog.pg_relation_filenode(c.oid) AS file_node,
                   idx.relname::text AS index_name, i.indkey[0] AS key_position
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_am h ON h.oid = c.relam AND h.amname = 'heap'
            JOIN pg_catalog.pg_index i ON i.indrelid = c.oid
            JOIN pg_catalog.pg_class idx ON idx.oid = i.indexrelid
            JOIN pg_catalog.pg_am b ON b.oid = idx.relam AND b.amname = 'btree'
            JOIN pg_catalog.pg_opclass opc ON opc.oid = i.indclass[0] AND opc.opcdefault
            WHERE {_CATALOG_RELATION_SCOPE} AND n.nspname::text = $1 AND c.relname::text = $2
              AND c.relkind = 'r' AND NOT c.relispartition AND NOT c.relrowsecurity AND NOT c.relhasrules
              AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits h
                              WHERE h.inhrelid = c.oid OR h.inhparent = c.oid)
              AND has_table_privilege(c.oid, 'SELECT')
              AND i.indisprimary AND i.indisvalid AND i.indisready AND i.indislive
              AND i.indnkeyatts = 1 AND i.indpred IS NULL AND i.indexprs IS NULL
              AND opc.opcnamespace = 'pg_catalog'::regnamespace
        """, schema, table)
        if table_state is None:
            raise HTTPException(422, "Use a readable ordinary source table with a valid single-column primary key")
        await connection.execute(f"LOCK TABLE ONLY {relation} IN ACCESS SHARE MODE")
        if not await connection.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM pg_catalog.pg_locks
                WHERE pid = pg_catalog.pg_backend_pid() AND locktype = 'relation'
                  AND relation = $1::oid AND mode = 'AccessShareLock' AND granted
            )
        """, table_state["oid"]):
            if relation_pin is not None:
                raise SourceIdentityMismatch()
            raise HTTPException(409, "Source table changed during the batch read")
        if relation_pin is not None:
            file_node = await connection.fetchval("SELECT pg_catalog.pg_relation_filenode($1::oid)", table_state["oid"])
            if (table_state["oid"], file_node) != relation_pin:
                raise SourceIdentityMismatch()
        raw = await connection.fetch("""
            SELECT a.attname::text AS name, a.atttypid AS "typeOid", a.atttypmod AS typmod,
                   NOT a.attnotnull AS nullable, a.attnum AS position
            FROM pg_catalog.pg_attribute a
            WHERE a.attrelid = $1::oid AND a.attnum > 0 AND NOT a.attisdropped
              AND a.attgenerated = '' AND a.attname::text = ANY($2::text[])
              AND has_column_privilege(a.attrelid, a.attnum, 'SELECT')
        """, table_state["oid"], columns)
        by_name = {col["name"]: col for col in raw}
        if len(by_name) != len(columns):
            raise HTTPException(422, "A mapped source column is unavailable or unsupported")
        metadata = []
        sizes = []
        for col, name in zip(columns, names, strict=True):
            item = dict(by_name[col])
            item["primaryKey"] = item.pop("position") == table_state["key_position"]
            oid = item["typeOid"]
            if oid not in _TYPES or (oid == 1700 and not 1 <= ((item["typmod"] - 4) >> 16) <= 100):
                raise HTTPException(422, "Mapped source columns must use supported bounded PostgreSQL types")
            if col == key_column and (not item["primaryKey"] or item["nullable"] or oid not in _KEY_TYPES):
                raise HTTPException(422, "Select the non-null integer or UUID source primary key")
            metadata.append(item)
            if oid in _LENGTH_TYPES:
                sizes.append(f"COALESCE(pg_catalog.octet_length({name}), 0)::bigint")
            elif oid == 1700:
                sizes.append(f"COALESCE(pg_catalog.octet_length({name}::text), 0)::bigint")
        await connection.execute("SET LOCAL enable_seqscan = off")
        sample = f"SELECT {', '.join(names)}, tableoid FROM ONLY {relation} ORDER BY {key} LIMIT {_MAX_ROWS + 1}"
        if not _lookup_uses_primary_index(await connection.fetchval("EXPLAIN (FORMAT JSON) " + sample),
                                          table_state["index_name"]):
            raise HTTPException(422, "No bounded primary-key read plan is available for this source")
        # Position-based result decoding avoids collision with internal aliases.
        selected = ", ".join(f"CASE WHEN guard.ok THEN s.{name} END" for name in names)
        size = " + ".join([str(_FIXED_BYTES_PER_COLUMN * len(names)), *sizes])
        result = await connection.fetch(f"""
            WITH s AS MATERIALIZED ({sample})
            SELECT {selected}, guard.ok, s.tableoid
            FROM s CROSS JOIN LATERAL (SELECT ({size}) <= {_MAX_ROW_BYTES} AS ok) guard
            ORDER BY s.{key}
        """)
        if len(result) > _MAX_ROWS:
            raise HTTPException(422, "The source exceeds the 100-row batch limit; no batch was read")
        if any(row[-1] != table_state["oid"] for row in result):
            raise HTTPException(409, "Source table changed during the batch read")
        if any(not row[-2] for row in result):
            raise HTTPException(422, "A source row exceeds the 8 KiB batch row limit")
        return [dict(zip(columns, row[:-2], strict=True)) for row in result], metadata


async def _require_identity_marker(connection, expected: UUID) -> None:
    """Read at most two fixed identity rows. The saved source role cannot author this metadata."""
    marker_oid = await connection.fetchval("""
        SELECT c.oid FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_am h ON h.oid = c.relam AND h.amname = 'heap'
        JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0
        JOIN pg_catalog.pg_roles r ON r.rolname = current_user
        WHERE n.nspname = 'batch_sync_identity' AND c.relname = 'source_identity'
          AND c.relkind = 'r' AND c.relpersistence = 'p' AND NOT c.relispartition
          AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity AND NOT c.relhasrules
          AND a.attname = 'marker' AND a.atttypid = 'pg_catalog.uuid'::regtype
          AND a.attgenerated = '' AND a.attnotnull AND NOT a.attisdropped
          AND NOT r.rolsuper AND NOT pg_catalog.pg_has_role(c.relowner, 'MEMBER')
          AND NOT pg_catalog.has_schema_privilege(n.oid, 'CREATE')
          AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
          AND pg_catalog.has_table_privilege(c.oid, 'SELECT')
          AND NOT pg_catalog.has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')
          AND NOT pg_catalog.has_column_privilege(c.oid, a.attnum, 'INSERT,UPDATE')
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE c.oid IN (i.inhparent, i.inhrelid))
          AND 1 = (SELECT count(*) FROM pg_catalog.pg_attribute b WHERE b.attrelid = c.oid AND b.attnum > 0)
          AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k
                      WHERE k.conrelid = c.oid AND k.contype = 'p' AND k.conkey = ARRAY[a.attnum])
    """)
    if marker_oid is None:
        raise SourceIdentityMismatch()
    try:
        await connection.execute("LOCK TABLE ONLY batch_sync_identity.source_identity IN ACCESS SHARE MODE")
        locked = await connection.fetchval("""
            SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_locks WHERE pid = pg_catalog.pg_backend_pid()
                           AND locktype = 'relation' AND relation = $1::oid
                           AND mode = 'AccessShareLock' AND granted)
        """, marker_oid)
        if not locked:
            raise SourceIdentityMismatch()
        rows = await connection.fetch("SELECT marker FROM ONLY batch_sync_identity.source_identity LIMIT 2")
    except Exception as error:
        if getattr(error, "sqlstate", None) in {"42P01", "42703", "42501"}:
            raise SourceIdentityMismatch() from None
        raise
    if len(rows) != 1 or rows[0]["marker"] != expected:
        raise SourceIdentityMismatch()


async def review_batch_source(
    connections: DataConnections, connection_id: str, revision: datetime, marker: UUID, *,
    schema: str | None = None, table: str | None = None,
) -> tuple[int, int] | None:
    """Verify only the identity catalog and optional source relation metadata, not business rows."""
    async with _saved_loopback_source(connections, connection_id, revision) as connection:
        await _require_identity_marker(connection, marker)
        if schema is None or table is None:
            return None
        if schema in (PROTECTED_SYNTHETIC_SCHEMA, "batch_sync_identity"):
            raise HTTPException(422, "The reserved synthetic schema is not a batch source")
        relation = f"{_identifier(schema)}.{_identifier(table)}"
        state = await connection.fetchrow("""
            SELECT c.oid, pg_catalog.pg_relation_filenode(c.oid) AS file_node
            FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_catalog.pg_am h ON h.oid = c.relam AND h.amname = 'heap'
            WHERE c.relkind = 'r' AND c.relpersistence = 'p' AND NOT c.relispartition
              AND NOT c.relrowsecurity AND NOT c.relhasrules
              AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE c.oid IN (i.inhparent, i.inhrelid))
              AND n.nspname = $1 AND c.relname = $2 AND pg_catalog.has_table_privilege(c.oid, 'SELECT')
        """, schema, table)
        if not state or not state["file_node"]:
            raise HTTPException(422, "A plain readable source table is required for identity review")
        await connection.execute(f"LOCK TABLE ONLY {relation} IN ACCESS SHARE MODE")
        if not await connection.fetchval("""
            SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_locks WHERE pid = pg_catalog.pg_backend_pid()
                           AND locktype = 'relation' AND relation = $1::oid
                           AND mode = 'AccessShareLock' AND granted)
        """, state["oid"]):
            raise SourceIdentityMismatch()
        file_node = await connection.fetchval("SELECT pg_catalog.pg_relation_filenode($1::oid)", state["oid"])
        if not file_node:
            raise SourceIdentityMismatch()
        return state["oid"], file_node
