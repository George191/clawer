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
from app.etl.schema.catalogs import TABLE_DEFINITIONS, lineage_columns
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


def cdc_table_enabled(schema: str, table: str) -> bool:
    layer = schema.removeprefix("ts_")
    logical = table.removeprefix(layer + "_")
    return TABLE_DEFINITIONS.get(layer, {}).get(logical, {}).get("storage") != "timescale"


def _schema_publications() -> list[str]:
    return [schema for schema in cdc_schemas() if schema != "ts_ods"]


def _connector_tables() -> str:
    patterns = []
    for schema in cdc_schemas():
        layer = schema[3:]
        excluded = [re.escape(layer + "_" + table) for table, definition in TABLE_DEFINITIONS.get(layer, {}).items()
                    if definition.get("storage") == "timescale"]
        guard = rf"(?!(?:{'|'.join(excluded)})$)" if excluded else ""
        patterns.append(rf"{schema}\.{guard}{layer}_[a-z][a-z0-9_]*")
    return ",".join(patterns)


_ODS_CURRENT_SQL = """
SELECT DISTINCT n.nspname,c.relname
FROM public.ontology_objects r
JOIN pg_namespace n ON n.nspname=r.schema_name
JOIN pg_class c ON c.relnamespace=n.oid AND c.relname=r.table_name
WHERE r.layer='ods' AND r.table_role='current' AND r.is_active
AND n.nspname='ts_ods' AND c.relkind IN ('r','p') AND NOT c.relispartition
ORDER BY n.nspname,c.relname
"""


def _ods_whitelist(rows):
    return {(row["nspname"], row["relname"]) for row in rows
            if cdc_table_enabled(row["nspname"], row["relname"])}


def _publication_objects(tables):
    objects = ["TABLES IN SCHEMA " + ", ".join(_identifier(schema) for schema in _schema_publications())]
    if tables:
        objects.append("TABLE " + ", ".join(f"{_identifier(schema)}.{_identifier(table)}" for schema, table in sorted(tables)))
    return ", ".join(objects)


async def ensure_table_publication(pg: PostgresClient, table_name: str) -> None:
    """Publish a newly registered ODS current parent before its first write."""
    schema, table = table_name.split(".")
    if schema != "ts_ods" or not cdc_table_enabled(schema, table):
        return
    _identifier(schema); _identifier(table)
    publication = postgres_cdc_identity()["publication"]
    async with pg.session() as session:
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:name,0))"), {"name": publication})
        registered = _ods_whitelist((await session.execute(text(_ODS_CURRENT_SQL))).mappings().all())
        if (schema, table) not in registered:
            raise SetupError("ODS CDC table must be an active registered current parent")
        covered = (await session.execute(text(
            "SELECT 1 FROM pg_publication_rel r JOIN pg_publication p ON p.oid=r.prpubid "
            "WHERE p.pubname=:publication AND r.prrelid=to_regclass(:table_name)"
        ), {"publication": publication, "table_name": table_name})).first()
        if not covered:
            await session.execute(text(f"ALTER PUBLICATION {_identifier(publication)} ADD TABLE {_identifier(schema)}.{_identifier(table)}"))


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
        "table.include.list": _connector_tables(),
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
    async with pg.session() as session:
        await session.execute(text("SET LOCAL lock_timeout = '5s'"))
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:name,0))"),
                              {"name": postgres_cdc_identity()["publication"]})
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
        ods_tables = _ods_whitelist((await session.execute(text(_ODS_CURRENT_SQL))).mappings().all())
        objects = _publication_objects(ods_tables)
        if exists:
            await session.execute(text(f"ALTER PUBLICATION {publication} SET {objects}"))
            await session.execute(text(f"ALTER PUBLICATION {publication} SET (publish_via_partition_root=true)"))
        else:
            await session.execute(text(
                f"CREATE PUBLICATION {publication} FOR {objects} "
                "WITH (publish_via_partition_root=true)"
            ))
        # Parent and existing leaf partitions need FULL; future tables receive it
        # in ETLBase before any write/partition creation can become visible.
        rows = (await session.execute(text(
            "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_class root ON root.oid=COALESCE(pg_partition_root(c.oid),c.oid) "
            "WHERE n.nspname=ANY(:schemas) AND c.relkind IN ('r','p') "
            "AND (n.nspname<>'ts_ods' OR root.relname=ANY(:ods_tables)) "
            "AND c.relreplident <> 'f' ORDER BY n.nspname,c.relname"
        ), {"schemas": list(cdc_schemas()), "ods_tables": [table for _, table in ods_tables]})).mappings().all()
        for row in rows:
            if not cdc_table_enabled(row['nspname'], row['relname']):
                continue
            name = f"{_identifier(row['nspname'])}.{_identifier(row['relname'])}"
            await session.execute(text(f"ALTER TABLE {name} REPLICA IDENTITY FULL"))
        if settings.pg_cdc_url:
            reader = _identifier(make_url(settings.pg_cdc_url).username or "")
            await session.execute(text(f"GRANT USAGE ON SCHEMA public TO {reader}"))
            await session.execute(text(
                "GRANT SELECT (layer,table_role,is_active,schema_name,table_name) "
                f"ON public.ontology_objects TO {reader}"
            ))


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
        "SELECT pubviaroot,puballtables FROM pg_publication WHERE pubname=:name",
        {"name": postgres_cdc_identity()["publication"]},
    )
    if not publication or not publication["pubviaroot"] or publication.get("puballtables"):
        raise SetupError("Prepare the CDC publication with publish_via_partition_root=true first")
    expected = _ods_whitelist(await pg.fetch_all(_ODS_CURRENT_SQL))
    missing = await pg.fetch_all(
        "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_class root ON root.oid=COALESCE(pg_partition_root(c.oid),c.oid) "
        "WHERE n.nspname=ANY(:schemas) AND c.relkind IN ('r','p') AND (c.relreplident<>'f' "
        "OR NOT has_table_privilege(c.oid,'SELECT')) "
        "AND (n.nspname<>'ts_ods' OR root.relname=ANY(:ods_tables))",
        {"schemas": list(cdc_schemas()), "ods_tables": [table for _, table in expected]},
    )
    missing = [row for row in missing if cdc_table_enabled(row['nspname'], row['relname'])]
    if missing:
        raise SetupError("ETL tables/partitions require REPLICA IDENTITY FULL and SELECT permission")
    covered = await pg.fetch_all(
        "SELECT n.nspname FROM pg_publication_namespace p "
        "JOIN pg_publication pub ON pub.oid=p.pnpubid "
        "JOIN pg_namespace n ON n.oid=p.pnnspid WHERE pub.pubname=:name",
        {"name": postgres_cdc_identity()["publication"]},
    )
    if {row["nspname"] for row in covered} != set(_schema_publications()):
        raise SetupError("CDC publication requires schema coverage outside ODS and explicit ODS current parents")
    actual = await pg.fetch_all(
        "SELECT n.nspname,c.relname FROM pg_publication_rel r "
        "JOIN pg_publication p ON p.oid=r.prpubid JOIN pg_class c ON c.oid=r.prrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE p.pubname=:name AND n.nspname='ts_ods'",
        {"name": postgres_cdc_identity()["publication"]},
    )
    if {(row['nspname'], row['relname']) for row in actual} != expected:
        raise SetupError("CDC publication ODS parents differ from registered non-Timescale current tables")
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
