"""Saved-source CDC lifecycle. Kafka Connect durably stores configuration and offsets.

One connector belongs to one saved source. Selection is immutable until removal;
each new capture gets a fresh topic namespace and replication slot identity.
No connector configuration, credentials or raw Connect errors leave this service.
"""

import asyncio
import json
import re
from urllib.parse import quote
from uuid import UUID, uuid4

import httpx
from aiokafka import AIOKafkaConsumer
from fastapi import HTTPException

from app.config.settings import settings
from app.etl.cdc import postgres_table_pattern
from app.web.api.models.data_connections import DataConnectionPayload
from app.web.api.models.source_cdc import SourceCdcCreate

CONNECTORS = {
    "postgresql": "io.debezium.connector.postgresql.PostgresConnector",
    "mongodb": "io.debezium.connector.mongodb.MongoDbConnector",
    "mysql": "io.debezium.connector.mysql.MySqlConnector",
    "oracle": "io.debezium.connector.oracle.OracleConnector",
    "milvus": "io.debezium.connector.milvus.MilvusConnector",
}
PREREQUISITES = {
    "postgresql": "Enable logical WAL and replication permissions; prepare a publication containing the selected tables. Use REPLICA IDENTITY FULL for complete before images.",
    "mongodb": "Requires a replica set or sharded cluster and snapshot / Change Streams permissions. Updates use MongoDB full-document lookup; before images are not enabled.",
    "mysql": "Enable ROW binlog with FULL row images, grant snapshot / replication permissions, and choose a server ID unique across replication clients.",
    "oracle": "Enable ARCHIVELOG and supplemental logging; grant snapshot / LogMiner permissions. Install the Oracle connector and JDBC driver on every Connect worker.",
    "milvus": "Incubating Debezium Milvus connector (3.7+). Requires compatible self-hosted Milvus with Kafka MQ and direct etcd access. Supply every single-partition physical channel used by the selected collections. Pulsar, RocksMQ, Woodpecker and Milvus Lite are not supported by this configuration. Upserts emit delete/insert events, not an update image.",
}


def connector_name(identifier) -> str:
    return f"spider-source-{UUID(str(identifier)).hex}"


async def request(method, path, *, missing=False, **kwargs):
    if not settings.cdc_connect_url:
        raise HTTPException(503, "Kafka Connect management URL is not configured")
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
            response = await client.request(method, settings.cdc_connect_url.rstrip("/") + path, **kwargs)
        if missing and response.status_code == 404:
            return None
        if not response.is_success:
            raise HTTPException(502, "Kafka Connect rejected the operation; inspect protected Connect logs")
        return response.json() if response.content else {}
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "Kafka Connect is unavailable or returned an invalid response") from None


async def require_unconfigured(identifier, engine, *, registry=None, row=None, session=None):
    # Called under the source row lock by both mutations and CDC task creation.
    if engine == "dameng":
        from app.web.services.source_dameng_cdc import require_no_dameng_binding
        await require_no_dameng_binding(session, UUID(str(identifier)))
        return
    if engine == "s3":
        from app.web.services.source_object_events import require_no_object_events
        row = row or await registry._stored(str(identifier), session=session, lock=" FOR UPDATE")
        await require_no_object_events(registry, row)
        return
    if engine not in CONNECTORS or not settings.cdc_connect_url:
        return
    existing = await request("GET", f"/connectors/{connector_name(identifier)}/config", missing=True)
    if existing is not None:
        raise HTTPException(409, "Remove this source's CDC task before editing, pausing or deleting the connection")


def _history(config, prefix):
    config.update({
        "schema.history.internal.kafka.bootstrap.servers": settings.kafka_brokers,
        "schema.history.internal.kafka.topic": f"{prefix}.schema-history",
    })
    for role in ("producer", "consumer"):
        key = f"schema.history.internal.{role}."
        config[key + "security.protocol"] = settings.kafka_security_protocol
        if settings.kafka_ssl_cafile:
            config[key + "ssl.truststore.type"] = "PEM"
            config[key + "ssl.truststore.location"] = settings.kafka_ssl_cafile
        if settings.kafka_sasl_mechanism:
            module = {"PLAIN": "plain.PlainLoginModule", "SCRAM-SHA-256": "scram.ScramLoginModule",
                      "SCRAM-SHA-512": "scram.ScramLoginModule"}.get(settings.kafka_sasl_mechanism)
            if not module:
                raise HTTPException(422, "Unsupported Kafka schema history authentication mechanism")
            def escape(value):
                return value.replace("\\", "\\\\").replace('"', '\\"')
            config[key + "sasl.mechanism"] = settings.kafka_sasl_mechanism
            config[key + "sasl.jaas.config"] = (
                f'org.apache.kafka.common.security.{module} required '
                f'username="{escape(settings.kafka_sasl_username)}" password="{escape(settings.kafka_sasl_password)}";'
            )


def build_config(identifier, source, password, payload: SourceCdcCreate, generation: str):
    engine, cfg = source.engine, source.config
    if engine not in CONNECTORS:
        raise HTTPException(422, "This source does not support continuous CDC")
    objects = [name.rsplit(".", 1) for name in payload.tables]
    if engine != "postgresql" and any("." in namespace for namespace, _ in objects):
        raise HTTPException(422, "Only PostgreSQL CDC supports a namespace containing literal dots")
    if engine in {"mysql", "mongodb", "milvus"} and any(namespace != cfg.database for namespace, _ in objects):
        raise HTTPException(422, "Every selected table or collection must belong to the saved database")
    if ((engine != "postgresql" and payload.publication is not None)
            or (engine != "mysql" and payload.serverId is not None)):
        raise HTTPException(422, "CDC options do not match the source engine")
    if engine != "oracle" and (payload.oracleDatabase or payload.oraclePdb):
        raise HTTPException(422, "Oracle options require an Oracle source")
    if (engine == "milvus") != (payload.milvus is not None):
        raise HTTPException(422, "Milvus capture requires Kafka MQ, etcd and physical channel configuration")
    prefix = f"spider-cdc-{UUID(str(identifier)).hex}-{generation}"
    config = {
        "connector.class": CONNECTORS[engine], "tasks.max": "1", "topic.prefix": prefix,
        "snapshot.mode": payload.snapshot,
        "key.converter": "org.apache.kafka.connect.json.JsonConverter",
        "value.converter": "org.apache.kafka.connect.json.JsonConverter",
        "key.converter.schemas.enable": "true", "value.converter.schemas.enable": "true",
        "tombstones.on.delete": "true", "heartbeat.interval.ms": "10000",
        "spider.source.revision": source.cdc_revision,
        "spider.capture.selection": payload.model_dump_json(exclude={"expectedUpdatedAt"}),
    }
    selected = ",".join(re.escape(name) for name in payload.tables)
    if engine == "milvus":
        host = f"[{cfg.host}]" if ":" in cfg.host else cfg.host
        config.update({
            "snapshot.mode": "initial" if payload.snapshot == "initial" else "never",
            "milvus.uri": f"{'https' if cfg.tls else 'http'}://{host}:{cfg.port}",
            "milvus.database": cfg.database,
            "milvus.token": password if cfg.auth == "token" else f"{cfg.username}:{password}",
            "milvus.kafka.bootstrap.servers": payload.milvus.kafkaBrokers,
            "milvus.kafka.consumer.group.id": prefix,
            "milvus.kafka.partition.index": "0",
            "milvus.etcd.endpoints": ",".join(payload.milvus.etcdEndpoints),
            "milvus.etcd.root.path": payload.milvus.etcdRoot,
            "milvus.pchannel.name": payload.milvus.pchannels[0],
            "milvus.collection.include.list": ",".join(re.escape(collection) for _, collection in objects),
        })
        return config
    if engine == "mongodb":
        host = f"[{cfg.host}]" if ":" in cfg.host else cfg.host
        config.update({
            "mongodb.connection.string": f"mongodb://{quote(cfg.username, safe='')}:{quote(password, safe='')}@{host}:{cfg.port}/?authSource={quote(cfg.authDatabase or 'admin', safe='')}&tls={str(cfg.tls).lower()}",
            "database.include.list": re.escape(cfg.database), "collection.include.list": selected,
            "capture.mode": "change_streams_update_full",
        })
        return config
    config.update({"database.hostname": cfg.host, "database.port": str(cfg.port),
                   "database.user": cfg.username, "database.password": password,
                   "table.include.list": selected, "decimal.handling.mode": "precise"})
    if engine == "postgresql":
        if not payload.publication:
            raise HTTPException(422, "Provide the preconfigured PostgreSQL publication")
        config.update({"database.dbname": cfg.database, "plugin.name": "pgoutput",
                       "table.include.list": ",".join(map(postgres_table_pattern, payload.tables)),
                       "database.sslmode": "verify-full" if cfg.tls else "disable",
                       "publication.name": payload.publication, "publication.autocreate.mode": "disabled",
                       "slot.name": f"spider_{generation}", "slot.drop.on.stop": "false"})
    elif engine == "mysql":
        if not payload.serverId:
            raise HTTPException(422, "Provide a unique MySQL replication server ID")
        config.update({"database.include.list": re.escape(cfg.database), "database.server.id": str(payload.serverId),
                       "database.ssl.mode": "verify_identity" if cfg.tls else "disabled"})
        _history(config, prefix)
    else:
        if not payload.oracleDatabase:
            raise HTTPException(422, "Provide the Oracle database (CDB / non-CDB) name")
        # Use a descriptor to retain the saved service endpoint and verified TCPS.
        # Only descriptor-safe endpoint names are accepted here.
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", cfg.host) or not re.fullmatch(r"[A-Za-z0-9_.-]+", cfg.database):
            raise HTTPException(422, "Oracle CDC requires a DNS / IPv4 host and a simple service name")
        config.update({"database.dbname": payload.oracleDatabase, "database.connection.adapter": "logminer",
                       "database.url": f"jdbc:oracle:thin:@(DESCRIPTION=(ADDRESS=(PROTOCOL={'TCPS' if cfg.tls else 'TCP'})(HOST={cfg.host})(PORT={cfg.port}))(CONNECT_DATA=(SERVICE_NAME={cfg.database}))(SECURITY=(SSL_SERVER_DN_MATCH=YES)))"})
        if payload.oraclePdb:
            config["database.pdb.name"] = payload.oraclePdb
        _history(config, prefix)
    return config


def channel_configs(name, config):
    if config.get("connector.class") != CONNECTORS["milvus"]:
        return [(name, config)]
    selection = json.loads(config["spider.capture.selection"])
    channels = (selection.get("milvus") or {}).get("pchannels", [])
    if not channels:
        return [(name, config)]
    return [(name if index == 0 else f"{name}-channel-{index}",
             {**config, "milvus.pchannel.name": channel,
              "milvus.kafka.consumer.group.id": f"{config['topic.prefix']}-{index}"})
            for index, channel in enumerate(channels)]


async def check_milvus_channels(selection):
    # This form deliberately supports plaintext, unauthenticated source MQ only.
    # Reject missing/multi-partition channels instead of silently reading partition 0.
    consumer = AIOKafkaConsumer(bootstrap_servers=selection.kafkaBrokers, group_id=None,
                               enable_auto_commit=False, request_timeout_ms=3000)
    try:
        async with asyncio.timeout(5):
            await consumer.start()
            await consumer.topics()
            if any(consumer.partitions_for_topic(name) != {0} for name in selection.pchannels):
                raise HTTPException(422, "Every Milvus physical channel must exist and have exactly one partition (0)")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Cannot inspect Milvus source MQ; this configuration requires unauthenticated PLAINTEXT Kafka access") from None
    finally:
        await consumer.stop()


async def check_postgres_selection(registry, row, selection):
    """Verify the requested change streams on the saved PostgreSQL database."""
    requested = [tuple(name.rsplit(".", 1)) for name in selection.tables]
    schemas, tables = zip(*requested, strict=True)
    async with registry._postgres_catalog_row(row) as connection:
        published = await connection.fetch("""
            SELECT p.schemaname AS schema_name, p.tablename AS table_name
            FROM pg_catalog.pg_publication_tables p
            JOIN unnest($2::text[], $3::text[]) AS selected(schema_name, table_name)
              ON p.schemaname::text COLLATE "C" = selected.schema_name
             AND p.tablename::text COLLATE "C" = selected.table_name
            WHERE p.pubname::text COLLATE "C" = $1
        """, selection.publication, schemas, tables)
    if {(table["schema_name"], table["table_name"]) for table in published} != set(requested):
        raise HTTPException(422, "The PostgreSQL publication must capture every selected table on this saved database")


class SourceCdc:
    def __init__(self, registry):
        self.registry = registry

    async def _source(self, identifier, session, expected=None):
        row = await self.registry._stored(str(identifier), session=session, lock=" FOR UPDATE")
        if row["engine"] not in CONNECTORS:
            raise HTTPException(422, "This source does not support continuous CDC")
        if expected is not None and row["updated_at"] != expected:
            raise HTTPException(409, "The source changed; reload before configuring CDC")
        return row

    async def describe(self, identifier):
        async with self.registry._session() as session:
            row = await self._source(identifier, session)
            plugin = CONNECTORS[row["engine"]]
            plugins = await request("GET", "/connector-plugins")
            available = any(item.get("class") == plugin for item in plugins)
            name = connector_name(identifier)
            config = await request("GET", f"/connectors/{name}/config", missing=True)
            result = {"connector": name, "pluginAvailable": available, "prerequisite": PREREQUISITES[row["engine"]],
                      "state": "unconfigured", "tasks": [], "selection": None, "topicPrefix": None}
            if config is not None:
                status = await request("GET", f"/connectors/{name}/status")
                result.update(state=status["connector"]["state"],
                              tasks=[{"id": task["id"], "state": task["state"]} for task in status.get("tasks", [])],
                              selection=json.loads(config["spider.capture.selection"]), topicPrefix=config["topic.prefix"],
                              sourceChanged=config.get("spider.source.revision") != row["updated_at"].isoformat(),
                              slot=config.get("slot.name"))
                for child, _ in channel_configs(name, config)[1:]:
                    child_status = await request("GET", f"/connectors/{child}/status", missing=True)
                    child_state = child_status["connector"]["state"] if child_status else "MISSING"
                    if child_state != "RUNNING":
                        result["state"] = "INCOMPLETE" if child_state == "MISSING" else child_state
                    result["tasks"].extend({"id": task["id"], "state": task["state"], "connector": child}
                                           for task in (child_status or {}).get("tasks", []))
            return result

    async def create(self, identifier, payload):
        async with self.registry._session() as session:
            row = await self._source(identifier, session, payload.expectedUpdatedAt)
            await require_unconfigured(identifier, row["engine"])
            stored_config = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
            source = DataConnectionPayload(name=row["name"], engine=row["engine"], config=stored_config)
            credentials = await self.registry._credentials(source, row)
            # Keep the revision outside the validated source payload.
            from types import SimpleNamespace
            source = SimpleNamespace(engine=source.engine, config=source.config, cdc_revision=row["updated_at"].isoformat())
            secret = credentials["token"] if source.config.auth == "token" else credentials["password"]
            config = build_config(identifier, source, secret, payload, uuid4().hex)
            if source.engine == "postgresql":
                await check_postgres_selection(self.registry, row, payload)
            plugin = config["connector.class"]
            captures = channel_configs(connector_name(identifier), config)
            for _, channel in captures:
                validation = await request("PUT", f"/connector-plugins/{plugin}/config/validate", json=channel)
                if validation.get("error_count") != 0:
                    raise HTTPException(422, "CDC configuration validation failed; check source prerequisites and protected Connect logs")
            if payload.milvus is not None:
                await check_milvus_channels(payload.milvus)
            for name, channel in captures:
                await request("POST", "/connectors", json={"name": name, "config": channel})
        return {"accepted": True}

    async def control(self, identifier, payload):
        async with self.registry._session() as session:
            row = await self._source(identifier, session, payload.expectedUpdatedAt)
            path = f"/connectors/{connector_name(identifier)}"
            config = await request("GET", path + "/config", missing=True)
            if config is None:
                raise HTTPException(404, "No CDC task is configured for this source")
            if payload.action in {"resume", "restart"} and config.get("spider.source.revision") != row["updated_at"].isoformat():
                raise HTTPException(409, "The source changed; remove this CDC task and configure it again")
            captures = channel_configs(connector_name(identifier), config)
            # Keep the root configuration until every child is removed, so partial
            # failures can always be discovered and retried from the same source.
            for name, channel in reversed(captures) if payload.action == "remove" else captures:
                current = f"/connectors/{name}"
                if payload.action == "remove":
                    await request("DELETE", current, missing=True)
                elif payload.action == "restart":
                    if await request("GET", current + "/config", missing=True) is None:
                        await request("POST", "/connectors", json={"name": name, "config": channel})
                    else:
                        await request("POST", current + "/restart", params={"includeTasks": "true", "onlyFailed": "true"})
                else:
                    await request("PUT", current + "/" + payload.action)
        return {"accepted": True}
