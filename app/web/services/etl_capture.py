"""Bounded, read-only observation of a physical table's Debezium topic."""

import asyncio
from datetime import datetime, timezone

from aiokafka import AIOKafkaConsumer
from aiokafka.admin import AIOKafkaAdminClient
from fastapi import HTTPException
from sqlalchemy import text

from app.base.kafka import build_admin_client_kwargs, build_consumer_kwargs
from app.config.settings import settings
from app.etl.cdc import cdc_schemas
from app.etl.orbital_history import event_lineage
from app.syncer.postgres_cdc import cdc_table_enabled, postgres_cdc_identity
from app.web.services.etl_layers import get_etl_layers
from app.web.services.etl_runtime import topic_partitions
from app.web.services.monitor_snapshots import MonitorSnapshots

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


async def _capture(schema, table):
    if schema not in cdc_schemas() or not table.startswith(schema[3:] + "_") or not cdc_table_enabled(schema, table):
        return {"state": "outside_capture", "reason": "This table is outside the PostgreSQL CDC connector's capture rules."}
    if not settings.kafka_brokers:
        return {"state": "unconfigured", "reason": "The Web monitor has no Kafka broker configured."}
    try:
        result = await asyncio.wait_for(_latest(postgres_cdc_identity(), schema, table), timeout=13)
    except Exception:
        result = {"state": "unavailable", "reason": "The Web monitor could not inspect the CDC topic. Check broker connectivity, TLS and read access."}
    return {**result, "checkedAt": datetime.now(timezone.utc).isoformat()}


async def _orbital_reference():
    # The ODS hypertable stores the immutable RDS event's identity separately
    # from its normalized business ID. Use the time index and the RDS primary key.
    async with get_etl_layers().session() as session:
        await session.execute(text("SET LOCAL statement_timeout = '2s'"))
        result = (await session.execute(text("""
            WITH target AS MATERIALIZED (
                SELECT source_event_id, source_revision, source_observed_at, etl_version,
                       record_id, data_source, data_type
                FROM ts_ods.ods_orbital
                WHERE source_observed_at BETWEEN now() - interval '24 hours' AND now()
                ORDER BY source_observed_at DESC LIMIT 1
            )
            SELECT o.source_event_id, o.source_revision, o.source_observed_at, o.etl_version,
                   o.record_id, r.raw_data->'_meta' AS meta
            FROM target o JOIN ts_rds.rds_orbital r
              ON r.record_id = o.source_event_id AND r.data_source = o.data_source
                 AND r.data_type = o.data_type AND o.data_type = 'orbital'
        """))).mappings().first()
    if result and isinstance(result["meta"], dict):
        try:
            ref = event_lineage(result["meta"])
        except (ValueError, OverflowError):
            ref = None
        if ref and all(result[k] == v for k, v in ref.items()) and result["record_id"] == result["meta"]["record_id"]:
            return {
                "state": "matched", "source": "ts_rds.rds_orbital", "target": "ts_ods.ods_orbital",
                "eventTime": ref["source_observed_at"].isoformat(),
                "checkedAt": datetime.now(timezone.utc).isoformat(),
            }
    return {"state": "no_sample", "reason": "No matching immutable source reference was found in the latest 24-hour sample."}


async def capture(layer, table, *, include_hidden):
    layers = get_etl_layers()
    asset = await layers.runtime_table(layer, table, include_hidden=include_hidden)
    schema = asset["schemaName"]
    result = await _CAPTURE.read((schema, table), lambda: _capture(schema, table))
    orbital = {("ts_rds", "rds_orbital"), ("ts_ods", "ods_orbital")}
    if (schema, table) in orbital:
        counterpart = (orbital - {(schema, table)}).pop()
        try:
            await layers.runtime_table(*counterpart, include_hidden=include_hidden)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
        else:
            try:
                reference = await _ORBITAL.read("orbital", _orbital_reference)
            except HTTPException as exc:
                if exc.status_code != 503:
                    raise
                reference = {"state": "unavailable", "reason": "The bounded source-reference query was unavailable."}
            result = {**result, "recordLink": reference}
    return result
