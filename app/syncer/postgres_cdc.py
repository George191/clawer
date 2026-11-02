"""Validate and explicitly prepare PostgreSQL for Debezium current-state capture."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config.settings import settings
from app.etl.cdc import UNAVAILABLE_VALUE, cdc_schemas
from app.etl.schema.catalogs import lineage_columns
from app.storage.postgres_client import PostgresClient, _resolved_pg_url
from app.syncer.mongo_cdc import SetupError, register_connector

_LINEAGE_TABLES_SQL = """
SELECT n.nspname,c.relname,
       ARRAY(SELECT a.attname FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) AS columns
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname=ANY(:schemas) AND c.relkind IN ('r','p') AND NOT c.relispartition
ORDER BY n.nspname,c.relname
"""


def _lineage_schemas() -> list[str]:
    return [schema for schema in cdc_schemas() if lineage_columns(schema[3:])]


def _missing_lineage(rows):
    for row in rows:
        for column in lineage_columns(row["nspname"][3:]):
            if column not in row["columns"]:
                yield row["nspname"], row["relname"], column


def postgres_cdc_identity() -> dict[str, str]:
    """Return stable, non-secret connector/slot names shared by monitoring and relay."""
    database = make_url(_resolved_pg_url()).database
    if not database or not settings.kafka_topic:
        raise SetupError("Postgres database and SPIDER_KAFKA_TOPIC are required")
    prefix = f"{settings.kafka_topic}-postgres-cdc"
    digest = hashlib.sha256(f"{prefix}:{database}".encode()).hexdigest()[:16]
    return {
        "database": database, "prefix": prefix,
        "connector": f"{prefix}-{database}",
        "slot": f"spider_cdc_{digest}", "publication": f"spider_cdc_{digest}",
    }


def connector_config(*, snapshot_mode: str = "no_data") -> dict[str, str]:
    """Build pgoutput capture with complete row values and precise logical types."""
    identity = postgres_cdc_identity()
    url = make_url(settings.pg_cdc_url or _resolved_pg_url())
    target = make_url(_resolved_pg_url())
    if (url.host, url.port or 5432, url.database) != (target.host, target.port or 5432, target.database):
        raise SetupError("SPIDER_PG_CDC_URL must point to the same host, port and database as SPIDER_PG_URL")
    if not url.host or not url.username:
        raise SetupError("SPIDER_PG_URL must specify a host, user and database")
    if snapshot_mode not in {"no_data", "initial"}:
        raise SetupError("Unsupported Postgres CDC snapshot mode")
    config = {
        "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
        "tasks.max": "1", "database.hostname": url.host,
        "database.port": str(url.port or 5432), "database.user": url.username,
        "database.password": url.password or "", "database.dbname": identity["database"],
        "database.sslmode": str(url.query.get("sslmode", "prefer")),
        "topic.prefix": identity["prefix"], "plugin.name": "pgoutput",
        "slot.name": identity["slot"], "slot.drop.on.stop": "false",
        "publication.name": identity["publication"], "publication.autocreate.mode": "disabled",
        "publish.via.partition.root": "true", "snapshot.mode": snapshot_mode,
        "schema.include.list": ",".join(cdc_schemas()),
        "table.include.list": ",".join(rf"ts_{layer}\.{layer}_[a-z][a-z0-9_]*"
                                       for layer in (schema[3:] for schema in cdc_schemas())),
        "decimal.handling.mode": "string", "time.precision.mode": "adaptive_time_microseconds",
        "hstore.handling.mode": "json", "binary.handling.mode": "base64",
        "tombstones.on.delete": "false", "unavailable.value.placeholder": UNAVAILABLE_VALUE,
        "heartbeat.interval.ms": "10000",
        "heartbeat.action.query": "SELECT pg_logical_emit_message(false, 'spider_cdc', 'heartbeat')",
        "key.converter": "org.apache.kafka.connect.json.JsonConverter",
        "value.converter": "org.apache.kafka.connect.json.JsonConverter",
        "key.converter.schemas.enable": "true", "value.converter.schemas.enable": "true",
        "topic.creation.default.replication.factor": "-1",
        "topic.creation.default.partitions": "1",
        "topic.creation.default.max.message.bytes": "16777216",
    }
    for parameter in ("sslrootcert", "sslcert", "sslkey"):
        if url.query.get(parameter):
            config[f"database.{parameter}"] = str(url.query[parameter])
    return config


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise SetupError("Unsafe PostgreSQL identifier")
    return f'"{value}"'


async def prepare_database(pg: PostgresClient) -> None:
    """Explicit migration: publish future ETL tables/partitions and full row images."""
    publication = _identifier(postgres_cdc_identity()["publication"])
    schemas = ", ".join(_identifier(name) for name in cdc_schemas())
    async with pg.session() as session:
        await session.execute(text("SET LOCAL lock_timeout = '5s'"))
        for schema in cdc_schemas():
            await session.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_identifier(schema)}"))
        lineage = (await session.execute(text(_LINEAGE_TABLES_SQL), {"schemas": _lineage_schemas()})).mappings().all()
        for schema, table, column in _missing_lineage(lineage):
            await session.execute(text(
                f"ALTER TABLE {_identifier(schema)}.{_identifier(table)} ADD COLUMN {_identifier(column)} TEXT"
            ))
        exists = (await session.execute(text(
            "SELECT 1 FROM pg_publication WHERE pubname=:name"
        ), {"name": postgres_cdc_identity()["publication"]})).first()
        if exists:
            await session.execute(text(f"ALTER PUBLICATION {publication} SET TABLES IN SCHEMA {schemas}"))
            await session.execute(text(f"ALTER PUBLICATION {publication} SET (publish_via_partition_root=true)"))
        else:
            await session.execute(text(
                f"CREATE PUBLICATION {publication} FOR TABLES IN SCHEMA {schemas} "
                "WITH (publish_via_partition_root=true)"
            ))
        # Parent and existing leaf partitions need FULL; future tables receive it
        # in ETLBase before any write/partition creation can become visible.
        rows = (await session.execute(text(
            "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=ANY(:schemas) AND c.relkind IN ('r','p') "
            "AND c.relreplident <> 'f' ORDER BY n.nspname,c.relname"
        ), {"schemas": list(cdc_schemas())})).mappings().all()
        for row in rows:
            name = f"{_identifier(row['nspname'])}.{_identifier(row['relname'])}"
            await session.execute(text(f"ALTER TABLE {name} REPLICA IDENTITY FULL"))


async def check_database(
    pg: PostgresClient, *, require_slot: bool = False, require_replication: bool = True,
) -> None:
    """Read-only checks reject incomplete WAL/publication/row-image preparation."""
    server = await pg.fetch_one(
        "SELECT current_setting('server_version_num')::int AS version, "
        "current_setting('wal_level') AS wal_level, "
        "(SELECT rolreplication OR rolsuper FROM pg_roles WHERE rolname=current_user) AS replication"
    )
    if server["version"] < 150000:
        raise SetupError("Postgres CDC schema publication requires PostgreSQL 15 or later")
    if server["wal_level"] != "logical" or (require_replication and not server["replication"]):
        raise SetupError("Postgres CDC requires wal_level=logical and a replication-enabled account")
    publication = await pg.fetch_one(
        "SELECT pubviaroot FROM pg_publication WHERE pubname=:name",
        {"name": postgres_cdc_identity()["publication"]},
    )
    if not publication or not publication["pubviaroot"]:
        raise SetupError("Prepare the CDC publication with publish_via_partition_root=true first")
    missing = await pg.fetch_all(
        "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=ANY(:schemas) AND c.relkind IN ('r','p') AND (c.relreplident<>'f' "
        "OR NOT has_table_privilege(c.oid,'SELECT'))",
        {"schemas": list(cdc_schemas())},
    )
    if missing:
        raise SetupError("ETL tables/partitions require REPLICA IDENTITY FULL and SELECT permission")
    covered = await pg.fetch_all(
        "SELECT n.nspname FROM pg_publication_namespace p "
        "JOIN pg_publication pub ON pub.oid=p.pnpubid "
        "JOIN pg_namespace n ON n.oid=p.pnnspid WHERE pub.pubname=:name",
        {"name": postgres_cdc_identity()["publication"]},
    )
    if set(cdc_schemas()) - {row["nspname"] for row in covered}:
        raise SetupError("CDC publication must include all current ETL schemas and future tables")
    lineage = await pg.fetch_all(_LINEAGE_TABLES_SQL, {"schemas": _lineage_schemas()})
    if next(_missing_lineage(lineage), None) is not None:
        raise SetupError("TASK/DWD source lineage columns are missing; run the owner preparation migration")
    slot = await pg.fetch_one(
        "SELECT plugin,database,wal_status,confirmed_flush_lsn FROM pg_replication_slots WHERE slot_name=:name",
        {"name": postgres_cdc_identity()["slot"]},
    )
    if slot and (slot["plugin"] != "pgoutput" or slot["database"] != postgres_cdc_identity()["database"]
                 or slot["wal_status"] == "lost" or slot["confirmed_flush_lsn"] is None):
        raise SetupError("Postgres CDC slot is incompatible or has lost its recoverable WAL position")
    if require_slot and slot is None:
        raise SetupError("Postgres CDC slot is not registered; prepare and start Debezium before ETL ingestion")


async def _run(args: argparse.Namespace) -> None:
    # DDL uses the application/owner connection; a replication-only account is
    # never required to own or alter published tables.
    pg = PostgresClient(url=None if args.prepare else settings.pg_cdc_url or None)
    try:
        await pg.connect(max_retries=1)
        if args.prepare:
            # Validate version before executing schema-publication syntax.
            version = await pg.fetch_one("SELECT current_setting('server_version_num')::int AS version")
            if version["version"] < 150000:
                raise SetupError("Postgres CDC preparation requires PostgreSQL 15 or later")
            await prepare_database(pg)
        await check_database(pg, require_replication=not args.prepare)
    finally:
        await pg.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect-url", default="http://127.0.0.1:8083")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="Only perform read-only database checks")
    action.add_argument("--prepare", action="store_true", help="Explicitly prepare publication/replica identity")
    parser.add_argument("--snapshot-mode", choices=("no_data", "initial"), default="no_data")
    args = parser.parse_args()
    try:
        config = connector_config(snapshot_mode=args.snapshot_mode)
        asyncio.run(_run(args))
        if not args.check and not args.prepare:
            register_connector(args.connect_url, config, name=postgres_cdc_identity()["connector"])
            print("Postgres CDC connector and task are RUNNING")
        else:
            print("Postgres CDC database checks passed")
        return 0
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
    except Exception as exc:
        print(f"Postgres CDC setup failed ({type(exc).__name__}); inspect protected service logs", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
