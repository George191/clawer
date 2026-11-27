"""Validate and explicitly prepare PostgreSQL for Debezium current-state capture."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from urllib.parse import quote
from uuid import UUID

import httpx
from aiokafka import AIOKafkaConsumer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.base.kafka import build_admin_client_kwargs, build_consumer_kwargs
from app.config.settings import settings
from app.etl.cdc import CDC_LAYERS, UNAVAILABLE_VALUE, postgres_table_pattern
from app.etl.schema.catalogs import (
    RECORD_IDENTITY_COLUMNS,
    TABLE_DEFINITIONS,
    lineage_columns,
)
from app.etl.schema.layout import sql_identifier
from app.storage.business_database import (
    BusinessDatabaseNotConfigured,
    BusinessPostgresClient,
    get_layer_pg_client,
    layer_connection_info,
)
from app.storage.postgres_client import get_pg_client
from app.syncer.mongo_cdc import SetupError, register_connector

_REGISTERED_SQL = """
SELECT schema_name, table_name, layer, data_type
FROM app.helio_objects
WHERE is_active AND table_role='current' AND layer=ANY(CAST(:layers AS text[]))
  AND data_type ~ '^[a-z][a-z0-9_]{0,49}$'
  AND (ddl_sql <> '' OR data_type_source='registration')
"""

_IDENTITY_SQL = """SELECT connector_name AS connector, topic_prefix AS prefix,
    slot_name AS slot, publication_name AS publication
    FROM app.etl_postgres_cdc_bindings WHERE connection_id=:id"""

_PHYSICAL_SQL = """
SELECT n.nspname,c.relname,c.oid,c.relkind::text AS relkind,
       ARRAY(SELECT a.attname FROM pg_attribute a
             WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) AS columns
FROM jsonb_to_recordset(CAST(:tables AS jsonb)) AS o(schema_name text, table_name text)
JOIN pg_namespace n ON n.nspname::text COLLATE "C"=o.schema_name
JOIN pg_class c ON c.relnamespace=n.oid AND c.relname::text COLLATE "C"=o.table_name
WHERE c.relkind IN ('r','p') AND c.relpersistence='p' AND NOT c.relispartition
  AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid=c.oid)
"""

_PUBLICATION_SQL = """
SELECT p.pubviaroot,p.puballtables,p.pubinsert AND p.pubupdate AND p.pubdelete AS publishes_changes,ARRAY(
    SELECT n.nspname FROM pg_publication_namespace pn
    JOIN pg_namespace n ON n.oid=pn.pnnspid WHERE pn.pnpubid=p.oid
) AS schemas
FROM pg_publication p WHERE p.pubname::text COLLATE "C"=:name
"""

_PUBLISHED_SQL = """
SELECT n.nspname,c.relname,
       pt.rowfilter IS NULL AND r.prattrs IS NULL AND r.prqual IS NULL AS full_table
FROM pg_publication_tables pt
JOIN pg_publication p ON p.pubname=pt.pubname
JOIN pg_namespace n ON n.nspname=pt.schemaname
JOIN pg_class c ON c.relnamespace=n.oid AND c.relname=pt.tablename
LEFT JOIN pg_publication_rel r ON r.prpubid=p.oid AND r.prrelid=c.oid
WHERE pt.pubname::text COLLATE "C"=:name
"""

_ROW_IMAGES_SQL = """
SELECT n.nspname,c.relname,c.relreplident='f' AS full_identity,
       has_table_privilege(c.oid,'SELECT') AS can_select
FROM pg_class root
CROSS JOIN LATERAL (
    SELECT root.oid AS relid
    UNION ALL
    SELECT t.relid::oid FROM pg_partition_tree(root.oid::regclass) t WHERE t.level > 0
) tree
JOIN pg_class c ON c.oid=tree.relid
JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE root.oid=ANY(CAST(:parents AS oid[]))
"""


def cdc_table_enabled(layer: str, data_type: str) -> bool:
    return layer in CDC_LAYERS and TABLE_DEFINITIONS.get(layer, {}).get(data_type, {}).get("storage") != "timescale"


async def registered_cdc_objects(*, connection_id: str | None = None) -> dict[tuple[str, str], tuple[str, str]]:
    """Return eligible internal ETL bindings from system metadata."""
    bindings = await layer_connection_info()
    layers = [layer for layer, source in bindings.items() if source["engine"] == "postgresql"
              and (connection_id is None or source["id"] == connection_id)]
    rows = await get_pg_client().fetch_all(_REGISTERED_SQL, {"layers": layers})
    return {(row["schema_name"], row["table_name"]): (row["layer"], row["data_type"])
            for row in rows if cdc_table_enabled(row["layer"], row["data_type"])}


def _table_parameters(objects):
    return {"tables": json.dumps([{"schema_name": schema, "table_name": table} for schema, table in objects])}


def _table_key(row):
    return row["nspname"], row["relname"]


def _physical_objects(rows):
    return {_table_key(row): row for row in rows if set(RECORD_IDENTITY_COLUMNS).issubset(row["columns"])}


def _table_list(tables):
    return ", ".join(f"{sql_identifier(schema, schema=True)}.{_identifier(table)}" for schema, table in sorted(tables))


def _publication_tables(physical):
    # PostgreSQL implicitly publishes declarative partitions. ONLY excludes unrelated
    # inheritance descendants of a regular table.
    return ", ".join(("ONLY " if physical[key]["relkind"] == "r" else "") + _table_list([key])
                     for key in sorted(physical))


def _check_publication(publication):
    if not publication or not publication["pubviaroot"] or not publication["publishes_changes"] or publication["puballtables"]:
        raise SetupError("Prepare an internal CDC publication with full change capture via parent tables")


async def published_cdc_objects(pg: BusinessPostgresClient, identity: dict[str, str]) -> dict[tuple[str, str], tuple[str, str]]:
    """Resolve registered ETL bindings covered by the internal publication."""
    if pg.connection_id != identity["connection_id"]:
        raise SetupError("The ETL connection changed while resolving the CDC publication")
    objects = await registered_cdc_objects(connection_id=identity["connection_id"])
    params = {"name": identity["publication"]}
    async with pg.session() as session:
        publication = (await session.execute(text(_PUBLICATION_SQL), params)).mappings().first()
        _check_publication(publication)
        published = (await session.execute(text(_PUBLISHED_SQL), params)).mappings().all()
        if any(not row["full_table"] for row in published):
            raise SetupError("The internal CDC publication must include full tables")
        selected = {key: objects[key] for row in published if (key := _table_key(row)) in objects}
        rows = (await session.execute(text(_PHYSICAL_SQL), _table_parameters(selected))).mappings().all()
    return {key: selected[key] for key in _physical_objects(rows)}


async def ensure_table_publication(pg: BusinessPostgresClient, schema: str, table: str) -> None:
    """Publish a newly initialized, registered current parent before its first write."""
    target = (schema, table)
    identity = await postgres_cdc_identity(pg)
    name = identity["publication"]
    params = {"name": name}
    async with pg.session() as session:
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:name,0))"), params)
        registry = await registered_cdc_objects(connection_id=identity["connection_id"])
        rows = (await session.execute(text(_PHYSICAL_SQL), _table_parameters([target]))).mappings().all()
        physical = _physical_objects(rows)
        if target not in registry or target not in physical:
            raise SetupError("CDC table must be an active registered current ETL parent")
        publication = (await session.execute(text(_PUBLICATION_SQL), params)).mappings().first()
        _check_publication(publication)
        published = (await session.execute(text(_PUBLISHED_SQL), params)).mappings().all()
        existing = next((row for row in published if _table_key(row) == target), None)
        if existing is None:
            if publication["schemas"]:
                raise SetupError("New ETL tables must be enrolled with the existing schema publication and connector")
            await session.execute(text(f"ALTER PUBLICATION {_identifier(name)} ADD TABLE {_publication_tables(physical)}"))
        elif not existing["full_table"]:
            raise SetupError("The internal CDC publication must include full tables")


def _missing_lineage(objects, physical):
    for key, row in physical.items():
        for column in lineage_columns(objects[key][0]):
            if column not in row["columns"]:
                yield key, column


async def check_unrouted_raw_topics(identity: dict[str, str], objects) -> set[str]:
    """Find existing raw topics without a route and verify their committed boundaries."""
    if not settings.kafka_brokers:
        raise SetupError("Configure Kafka access to verify existing CDC topic boundaries")
    name = identity["prefix"]
    retained = {f"{name}.{schema}.{table}" for schema, table in objects}
    admin = AIOKafkaAdminClient(**build_admin_client_kwargs(request_timeout_ms=8000))
    # Use the physical end so a paused topic with an open transaction is not considered empty.
    reader = AIOKafkaConsumer(**build_consumer_kwargs(
        group_id=None, isolation_level="read_uncommitted", request_timeout_ms=8000,
    ))
    try:
        async with asyncio.timeout(15):
            await admin.start()
            # Dots in a schema are part of the database identifier.
            raw_topic = re.compile(rf"{re.escape(name)}\..+\.[^.]+")
            removed = [topic for topic in await admin.list_topics() if raw_topic.fullmatch(topic) and topic not in retained]
            if not removed:
                return set()
            descriptions = await admin.describe_topics(removed)
            if {item["topic"] for item in descriptions if item["partitions"] and not item.get("error_code")
                and all(not part.get("error_code") for part in item["partitions"])} != set(removed):
                raise SetupError("Cannot verify all unrouted CDC topic partitions")
            partitions = [TopicPartition(item["topic"], part["partition"])
                          for item in descriptions for part in item["partitions"]]
            await reader.start()
            starts, ends, committed = await asyncio.gather(
                reader.beginning_offsets(partitions), reader.end_offsets(partitions),
                admin.list_consumer_group_offsets(f"{name}-relay"),
            )
            paused = set(settings.kafka_paused_topics)
            pending = set()
            for part in partitions:
                offset = committed[part].offset if part in committed else -1
                if ends[part] == max(0, offset):
                    continue
                # A paused, empty partition proves that there are no retained
                # records, even if the group has no position at this boundary.
                if part.topic in paused and starts[part] == ends[part] and offset < ends[part]:
                    continue
                pending.add(part.topic)
            if pending:
                raise SetupError(f"Unverified CDC backlog for {len(pending)} unrouted raw topic(s): {', '.join(sorted(pending)[:10])}")
            return set(removed)
    except SetupError:
        raise
    except Exception:
        raise SetupError("Cannot verify unrouted CDC topic offsets") from None
    finally:
        await asyncio.gather(reader.stop(), admin.close(), return_exceptions=True)


async def postgres_cdc_identity(pg: BusinessPostgresClient) -> dict[str, str]:
    """Read the saved CDC identity and the currently selected physical database."""
    database = make_url(pg.connection_url).database
    if not database or not pg.connection_id:
        raise SetupError("Resolve the saved PostgreSQL connection before reading its CDC identity")
    row = await get_pg_client().fetch_one(_IDENTITY_SQL, {"id": UUID(pg.connection_id)})
    if row is None:
        raise SetupError("Prepare the saved connection's internal PostgreSQL CDC first")
    return {**row, "connection_id": pg.connection_id, "database": database}


async def _ensure_postgres_cdc_identity(pg: BusinessPostgresClient) -> None:
    connection_id = UUID(pg.connection_id)
    if await get_pg_client().fetch_one(_IDENTITY_SQL, {"id": connection_id}) is not None:
        return
    if not settings.kafka_topic:
        raise SetupError("SPIDER_KAFKA_TOPIC is required to prepare PostgreSQL CDC")
    prefix = f"{settings.kafka_topic}-postgres-cdc-{connection_id.hex}"
    digest = hashlib.sha256(connection_id.bytes).hexdigest()[:24]
    await get_pg_client().execute("""INSERT INTO app.etl_postgres_cdc_bindings
        (connection_id, connector_name, topic_prefix, slot_name, publication_name)
        VALUES (:id, :prefix, :prefix, :slot, :slot) ON CONFLICT (connection_id) DO NOTHING""",
        {"id": connection_id, "prefix": prefix, "slot": f"spider_cdc_{digest}"})


def connector_config(
    pg: BusinessPostgresClient, identity: dict[str, str], *, snapshot_mode: str = "no_data", snapshot_tables=(),
) -> dict[str, str]:
    """Build pgoutput capture with complete row values and precise logical types."""
    url = make_url(pg.connection_url)
    if not url.host or not url.username:
        raise SetupError("The business Data Connection must specify a host, user and database")
    if snapshot_mode not in {"no_data", "initial"}:
        raise SetupError("Unsupported Postgres CDC snapshot mode")
    config = {
        "connector.class": "io.debezium.connector.postgresql.PostgresConnector",
        "tasks.max": "1", "database.hostname": url.host,
        "database.port": str(url.port or 5432), "database.user": url.username,
        "database.password": url.password or "", "database.dbname": identity["database"],
        "database.sslmode": pg.sslmode,
        "topic.prefix": identity["prefix"], "plugin.name": "pgoutput",
        "slot.name": identity["slot"], "slot.drop.on.stop": "false",
        "publication.name": identity["publication"], "publication.autocreate.mode": "disabled",
        "publish.via.partition.root": "true", "snapshot.mode": snapshot_mode,
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
    if snapshot_mode == "initial":
        if not snapshot_tables:
            raise SetupError("Initial CDC snapshots require published ETL tables")
        config["snapshot.include.collection.list"] = ",".join(
            postgres_table_pattern(f"{schema}.{table}") for schema, table in sorted(snapshot_tables)
        )
    for parameter in ("sslrootcert", "sslcert", "sslkey"):
        if url.query.get(parameter):
            config[f"database.{parameter}"] = str(url.query[parameter])
    return config


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]{0,62}", value):
        raise SetupError("Unsafe PostgreSQL identifier")
    return f'"{value}"'


async def prepare_database(pg: BusinessPostgresClient) -> None:
    """Prepare the internal publication and full images for registered ETL parents."""
    identity = await postgres_cdc_identity(pg)
    name = identity["publication"]
    publication_name = _identifier(name)
    params = {"name": name}
    async with pg.session() as session:
        await session.execute(text("SET LOCAL lock_timeout = '5s'"))
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:name,0))"), params)
        objects = await registered_cdc_objects(connection_id=identity["connection_id"])
        rows = (await session.execute(text(_PHYSICAL_SQL), _table_parameters(objects))).mappings().all()
        physical = _physical_objects(rows)
        publication = (await session.execute(text(_PUBLICATION_SQL), params)).mappings().first()
        if publication and publication["puballtables"]:
            raise SetupError("The internal CDC publication cannot be a FOR ALL TABLES publication")
        if publication:
            if publication["schemas"]:
                raise SetupError("Changing schema publication coverage requires a coordinated CDC cutover")
            existing = (await session.execute(text(_PUBLISHED_SQL), params)).mappings().all()
            if {_table_key(row) for row in existing} - physical.keys():
                raise SetupError("Removing published tables requires a coordinated CDC cutover")
            # Relation OIDs survive renames, so check historical topics as well as membership.
            await check_unrouted_raw_topics(identity, physical)
        for key, column in _missing_lineage(objects, physical):
            await session.execute(text(
                f"ALTER TABLE {_table_list([key])} ADD COLUMN {_identifier(column)} TEXT"
            ))
        if publication:
            if physical:
                await session.execute(text(f"ALTER PUBLICATION {publication_name} SET TABLE {_publication_tables(physical)}"))
            await session.execute(text(
                f"ALTER PUBLICATION {publication_name} SET (publish_via_partition_root=true, publish='insert, update, delete, truncate')"
            ))
        else:
            scope = f" FOR TABLE {_publication_tables(physical)}" if physical else ""
            await session.execute(text(
                f"CREATE PUBLICATION {publication_name}{scope} WITH (publish_via_partition_root=true)"
            ))
        rows = (await session.execute(
            text(_ROW_IMAGES_SQL), {"parents": [row["oid"] for row in physical.values()]},
        )).mappings().all()
        for row in rows:
            if not row["full_identity"]:
                await session.execute(text(f"ALTER TABLE {_table_list([_table_key(row)])} REPLICA IDENTITY FULL"))


async def check_database(
    pg: BusinessPostgresClient, *, require_slot: bool = False, require_replication: bool = True,
) -> bool:
    """Read-only checks reject incomplete WAL/publication/row-image preparation."""
    identity = await postgres_cdc_identity(pg)
    server = await pg.fetch_one(
        "SELECT current_setting('server_version_num')::int AS version, "
        "current_setting('wal_level') AS wal_level, "
        "(SELECT rolreplication OR rolsuper FROM pg_roles WHERE rolname=current_user) AS replication"
    )
    if server["version"] < 150000:
        raise SetupError("PostgreSQL CDC requires PostgreSQL 15 or later")
    if server["wal_level"] != "logical" or (require_replication and not server["replication"]):
        raise SetupError("Postgres CDC requires wal_level=logical and a replication-enabled account")
    params = {"name": identity["publication"]}
    publication = await pg.fetch_one(_PUBLICATION_SQL, params)
    _check_publication(publication)
    objects = await registered_cdc_objects(connection_id=identity["connection_id"])
    physical = _physical_objects(await pg.fetch_all(_PHYSICAL_SQL, _table_parameters(objects)))
    published = await pg.fetch_all(_PUBLISHED_SQL, params)
    covered = {_table_key(row) for row in published}
    if (any(not row["full_table"] for row in published) or physical.keys() - covered
            or (not publication["schemas"] and covered != physical.keys())):
        raise SetupError("CDC publication tables differ from registered current ETL parents")
    rows = await pg.fetch_all(_ROW_IMAGES_SQL, {"parents": [row["oid"] for row in physical.values()]})
    if any(not row["full_identity"] or not row["can_select"] for row in rows):
        raise SetupError("ETL tables/partitions require REPLICA IDENTITY FULL and SELECT permission")
    if next(_missing_lineage(objects, physical), None) is not None:
        raise SetupError("ETL source lineage columns are missing. Run CDC preparation first")
    slot = await pg.fetch_one(
        "SELECT plugin,database,wal_status,confirmed_flush_lsn FROM pg_replication_slots WHERE slot_name=:name",
        {"name": identity["slot"]},
    )
    if slot and (slot["plugin"] != "pgoutput" or slot["database"] != identity["database"]
                 or slot["wal_status"] == "lost" or slot["confirmed_flush_lsn"] is None):
        raise SetupError("Postgres CDC slot is incompatible or has lost its recoverable WAL position")
    if require_slot and slot is None:
        raise SetupError("Postgres CDC slot is not registered. Prepare and start Debezium before ETL ingestion")
    return slot is not None


async def internal_cdc_clients(layer: str | None = None) -> list[BusinessPostgresClient]:
    """Return one resolved client per saved connection used by ETL."""
    bindings = {layer: source["id"] for layer, source in (await layer_connection_info()).items()
                if source["engine"] == "postgresql" and layer in CDC_LAYERS}
    if not bindings or (layer is not None and layer not in bindings):
        raise BusinessDatabaseNotConfigured("Assign a PostgreSQL connection to the ETL layer in Data Connections")
    clients = []
    seen = set()
    for selected_layer, connection_id in bindings.items():
        if (layer is not None and selected_layer != layer) or connection_id in seen:
            continue
        client = get_layer_pg_client(selected_layer)
        await client.resolve()
        if client.connection_id != connection_id:
            raise SetupError("ETL connection assignment changed while resolving CDC configuration")
        seen.add(connection_id)
        clients.append(client)
    return clients


async def _run(args: argparse.Namespace) -> list[tuple[str, dict[str, str], bool]]:
    clients = []
    try:
        clients = await internal_cdc_clients(args.layer)
        configs = []
        for pg in clients:
            await pg.connect(max_retries=1)
            if args.prepare:
                version = await pg.fetch_one("SELECT current_setting('server_version_num')::int AS version")
                if version["version"] < 150000:
                    raise SetupError("Postgres CDC preparation requires PostgreSQL 15 or later")
                await _ensure_postgres_cdc_identity(pg)
                await prepare_database(pg)
            slot_exists = await check_database(pg, require_replication=not args.prepare)
            identity = await postgres_cdc_identity(pg)
            mode = "no_data" if args.check or args.prepare else args.snapshot_mode
            if not (args.check or args.prepare):
                publication = await pg.fetch_one(_PUBLICATION_SQL, {"name": identity["publication"]})
                if publication["schemas"]:
                    raise SetupError("Keep the existing connector configuration for schema publications. Use --check.")
            snapshot_tables = await published_cdc_objects(pg, identity) if mode == "initial" else ()
            config = connector_config(pg, identity, snapshot_mode=mode, snapshot_tables=snapshot_tables)
            configs.append((identity["connector"], config, slot_exists))
        return configs
    finally:
        await asyncio.gather(*(pg.close() for pg in clients))
        await get_pg_client().close()


def _register_internal_connector(connect_url: str, name: str, config: dict[str, str], *, slot_exists: bool) -> None:
    # Check actual configuration before any PUT so a mismatch cannot move the stream to a new slot or topic.
    endpoint = f"{connect_url.rstrip('/')}/connectors"
    with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
        response = client.get(endpoint, params={"expand": "info"})
        if not response.is_success:
            raise SetupError("Cannot verify existing Kafka Connect identities before PostgreSQL CDC setup")
        for other, expanded in response.json().items():
            existing = expanded["info"]["config"]
            if other == name:
                stable = ("connector.class", "plugin.name", "topic.prefix", "slot.name", "publication.name")
                if any(existing.get(key) != config[key] for key in stable):
                    raise SetupError("The existing internal CDC connector identity differs from the saved binding")
                if not slot_exists:
                    offsets = client.get(f"{endpoint}/{quote(name, safe='')}/offsets")
                    if not offsets.is_success or offsets.json().get("offsets") != []:
                        raise SetupError("The saved replication slot is missing and the connector may have committed offsets")
            elif existing.get("topic.prefix") == config["topic.prefix"] or all(
                existing.get(key) == config[key] for key in ("database.hostname", "database.port", "slot.name")
            ):
                raise SetupError("Another connector already uses the saved internal CDC topic prefix or replication slot")
    register_connector(connect_url, config, name=name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect-url", default="http://127.0.0.1:8083")
    parser.add_argument("--layer", choices=CDC_LAYERS, help="Limit the operation to this layer's saved connection")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="Only perform read-only database checks")
    action.add_argument("--prepare", action="store_true", help="Explicitly prepare publication/replica identity")
    parser.add_argument("--snapshot-mode", choices=("no_data", "initial"), default="no_data")
    args = parser.parse_args()
    try:
        configs = asyncio.run(_run(args))
        if not args.check and not args.prepare:
            for name, config, slot_exists in configs:
                _register_internal_connector(args.connect_url, name, config, slot_exists=slot_exists)
            print(f"{len(configs)} PostgreSQL CDC connector(s) and tasks are RUNNING")
        else:
            print("Postgres CDC database checks passed")
        return 0
    except (BusinessDatabaseNotConfigured, SetupError) as exc:
        print(str(exc), file=sys.stderr)
    except Exception as exc:
        print(f"Postgres CDC setup failed ({type(exc).__name__}); inspect protected service logs", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
