"""Bounded, read-only observation of a physical table's Debezium topic."""

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import quote

from aiokafka import AIOKafkaConsumer
from aiokafka.admin import AIOKafkaAdminClient
from fastapi import HTTPException
from sqlalchemy import text

from app.base.kafka import build_admin_client_kwargs, build_consumer_kwargs
from app.config.settings import settings
from app.etl.orbital_history import event_lineage
from app.etl.schema.registry import get_registered_object, object_table_ref
from app.storage.business_database import BusinessDatabaseNotConfigured, get_layer_pg_client
from app.storage.postgres_client import get_pg_client
from app.syncer.postgres_cdc import (
    cdc_table_enabled,
    connector_config,
    postgres_cdc_identity,
    published_cdc_objects,
)
from app.web.services.etl_layers import get_etl_layers
from app.web.services.etl_runtime import topic_partitions
from app.web.services.monitor_snapshots import MonitorSnapshots
from app.web.services.source_cdc import request as connect_request

_CAPTURE = MonitorSnapshots(ttl=30, concurrent=4, capacity=64)
_ORBITAL = MonitorSnapshots(ttl=30, concurrent=1, capacity=1)


def _observation(message, identity, schema, table):
    envelope = message.value
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    if not isinstance(payload, dict):
        return None
    source = payload.get("source")
    if not isinstance(source, dict) or (
        source.get("connector"), source.get("name"), source.get("db"), source.get("schema"), source.get("table")
    ) != ("postgresql", identity["prefix"], identity["database"], schema, table):
        return None
    op = payload.get("op")
    if op not in ("c", "u", "d", "r") or type(source.get("lsn")) is not int or source["lsn"] < 0:
        return None
    if not isinstance(payload.get("before" if op == "d" else "after"), dict):
        return None
    return {
        "topic": message.topic, "partition": message.partition, "offset": message.offset,
        "operation": {"c": "insert", "u": "update", "d": "delete", "r": "snapshot"}[op],
        "sourceLsn": f"{source['lsn'] >> 32:X}/{source['lsn'] & 0xffffffff:X}",
        "eventTimeMs": source.get("ts_ms"),
    }


async def _latest(identity, schema, table):
    topic = f"{identity['prefix']}.{schema}.{table}"
    reader = AIOKafkaConsumer(**build_consumer_kwargs(group_id=None, request_timeout_ms=8000))
    admin = AIOKafkaAdminClient(**build_admin_client_kwargs(request_timeout_ms=8000))
    try:
        await admin.start()
        partitions = await topic_partitions(admin, topic)
        if partitions is None:
            return {"state": "no_topic", "reason": "The broker has no visible CDC topic for this table."}
        await reader.start()
        parts = sorted(partitions, key=lambda p: p.partition)[:4]
        if parts:
            starts, ends = await asyncio.gather(reader.beginning_offsets(parts), reader.end_offsets(parts))
            parts = [p for p in parts if ends[p] > starts[p]]
        if not parts:
            return {"state": "no_sample", "topic": topic, "reason": "The inspected Kafka partitions have no retained records."}
        reader.assign(parts)
        for p in parts:
            reader.seek(p, max(starts[p], ends[p] - 2))
        batch = await reader.getmany(timeout_ms=2000, max_records=8)
        for partition, messages in batch.items():
            for message in reversed(messages):
                if message.offset < ends[partition] and (event := _observation(message, identity, schema, table)):
                    return {"state": "observed", **event}
        return {"state": "no_sample", "topic": topic, "reason": "The bounded sample contained no matching table change."}
    finally:
        await asyncio.gather(reader.stop(), admin.close())


async def _selected_capture(pg, schema, table, layer, data_type, *, connection_id=None):
    await pg.connect(max_retries=1)
    if connection_id is not None and pg.connection_id != connection_id:
        return {"state": "outside_capture", "reason": "The table's saved connection is no longer assigned to this ETL layer."}
    identity = await postgres_cdc_identity(pg)
    selected = await published_cdc_objects(pg, identity)
    if selected.get((schema, table)) != (layer, data_type):
        return {"state": "outside_capture", "reason": "This table is outside the internal ETL publication."}
    if settings.cdc_connect_url:
        config = await connect_request("GET", f"/connectors/{quote(identity['connector'], safe='')}/config", missing=True)
        expected = connector_config(pg, identity)
        keys = ("connector.class", "database.hostname", "database.port", "database.dbname", "publication.name", "topic.prefix")
        if config is None or any(config.get(key) != expected[key] for key in keys):
            return {"state": "unconfigured", "reason": "The internal ETL connector is missing or targets a different publication."}
        for kind, name in (("schema", schema), ("table", f"{schema}.{table}")):
            included = config.get(f"{kind}.include.list")
            excluded = config.get(f"{kind}.exclude.list")
            if ((included and not any(re.fullmatch(pattern, name, re.IGNORECASE) for pattern in included.split(",")))
                    or (excluded and any(re.fullmatch(pattern, name, re.IGNORECASE) for pattern in excluded.split(",")))):
                return {"state": "outside_capture", "reason": "The internal ETL connector excludes this table."}
    return await _latest(identity, schema, table)


async def _capture(pg, schema, table, layer, data_type, *, connection_id):
    if settings.etl_output_mode != "debezium" or not data_type or not cdc_table_enabled(layer, data_type):
        return {"state": "outside_capture", "reason": "This table is outside internal PostgreSQL ETL capture."}
    if not settings.kafka_brokers:
        return {"state": "unconfigured", "reason": "The Web monitor has no Kafka broker configured."}
    try:
        result = await asyncio.wait_for(_selected_capture(pg, schema, table, layer, data_type,
                                                          connection_id=connection_id), timeout=13)
    except Exception:
        result = {"state": "unavailable", "reason": "The Web monitor could not inspect the CDC topic. Check broker connectivity, TLS and read access."}
    return {**result, "checkedAt": datetime.now(timezone.utc).isoformat()}


async def _orbital_reference(source, target):
    # The ODS hypertable stores the immutable RDS event's identity separately
    # from its normalized business ID. Use the time index and the RDS primary key.
    source_ref, target_ref = object_table_ref(source), object_table_ref(target)
    source_pg, target_pg = get_layer_pg_client("rds"), get_layer_pg_client("ods")
    await asyncio.gather(source_pg.connect(max_retries=1), target_pg.connect(max_retries=1))
    if source_pg.connection_id != source["connection_id"] or target_pg.connection_id != target["connection_id"]:
        raise BusinessDatabaseNotConfigured("Orbital connection assignment changed while checking the source reference")
    async with target_pg.session() as session:
        await session.execute(text("SET LOCAL statement_timeout = '2s'"))
        result = (await session.execute(text(f"""
            SELECT source_event_id, source_revision, source_observed_at, etl_version,
                   record_id, data_source, data_type
            FROM {target_ref}
            WHERE data_type = 'orbital' AND source_observed_at BETWEEN now() - interval '24 hours' AND now()
            ORDER BY source_observed_at DESC LIMIT 1
        """))).mappings().first()
    if result:
        async with source_pg.session() as session:
            await session.execute(text("SET LOCAL statement_timeout = '2s'"))
            meta = await session.scalar(text(f"""
                SELECT raw_data->'_meta' FROM {source_ref}
                WHERE record_id=:source_event_id AND data_source=:data_source AND data_type=:data_type
            """), dict(result))
        try:
            ref = event_lineage(meta) if isinstance(meta, dict) else None
        except (ValueError, OverflowError):
            ref = None
        if ref and all(result[k] == v for k, v in ref.items()) and result["record_id"] == meta.get("record_id"):
            return {
                "state": "matched", "source": f"{source['schema_name']}.{source['table_name']}",
                "target": f"{target['schema_name']}.{target['table_name']}",
                "eventTime": ref["source_observed_at"].isoformat(),
                "checkedAt": datetime.now(timezone.utc).isoformat(),
            }
    return {"state": "no_sample", "reason": "No matching immutable source reference was found in the latest 24-hour sample."}


async def capture(layer, table, *, include_hidden, connection_id=None):
    layers = get_etl_layers()
    asset = await layers.runtime_table(layer, table, include_hidden=include_hidden, connection_id=connection_id)
    schema, resolved_layer, data_type = asset["schemaName"], asset.get("layer"), asset.get("dataType")
    pg = get_layer_pg_client(resolved_layer) if resolved_layer else None
    asset_connection = asset.get("connectionId")
    if asset.get("tableRole") == "current":
        key = (asset_connection, schema, table, resolved_layer, data_type)
        result = await _CAPTURE.read(key, lambda: _capture(pg, schema, table, resolved_layer, data_type,
                                                          connection_id=asset_connection))
    else:
        result = {"state": "outside_capture", "reason": "This table is outside internal PostgreSQL ETL capture."}
    if data_type == "orbital" and resolved_layer in {"rds", "ods"} and asset.get("tableRole") == "current":
        peer_layer = "ods" if resolved_layer == "rds" else "rds"
        peer = await get_registered_object(get_pg_client(), peer_layer, "orbital", "current")
        if peer is None:
            return result
        try:
            peer_pg = get_layer_pg_client(peer_layer)
            await peer_pg.resolve()
            await layers.runtime_table(peer["schema_name"], peer["table_name"], include_hidden=include_hidden,
                                       connection_id=peer_pg.connection_id)
        except BusinessDatabaseNotConfigured:
            return {**result, "recordLink": {"state": "unavailable", "reason": "The orbital counterpart connection is unavailable."}}
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
        else:
            current = {"schema_name": schema, "table_name": table, "connection_id": asset_connection}
            peer = {**peer, "connection_id": peer_pg.connection_id}
            source, target = (current, peer) if resolved_layer == "rds" else (peer, current)
            key = (asset_connection, peer_pg.connection_id, schema, table,
                   peer["schema_name"], peer["table_name"])
            try:
                reference = await _ORBITAL.read(key, lambda: asyncio.wait_for(_orbital_reference(source, target), timeout=8))
            except Exception:
                reference = {"state": "unavailable", "reason": "The bounded source-reference query was unavailable."}
            result = {**result, "recordLink": reference}
    return result
