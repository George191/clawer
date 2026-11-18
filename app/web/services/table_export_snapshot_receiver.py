"""Read the fixed, separately provisioned local snapshot receiver. Never writes."""

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from ipaddress import ip_address
from uuid import NAMESPACE_URL, UUID, uuid5

import asyncpg
from anyio import fail_after

from app.web.core.config import settings

_SCHEMA = "ts_table_export_snapshot_receiver"
_LIMIT_ROWS = 100
_LIMIT_BYTES = 65536
_LIMIT_CAPTION_BYTES = 8192
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_ORDERED = f"FROM ONLY {_SCHEMA}.records ORDER BY business_key LIMIT {_LIMIT_ROWS + 1}"
_LENGTHS = f"SELECT octet_length(caption) AS caption_bytes {_ORDERED}"
_READ = f"SELECT business_key, caption {_ORDERED}"


class SnapshotReceiverUnavailable(Exception):
    def __init__(self) -> None:
        super().__init__("The approved snapshot preview receiver could not be verified")


@dataclass(frozen=True)
class SnapshotReceiverProfile:
    deployment_revision: UUID
    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)
    marker: UUID = field(repr=False)
    proof_sha256: str = field(repr=False)

    @property
    def config_revision(self) -> UUID:
        # User-visible opaque revision is invalidated by changing any approved
        # noncredential scope even if the operator forgot to rotate its UUID.
        return uuid5(NAMESPACE_URL, json.dumps((
            "asiral:table-export-snapshot-preview:v1", str(self.deployment_revision), str(ip_address(self.host)),
            self.port, self.database, self.username, str(self.marker), self.proof_sha256,
        ), separators=(",", ":")))


@dataclass(frozen=True)
class SnapshotReceiverRow:
    business_key: int = field(repr=False)
    caption: str | None = field(repr=False)


def _is_loopback(host: str) -> bool:
    try:
        return "%" not in host and ip_address(host).is_loopback
    except (TypeError, ValueError):
        return False


def _valid_profile(profile: SnapshotReceiverProfile) -> bool:
    s = settings
    return bool(
        "ENVIRONMENT" in s.model_fields_set
        and s.ENVIRONMENT == "local"
        and s.TABLE_EXPORT_SANDBOX_ENABLED
        and s.TABLE_EXPORT_SNAPSHOT_PREVIEW_ENABLED
        and isinstance(profile.deployment_revision, UUID)
        and profile.deployment_revision.int
        and isinstance(profile.marker, UUID)
        and profile.marker.int
        and profile.deployment_revision != profile.marker
        and _is_loopback(profile.host)
        and 1 <= profile.port <= 65535
        and _IDENTIFIER.fullmatch(profile.database)
        and _IDENTIFIER.fullmatch(profile.username)
        and profile.password
        and _HEX_SHA256.fullmatch(profile.proof_sha256)
        and profile.port not in {s.TABLE_EXPORT_RECEIVER_PORT, s.TABLE_EXPORT_SECONDARY_RECEIVER_PORT}
        and profile.marker not in {
            s.TABLE_EXPORT_SOURCE_MARKER, s.TABLE_EXPORT_RECEIVER_MARKER, s.TABLE_EXPORT_SECONDARY_RECEIVER_MARKER,
        }
        and profile.database not in {s.TABLE_EXPORT_RECEIVER_DATABASE, s.TABLE_EXPORT_SECONDARY_RECEIVER_DATABASE}
        and profile.username not in {s.TABLE_EXPORT_RECEIVER_USERNAME, s.TABLE_EXPORT_SECONDARY_RECEIVER_USERNAME}
    )


def configured_snapshot_profile() -> SnapshotReceiverProfile | None:
    s = settings
    if not (s.TABLE_EXPORT_SNAPSHOT_PREVIEW_CONFIG_REVISION and s.TABLE_EXPORT_SNAPSHOT_PREVIEW_MARKER):
        return None
    profile = SnapshotReceiverProfile(
        s.TABLE_EXPORT_SNAPSHOT_PREVIEW_CONFIG_REVISION, s.TABLE_EXPORT_SNAPSHOT_PREVIEW_HOST,
        s.TABLE_EXPORT_SNAPSHOT_PREVIEW_PORT, s.TABLE_EXPORT_SNAPSHOT_PREVIEW_DATABASE,
        s.TABLE_EXPORT_SNAPSHOT_PREVIEW_USERNAME,
        s.TABLE_EXPORT_SNAPSHOT_PREVIEW_PASSWORD.get_secret_value() if s.TABLE_EXPORT_SNAPSHOT_PREVIEW_PASSWORD else "",
        s.TABLE_EXPORT_SNAPSHOT_PREVIEW_MARKER, s.TABLE_EXPORT_SNAPSHOT_PREVIEW_PROOF_SHA256,
    )
    return profile if _valid_profile(profile) else None


def assert_snapshot_sender_distinct(
    profile: SnapshotReceiverProfile, *, sender_port: int, sender_marker: UUID | None,
    sender_database: str, sender_username: str,
) -> None:
    if not (
        type(sender_port) is int and 1 <= sender_port <= 65535 and profile.port != sender_port
        and isinstance(sender_marker, UUID) and sender_marker.int and profile.marker != sender_marker
        and sender_database and sender_database != profile.database
        and sender_username and sender_username != profile.username
    ):
        raise SnapshotReceiverUnavailable()


async def compute_snapshot_relation_proof(connection: asyncpg.Connection) -> str:
    """Compute an enrollment pin while both fixed heaps are locked in ACCESS SHARE."""
    proof, _ = await _inspect_snapshot_relations(connection)
    return proof


async def _inspect_snapshot_relations(connection: asyncpg.Connection) -> tuple[str, str]:
    rows = await connection.fetch("""
        SELECT n.oid::bigint AS schema_oid, c.relname::text AS name, c.oid::bigint AS table_oid,
               pg_relation_filenode(c.oid)::bigint AS table_node,
               idx.relname::text AS index_name, idx.oid::bigint AS index_oid,
               pg_relation_filenode(idx.oid)::bigint AS index_node,
               ARRAY(SELECT a.attname::text || ':' || a.atttypid::text || ':' || a.attnotnull::text
                     FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 ORDER BY a.attnum) AS columns
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_am heap ON heap.oid = c.relam AND heap.amname = 'heap'
        JOIN pg_index i ON i.indrelid = c.oid AND i.indisprimary AND i.indisunique AND i.indisvalid
          AND i.indisready AND i.indislive AND i.indimmediate AND i.indnatts = 1 AND i.indnkeyatts = 1
          AND i.indkey[0] = 1 AND i.indoption[0] = 0 AND i.indcollation[0] = 0
          AND i.indpred IS NULL AND i.indexprs IS NULL
        JOIN pg_class idx ON idx.oid = i.indexrelid AND idx.relkind = 'i' AND idx.relpersistence = 'p'
          AND NOT idx.relispartition AND idx.relnamespace = n.oid
        JOIN pg_am btree ON btree.oid = idx.relam AND btree.amname = 'btree'
        JOIN pg_opclass opc ON opc.oid = i.indclass[0] AND opc.opcdefault AND opc.opcmethod = btree.oid
          AND opc.opcnamespace = 'pg_catalog'::regnamespace
          AND ((c.relname = 'identity' AND opc.opcintype = 'uuid'::regtype)
            OR (c.relname = 'records' AND opc.opcintype = 'int8'::regtype))
        WHERE n.nspname = $1 AND c.relname IN ('identity','records')
          AND c.relkind = 'r' AND c.relpersistence = 'p' AND NOT c.relispartition
          AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity AND NOT c.relhasrules AND NOT c.relhastriggers
          AND NOT EXISTS (SELECT 1 FROM pg_inherits p WHERE p.inhrelid = c.oid OR p.inhparent = c.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = c.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0
                          AND (a.attisdropped OR a.attidentity <> '' OR a.attgenerated <> '' OR a.atttypmod <> -1
                               OR a.atthasdef OR (a.attcollation <> 0 AND a.attcollation <> 'pg_catalog."default"'::regcollation)))
          AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid = c.oid AND k.contype NOT IN ('p', 'n'))
          AND NOT EXISTS (SELECT 1 FROM pg_index alt WHERE alt.indrelid = c.oid AND alt.indexrelid <> idx.oid)
        ORDER BY c.relname
    """, _SCHEMA)
    if {r["name"]: r["columns"] for r in rows} != {
        "identity": ["marker:2950:true"], "records": ["business_key:20:true", "caption:25:false"],
    } or any(not row["table_node"] or not row["index_node"] for row in rows):
        raise SnapshotReceiverUnavailable()
    pins = [tuple(row[key] for key in (
        "schema_oid", "name", "table_oid", "table_node", "index_name", "index_oid", "index_node",
    )) for row in rows]
    return sha256(json.dumps(pins, separators=(",", ":")).encode()).hexdigest(), rows[1]["index_name"]


async def _verify_role_and_endpoint(connection: asyncpg.Connection, profile: SnapshotReceiverProfile) -> None:
    approved = await connection.fetchval("""
        SELECT current_database() = $1 AND current_user = $2 AND session_user = $2
          AND (inet_server_addr() << inet '127.0.0.0/8' OR inet_server_addr() = inet '::1')
          AND inet_server_port() = $3 AND inet_server_addr() = $5::inet AND current_setting('server_encoding') = 'UTF8'
          AND current_setting('transaction_read_only') = 'on'
          AND current_setting('transaction_isolation') = 'repeatable read'
          AND NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls)
          AND NOT EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid)
          AND NOT has_database_privilege(r.oid, d.oid, 'CREATE,TEMP')
          AND NOT EXISTS (SELECT 1 FROM pg_namespace writable WHERE left(writable.nspname, 3) <> 'pg_'
                          AND writable.nspname <> 'information_schema'
                          AND has_schema_privilege(r.oid, writable.oid, 'CREATE'))
          AND NOT pg_has_role(r.oid, d.datdba, 'MEMBER') AND NOT pg_has_role(r.oid, n.nspowner, 'MEMBER')
          AND (SELECT count(*) FROM pg_class c WHERE c.relnamespace = n.oid
               AND c.relname IN ('identity','records') AND c.relkind = 'r'
               AND NOT pg_has_role(r.oid, c.relowner, 'MEMBER')
               AND has_table_privilege(r.oid, c.oid, 'SELECT')
               AND NOT has_table_privilege(r.oid, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
               AND NOT has_any_column_privilege(r.oid, c.oid, 'INSERT,UPDATE,REFERENCES')
               AND NOT EXISTS (SELECT 1 FROM aclexplode(c.relacl) acl
                               WHERE acl.grantee IN (0, r.oid) AND acl.privilege_type = 'MAINTAIN')) = 2
        FROM pg_roles r JOIN pg_database d ON d.datname = current_database()
        JOIN pg_namespace n ON n.nspname = $4
        WHERE r.rolname = current_user
    """, profile.database, profile.username, profile.port, _SCHEMA, ip_address(profile.host))
    if not approved:
        raise SnapshotReceiverUnavailable()


async def _require_pk_plan(connection: asyncpg.Connection, query: str, index_name: str) -> None:
    # Disable expensive alternatives; additionally verify rather than trust planner preferences.
    explain = json.loads(await connection.fetchval(f"EXPLAIN (FORMAT JSON) {query}"))[0]["Plan"]
    nodes = explain.get("Plans", [])
    if explain["Node Type"] != "Limit" or len(nodes) != 1 or (
        nodes[0]["Node Type"], nodes[0].get("Relation Name"), nodes[0].get("Scan Direction"),
        nodes[0].get("Index Name"),
    ) != ("Index Scan", "records", "Forward", index_name):
        raise SnapshotReceiverUnavailable()


async def _read_bounded(connection: asyncpg.Connection, index_name: str) -> tuple[SnapshotReceiverRow, ...]:
    await _require_pk_plan(connection, _LENGTHS, index_name)
    lengths = [row["caption_bytes"] for row in await connection.fetch(_LENGTHS)]
    # Prove aggregate and per-caption budgets before transmitting any values.
    # The exact two passes observe the same immutable MVCC snapshot.
    if (len(lengths) > _LIMIT_ROWS or any(size is not None and size > _LIMIT_CAPTION_BYTES for size in lengths)
            or sum((size or 0) + 8 for size in lengths) > _LIMIT_BYTES):
        raise SnapshotReceiverUnavailable()
    await _require_pk_plan(connection, _READ, index_name)
    rows = await connection.fetch(_READ)
    if len(rows) != len(lengths) or any(
        (len(row["caption"].encode("utf-8")) if row["caption"] is not None else None) != size
        for size, row in zip(lengths, rows, strict=True)
    ):
        raise SnapshotReceiverUnavailable()
    return tuple(SnapshotReceiverRow(row["business_key"], row["caption"]) for row in rows)


async def read_snapshot_preview(
    profile: SnapshotReceiverProfile, *, sender_port: int, sender_marker: UUID,
    sender_database: str, sender_username: str,
) -> tuple[SnapshotReceiverRow, ...]:
    if configured_snapshot_profile() != profile or not _valid_profile(profile):
        raise SnapshotReceiverUnavailable()
    assert_snapshot_sender_distinct(
        profile, sender_port=sender_port, sender_marker=sender_marker,
        sender_database=sender_database, sender_username=sender_username,
    )
    connection = None
    try:
        with fail_after(7):
            connection = await asyncpg.connect(
                host=profile.host, port=profile.port, database=profile.database, user=profile.username,
                password=profile.password, ssl=False, timeout=2, command_timeout=3,
                server_settings={
                    "search_path": "pg_catalog", "statement_timeout": "2000", "lock_timeout": "500",
                    "idle_in_transaction_session_timeout": "4000", "application_name": "clawer_snapshot_preview",
                    "enable_seqscan": "off", "enable_bitmapscan": "off", "enable_sort": "off",
                    "jit": "off",
                },
            )
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                # DDL cannot replace a pinned heap between verification and reading.
                await connection.execute(f"LOCK TABLE ONLY {_SCHEMA}.identity, ONLY {_SCHEMA}.records "
                                         "IN ACCESS SHARE MODE NOWAIT")
                await _verify_role_and_endpoint(connection, profile)
                proof, index_name = await _inspect_snapshot_relations(connection)
                if proof != profile.proof_sha256:
                    raise SnapshotReceiverUnavailable()
                markers = await connection.fetch(f"SELECT marker FROM ONLY {_SCHEMA}.identity LIMIT 2")
                if len(markers) != 1 or markers[0]["marker"] != profile.marker:
                    raise SnapshotReceiverUnavailable()
                rows = await _read_bounded(connection, index_name)
                # A plain read lock prevents heap replacement; recheck native
                # index pins as well before returning the retained sample.
                if await _inspect_snapshot_relations(connection) != (proof, index_name):
                    raise SnapshotReceiverUnavailable()
                return rows
    except SnapshotReceiverUnavailable:
        raise
    except Exception:
        raise SnapshotReceiverUnavailable() from None
    finally:
        if connection is not None:
            connection.terminate()
