"""Dedicated local immutable snapshot publication receiver and enrollment pins."""

import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from ipaddress import ip_address
from uuid import NAMESPACE_URL, UUID, uuid5

import asyncpg
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.web.core.config import settings


SNAPSHOT_PUBLICATION_SCHEMA = "ts_table_export_snapshot_publication"
_SCHEMA = SNAPSHOT_PUBLICATION_SCHEMA
_TABLES = ("generations", "records", "state")
_COLUMNS = {
    "generations": [(1, "generation", 20, True), (2, "previous_generation", 20, True),
                    (3, "request_id", 2950, True), (4, "payload_hash", 25, True),
                    (5, "row_count", 23, True), (6, "recorded_at", 1184, True)],
    "records": [(1, "generation", 20, True), (2, "id", 20, True), (3, "caption", 25, False)],
    "state": [(1, "singleton", 16, True), (2, "marker", 2950, True), (3, "current_generation", 20, True)],
}
_INDEXES = {
    ("generations", "generations_pkey"): (True, [1], [20]),
    ("generations", "generations_request_id_key"): (False, [3], [2950]),
    ("records", "records_pkey"): (True, [1, 2], [20, 20]),
    ("state", "state_pkey"): (True, [1], [16]),
}
_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class SnapshotPublicationReceiverUnavailable(Exception):
    def __init__(self) -> None:
        super().__init__("The approved snapshot publication receiver could not be verified")


@dataclass(frozen=True)
class SnapshotPublicationProfile:
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
        return uuid5(NAMESPACE_URL, json.dumps((
            "asiral:table-export-snapshot-publication:v1", str(self.deployment_revision), str(ip_address(self.host)),
            self.port, self.database, self.username, str(self.marker), self.proof_sha256,
        ), separators=(",", ":")))


def configured_snapshot_publication_profile() -> SnapshotPublicationProfile | None:
    s = settings
    prefix = "TABLE_EXPORT_SNAPSHOT_PUBLICATION_"
    if not ("ENVIRONMENT" in s.model_fields_set and s.ENVIRONMENT == "local"
            and s.TABLE_EXPORT_SANDBOX_ENABLED and s.TABLE_EXPORT_SNAPSHOT_PUBLICATION_ENABLED):
        return None
    def get(name: str):
        return getattr(s, prefix + name)

    try:
        host = ip_address(get("HOST"))
        sender = make_url(str(s.SQLALCHEMY_RUNNABLE_DATABASE_URI))
        sender_address = ip_address(sender.host)
        sender_port = sender.port or 5432
    except (ArgumentError, TypeError, ValueError):
        return None
    if not (sender.drivername == "postgresql+asyncpg" and not sender.query and sender_address.is_loopback
            and "%" not in str(sender_address) and sender.database and sender.username):
        return None
    deployment_revision, marker, secret = get("CONFIG_REVISION"), get("MARKER"), get("PASSWORD")
    port, database, username, proof = get("PORT"), get("DATABASE"), get("USERNAME"), get("PROOF_SHA256")
    other_profiles = ("TABLE_EXPORT_RECEIVER_", "TABLE_EXPORT_SECONDARY_RECEIVER_", "TABLE_EXPORT_SNAPSHOT_PREVIEW_")
    if not (
        host.is_loopback and "%" not in str(host)
        and deployment_revision and deployment_revision.int and marker and marker.int and marker != deployment_revision
        and secret and secret.get_secret_value() and 1 <= port <= 65535 and _IDENTIFIER.fullmatch(database)
        and _IDENTIFIER.fullmatch(username) and _SHA256.fullmatch(proof) and marker != s.TABLE_EXPORT_SOURCE_MARKER
        and port != sender_port and database != sender.database and username != sender.username
        and all(all(getattr(s, p + key) != value for key, value in (
            ("PORT", port), ("DATABASE", database), ("USERNAME", username), ("MARKER", marker),
        )) for p in other_profiles)
    ):
        return None
    return SnapshotPublicationProfile(deployment_revision, str(host), port, database, username,
                                      secret.get_secret_value(), marker, proof)


def assert_snapshot_publication_sender_distinct(
    profile: SnapshotPublicationProfile, *, sender_port: int, sender_database: str,
    sender_username: str, sender_marker: UUID,
) -> None:
    if not (
        type(sender_port) is int and 1 <= sender_port <= 65535 and sender_port != profile.port
        and sender_database and sender_database != profile.database
        and sender_username and sender_username != profile.username
        and isinstance(sender_marker, UUID) and sender_marker.int and sender_marker != profile.marker
    ):
        raise SnapshotPublicationReceiverUnavailable()


@asynccontextmanager
async def snapshot_publication_connection(
    profile: SnapshotPublicationProfile, *, sender_port: int, sender_database: str,
    sender_username: str, sender_marker: UUID,
):
    if configured_snapshot_publication_profile() != profile:
        raise SnapshotPublicationReceiverUnavailable()
    assert_snapshot_publication_sender_distinct(
        profile, sender_port=sender_port, sender_database=sender_database,
        sender_username=sender_username, sender_marker=sender_marker,
    )
    try:
        connection = await asyncpg.connect(
            host=profile.host, port=profile.port, database=profile.database, user=profile.username,
            password=profile.password, ssl=False, timeout=2, command_timeout=3,
            server_settings={"search_path": "pg_catalog", "lock_timeout": "500", "statement_timeout": "2000",
                             "idle_in_transaction_session_timeout": "5000",
                             "application_name": "clawer_snapshot_publication"},
        )
    except Exception:
        raise SnapshotPublicationReceiverUnavailable() from None
    try:
        yield connection
    finally:
        connection.terminate()


async def lock_snapshot_publication_relations(connection: asyncpg.Connection) -> None:
    if not connection.is_in_transaction():
        raise SnapshotPublicationReceiverUnavailable()
    # PG17 scoped MAINTAIN is required for this lock. ACCESS SHARE would
    # permit CREATE TRIGGER, and ROW EXCLUSIVE permits REINDEX CONCURRENTLY.
    # Retain these stronger locks until COMMIT; NOWAIT makes contention retryable.
    await connection.execute(
        f"LOCK TABLE ONLY {_SCHEMA}.state, ONLY {_SCHEMA}.generations, ONLY {_SCHEMA}.records"
        " IN SHARE UPDATE EXCLUSIVE MODE NOWAIT"
    )


async def compute_snapshot_publication_proof(connection: asyncpg.Connection) -> str:
    """Validate/prove enrolled PostgreSQL shape; caller holds fixed locks and pg_catalog search_path."""
    tables = await connection.fetch("""
        SELECT n.oid::bigint AS schema_oid, c.relname::text AS table_name, c.oid::bigint AS table_oid,
               pg_relation_filenode(c.oid)::bigint AS filenode
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_am heap ON heap.oid = c.relam AND heap.amname = 'heap'
        WHERE n.nspname = $1 AND c.relname::text = ANY($2::text[])
          AND c.relkind='r' AND c.relpersistence='p' AND NOT c.relispartition
          AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity AND NOT c.relhasrules AND NOT c.relhastriggers
          AND NOT EXISTS (SELECT 1 FROM pg_inherits h WHERE h.inhrelid=c.oid OR h.inhparent=c.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid)
        ORDER BY c.relname
    """, _SCHEMA, _TABLES)
    if tuple(row["table_name"] for row in tables) != _TABLES or any(not row["filenode"] for row in tables):
        raise SnapshotPublicationReceiverUnavailable()
    pins = [tuple(row.values()) for row in tables]
    oids = [row["table_oid"] for row in tables]
    columns = await connection.fetch("""
        SELECT c.relname::text AS table_name,a.attnum,a.attname::text,a.atttypid::bigint,a.attnotnull,
               (NOT a.attisdropped AND a.attidentity='' AND a.attgenerated='' AND a.atttypmod=-1
                AND NOT a.atthasdef
                AND (a.attcollation=0 OR a.attcollation='pg_catalog."default"'::regcollation)) AS approved
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        WHERE a.attrelid::bigint=ANY($1::bigint[]) AND a.attnum>0 ORDER BY c.relname,a.attnum
    """, oids)
    actual = {name: [] for name in _TABLES}
    for row in columns:
        if not row["approved"]:
            raise SnapshotPublicationReceiverUnavailable()
        actual[row[0]].append(tuple(row.values())[1:5])
    if actual != _COLUMNS:
        raise SnapshotPublicationReceiverUnavailable()

    indexes = await connection.fetch("""
        SELECT c.relname::text AS table_name, idx.relname::text AS index_name,idx.oid::bigint AS index_oid,
               pg_relation_filenode(idx.oid)::bigint AS filenode, i.indisprimary,
               ARRAY(SELECT unnest(i.indkey)::int) AS keys,
               ARRAY(SELECT opc.opcintype::bigint
                     FROM unnest(i.indclass) WITH ORDINALITY AS k(oid,pos)
                     JOIN pg_opclass opc ON opc.oid=k.oid AND opc.opcdefault AND opc.opcmethod=btree.oid
                      AND opc.opcnamespace='pg_catalog'::regnamespace ORDER BY k.pos) AS key_types,
               (idx.relkind='i' AND idx.relpersistence='p' AND NOT idx.relispartition
                AND idx.relnamespace=c.relnamespace AND btree.amname='btree'
                AND i.indisunique AND i.indisvalid AND i.indisready AND i.indislive AND i.indimmediate
                AND i.indnatts=i.indnkeyatts AND i.indpred IS NULL AND i.indexprs IS NULL
                AND NOT EXISTS (SELECT 1 FROM unnest(i.indoption) n WHERE n<>0)
                AND NOT EXISTS (SELECT 1 FROM unnest(i.indcollation) n WHERE n<>0)) AS approved
        FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
        JOIN pg_class idx ON idx.oid=i.indexrelid JOIN pg_am btree ON btree.oid=idx.relam
        WHERE c.oid::bigint=ANY($1::bigint[])
        ORDER BY c.relname,idx.relname
    """, oids)
    actual_indexes = {}
    for row in indexes:
        if not row["approved"] or not row["filenode"]:
            raise SnapshotPublicationReceiverUnavailable()
        actual_indexes[(row["table_name"], row["index_name"])] = (row["indisprimary"], row["keys"], row["key_types"])
        pins.append(tuple(row.values())[:4])
    if actual_indexes != _INDEXES:
        raise SnapshotPublicationReceiverUnavailable()

    checks = await connection.fetch("""
        SELECT c.relname::text,k.conname::text,k.contype::text,k.convalidated,
               CASE WHEN k.contype='c' THEN pg_get_expr(k.conbin,k.conrelid) END
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        WHERE k.conrelid::bigint=ANY($1::bigint[]) AND k.contype<>'n'
    """, oids)
    if {tuple(row.values()) for row in checks} != {
        ("state", "state_singleton_check", "c", True, "singleton"),
        ("state", "state_current_generation_check", "c", True, "(current_generation >= 0)"),
        *((table, name, "p" if expected[0] else "u", True, None)
          for (table, name), expected in _INDEXES.items()),
    }:
        raise SnapshotPublicationReceiverUnavailable()
    return sha256(json.dumps(pins, separators=(",", ":")).encode()).hexdigest()


async def verify_snapshot_publication_receiver(connection: asyncpg.Connection, profile: SnapshotPublicationProfile) -> None:
    await lock_snapshot_publication_relations(connection)
    approved = await connection.fetchval("""
        SELECT current_database() = $1 AND current_user = $2 AND session_user = $2
          AND inet_server_addr() = $3::inet AND inet_server_port() = $4
          AND current_setting('server_encoding')='UTF8'
          AND NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls)
          AND NOT EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member=r.oid)
          AND NOT has_database_privilege(r.oid,d.oid,'CREATE,TEMP')
          AND NOT pg_has_role(r.oid,d.datdba,'MEMBER') AND NOT pg_has_role(r.oid,n.nspowner,'MEMBER')
          AND NOT EXISTS (SELECT 1 FROM pg_namespace w WHERE left(w.nspname,3)<>'pg_'
                          AND w.nspname<>'information_schema' AND has_schema_privilege(r.oid,w.oid,'CREATE'))
          AND NOT EXISTS (SELECT 1 FROM pg_class other JOIN pg_namespace ons ON ons.oid=other.relnamespace
                          WHERE left(ons.nspname,3)<>'pg_' AND ons.nspname<>'information_schema'
                            AND other.relkind IN ('r','m','p')
                            AND NOT (ons.oid=n.oid AND other.relname::text=ANY($6::text[]))
                            AND has_table_privilege(r.oid,other.oid,'MAINTAIN'))
          AND (SELECT count(*) FROM pg_class c WHERE c.relnamespace=n.oid
               AND c.relname::text=ANY($6::text[]) AND c.relkind='r'
               AND NOT pg_has_role(r.oid,c.relowner,'MEMBER')
               AND has_table_privilege(r.oid,c.oid,'SELECT')
               AND NOT has_table_privilege(r.oid,c.oid,'UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
               AND NOT has_any_column_privilege(r.oid,c.oid,'REFERENCES')
               AND has_table_privilege(r.oid,c.oid,'MAINTAIN')
               AND NOT EXISTS (SELECT 1 FROM aclexplode(c.relacl) acl
                               WHERE acl.grantee NOT IN (r.oid,c.relowner)
                                  OR (acl.grantee=r.oid AND acl.is_grantable))
               AND NOT EXISTS (SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) acl
                               WHERE a.attrelid=c.oid AND a.attnum>0
                                 AND (acl.grantee NOT IN (r.oid,c.relowner)
                                      OR (acl.grantee=r.oid AND acl.is_grantable)))
               AND CASE WHEN c.relname='state' THEN
                        NOT has_any_column_privilege(r.oid,c.oid,'INSERT')
                    AND has_column_privilege(r.oid,c.oid,'current_generation','UPDATE')
                    AND NOT has_column_privilege(r.oid,c.oid,'marker','UPDATE')
                    AND NOT has_column_privilege(r.oid,c.oid,'singleton','UPDATE')
                   ELSE has_table_privilege(r.oid,c.oid,'INSERT')
                    AND NOT has_any_column_privilege(r.oid,c.oid,'UPDATE') END) = 3
        FROM pg_roles r JOIN pg_database d ON d.datname=current_database()
        JOIN pg_namespace n ON n.nspname=$5 WHERE r.rolname=current_user
    """, profile.database, profile.username, ip_address(profile.host), profile.port, _SCHEMA, _TABLES)
    if not approved or await compute_snapshot_publication_proof(connection) != profile.proof_sha256:
        raise SnapshotPublicationReceiverUnavailable()
    marker = await connection.fetchval(f"SELECT marker FROM ONLY {_SCHEMA}.state WHERE singleton")
    if marker != profile.marker:
        raise SnapshotPublicationReceiverUnavailable()
