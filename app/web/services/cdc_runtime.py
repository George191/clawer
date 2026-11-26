"""Read CDC infrastructure without changing connectors, slots or consumer offsets."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any, TypeVar
from uuid import UUID

import httpx
from aiokafka import AIOKafkaConsumer
from aiokafka.admin import AIOKafkaAdminClient
from aiokafka.structs import OffsetAndMetadata, TopicPartition

from app.base.kafka import build_admin_client_kwargs, build_consumer_kwargs
from app.config.settings import settings
from app.records.change_feed import MONGO_CDC_GROUP_PREFIX, mongo_cdc_topic_prefix
from app.storage.business_database import BusinessDatabaseNotConfigured
from app.syncer.postgres_cdc import (
    SetupError,
    check_database,
    connector_config,
    internal_cdc_clients,
    postgres_cdc_identity,
    published_cdc_objects,
)
from app.web.api.models.cdc import (
    CdcStatus,
    ConnectorState,
    ConnectState,
    ConsumerLag,
    KafkaState,
    PostgresState,
    PostgresTargetState,
    ProbeState,
    WalSlot,
)
from app.web.services.monitor_snapshots import MonitorSnapshots

_PROBE_TIMEOUT_SECONDS = 15
_STATUS = MonitorSnapshots(ttl=15, concurrent=1, capacity=1)
_CONNECTOR_STATES = {"RUNNING", "FAILED", "PAUSED", "UNASSIGNED", "RESTARTING", "STOPPED"}
_Probe = TypeVar("_Probe", bound=ProbeState)


def _state(value: Any) -> str:
    return value if value in _CONNECTOR_STATES else "UNKNOWN"


async def _internal_targets():
    if settings.etl_output_mode != "debezium":
        return []
    clients = await internal_cdc_clients()
    return [(pg, await postgres_cdc_identity(pg)) for pg in clients]


async def _connect() -> ConnectState:
    if not settings.cdc_connect_url:
        return ConnectState(state="unconfigured", reason="No Kafka Connect management URL is configured")
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS, trust_env=False) as client:
        response = await client.get(
            f"{settings.cdc_connect_url.rstrip('/')}/connectors", params=[("expand", "status"), ("expand", "info")],
        )
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, dict):
        raise ValueError("Expected expanded connector statuses")
    expected = set()
    internal = {}
    connection_ids = {}
    selection_error = None
    try:
        for pg, identity in await _internal_targets():
            internal[identity["connector"]] = connector_config(pg, identity)
            connection_ids[identity["connector"]] = identity["connection_id"]
    except (BusinessDatabaseNotConfigured, SetupError) as error:
        selection_error = str(error)
    expected.update(internal)
    if settings.db_url and settings.db_name:
        expected.add(f"{mongo_cdc_topic_prefix()}-{settings.db_name}")
    connectors, mismatched = [], []
    for name, item in body.items():
        saved_source = re.fullmatch(r"spider-source-([0-9a-f]{32})(?:-channel-[0-9]+)?", name)
        if name not in expected and not saved_source:
            continue
        if saved_source:
            expected.add(name)
            connection_ids[name] = str(UUID(saved_source.group(1)))
        if name in internal:
            config = item.get("info", {}).get("config", {})
            keys = ("connector.class", "database.hostname", "database.port", "database.dbname", "publication.name", "topic.prefix", "slot.name")
            if any(config.get(key) != internal[name][key] for key in keys):
                mismatched.append(name)
                connection_ids.pop(name, None)
        status = item["status"]
        connectors.append(ConnectorState(
            name=name, state=_state(status["connector"]["state"]),
            task_states=[_state(task["state"]) for task in status.get("tasks", [])],
            connection_id=connection_ids.get(name),
        ))
    if selection_error:
        return ConnectState(state="not_ready", connectors=connectors, reason=selection_error)
    if not connectors and not expected:
        return ConnectState(state="not_ready", reason="Kafka Connect is reachable, but no project connectors were found")
    missing = expected - {item.name for item in connectors}
    ready = all(item.state == "RUNNING" and item.task_states and
                all(state == "RUNNING" for state in item.task_states)
                for item in connectors if item.name in expected)
    return ConnectState(
        state="available" if ready and not missing and not mismatched else "not_ready", connectors=connectors,
        reason=f"Connectors not registered: {', '.join(sorted(missing))}" if missing else
        f"Internal connectors target a different connection or publication: {', '.join(sorted(mismatched))}" if mismatched else
        None if ready else "Some connectors or tasks are not running. Check the protected server logs.",
    )


async def _mongo() -> ProbeState:
    if not settings.db_url or not settings.db_name:
        return ProbeState(state="unconfigured", reason="No MongoDB source is configured")
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(
        settings.db_url, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000,
        socketTimeoutMS=3000,
    )
    try:
        hello = await client.admin.command("hello")
        if not hello.get("setName") and hello.get("msg") != "isdbgrid":
            return ProbeState(state="not_ready", reason="MongoDB requires a replica set or sharded topology")
        async with client[settings.db_name].watch(max_await_time_ms=100) as changes:
            await changes.try_next()
        return ProbeState(state="available", reason="Topology and database Change Streams read permissions verified")
    finally:
        client.close()


async def _postgres_target(pg, identity) -> tuple[PostgresTargetState, list[WalSlot]]:
    target = {"database": identity["database"], "connection_id": identity["connection_id"], "expected_slot": identity["slot"]}
    await pg.connect(max_retries=1)
    if pg.connection_id != identity["connection_id"]:
        raise SetupError("The ETL connection changed while checking its PostgreSQL CDC target")
    level = await pg.fetch_one(
        "SELECT current_setting('wal_level') AS wal_level, pg_is_in_recovery() AS in_recovery",
    )
    if not level or level["wal_level"] != "logical":
        return PostgresTargetState(
            state="not_ready", wal_level=level["wal_level"] if level else None,
            reason="PostgreSQL wal_level is not set to logical", **target,
        ), []
    if level["in_recovery"]:
        return PostgresTargetState(state="not_ready", wal_level="logical", **target,
                                   reason="This connection targets a standby. Verify logical replication support separately."), []
    rows = await pg.fetch_all("""
        SELECT slot_name AS name, active,
               pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint AS retained_bytes,
               pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint AS unconfirmed_bytes
        FROM pg_replication_slots
        WHERE database = current_database() AND slot_type = 'logical'
        ORDER BY slot_name
    """)
    slots = [WalSlot(**row, connection_id=identity["connection_id"]) for row in rows]
    try:
        await check_database(pg, require_slot=True, require_replication=False)
    except SetupError as error:
        return PostgresTargetState(state="not_ready", wal_level="logical", reason=str(error), **target), slots
    active = any(slot.name == identity["slot"] and slot.active for slot in slots)
    return PostgresTargetState(
        state="available" if active else "not_ready", wal_level="logical", **target,
        reason=None if active else "The internal replication slot has no active CDC reader",
    ), slots


async def _postgres() -> PostgresState:
    try:
        targets = await _internal_targets()
    except BusinessDatabaseNotConfigured as error:
        return PostgresState(state="unconfigured", reason=str(error))
    except SetupError as error:
        return PostgresState(state="not_ready", reason=str(error))
    if not targets:
        return PostgresState(state="unconfigured", reason="Internal PostgreSQL ETL capture is disabled")
    probes = await asyncio.gather(*(_postgres_target(pg, identity) for pg, identity in targets), return_exceptions=True)
    states, slots = [], []
    for (_, identity), result in zip(targets, probes, strict=True):
        if isinstance(result, BaseException):
            states.append(PostgresTargetState(state="unavailable", database=identity["database"],
                                              connection_id=identity["connection_id"], expected_slot=identity["slot"],
                                              reason="Unable to read PostgreSQL WAL and publication status"))
        else:
            state, target_slots = result
            states.append(state)
            slots.extend(target_slots)
    ready = all(state.state == "available" for state in states)
    return PostgresState(state="available" if ready else "not_ready", targets=states, slots=slots,
                         wal_level=states[0].wal_level if len(states) == 1 else None,
                         expected_slot=states[0].expected_slot if len(states) == 1 else None,
                         reason=next((state.reason for state in states if state.reason), None))


async def _consumer_offsets(admin: AIOKafkaAdminClient) -> dict[str, dict[TopicPartition, OffsetAndMetadata]]:
    try:
        targets = await _internal_targets()
    except (BusinessDatabaseNotConfigured, SetupError):
        # This configuration state is reported by the PostgreSQL probe.
        targets = []
    relay_topics = {}
    for pg, identity in targets:
        try:
            await pg.connect(max_retries=1)
            objects = await published_cdc_objects(pg, identity)
        except SetupError:
            # Publication readiness is reported by the PostgreSQL probe.
            continue
        relay_topics[f"{identity['prefix']}-relay"] = {
            f"{identity['prefix']}.{schema}.{table}" for schema, table in objects
        }
    mongo_prefix = f"{mongo_cdc_topic_prefix()}.{settings.db_name}."
    worker_topics = {
        settings.etl_rds_consumer_group: (settings.etl_raw_topic, settings.kafka_topic + "-"),
        settings.etl_ods_consumer_group: ("", settings.etl_rds_topic + "-"),
    }
    groups = [name for name, _ in await admin.list_consumer_groups()
              if name.startswith(MONGO_CDC_GROUP_PREFIX) or name in relay_topics or name in worker_topics]
    offsets = await asyncio.gather(*(admin.list_consumer_group_offsets(group) for group in groups))
    result = {}
    for group, positions in zip(groups, offsets, strict=True):
        if group in relay_topics:
            result[group] = {part: pos for part, pos in positions.items() if pos.offset >= 0 and part.topic in relay_topics[group]}
            continue
        exact, prefix = worker_topics.get(group, ("", mongo_prefix))
        result[group] = {part: pos for part, pos in positions.items()
                         if pos.offset >= 0 and (part.topic == exact or part.topic.startswith(prefix))}
    return result


async def _kafka() -> KafkaState:
    if not settings.kafka_brokers:
        return KafkaState(state="unconfigured", reason="No Kafka cluster is configured")
    admin = AIOKafkaAdminClient(**build_admin_client_kwargs(request_timeout_ms=10000))
    # This observer never subscribes, joins a group or commits offsets.
    reader = AIOKafkaConsumer(**build_consumer_kwargs(group_id=None, request_timeout_ms=10000))
    try:
        await admin.start()
        offsets = await _consumer_offsets(admin)
        existing = {TopicPartition(item["topic"], part["partition"])
                    for item in await admin.describe_topics()
                    for part in item["partitions"]}
        offsets = {group: {part: position for part, position in values.items() if part in existing}
                   for group, values in offsets.items()}
        partitions = {partition for positions in offsets.values() for partition in positions}
        if not partitions:
            return KafkaState(state="not_ready", reason="No committed offsets were found for the project CDC consumer groups")
        # Prime all topic metadata in one request. Pause before start so the
        # lag observer fetches positions without reading any business records.
        reader.assign(partitions)
        reader.pause(*partitions)
        await reader.start()
        ends = await reader.end_offsets(list(partitions))
        consumers = [ConsumerLag(
            group=group, partitions=len(positions),
            lag=sum(max(0, ends[partition] - position.offset) for partition, position in positions.items()),
        ) for group, positions in offsets.items() if positions]
        return KafkaState(state="available", consumers=consumers)
    finally:
        for close in (reader.stop, admin.close):
            with suppress(Exception):
                await asyncio.wait_for(close(), timeout=1)


async def _probe(operation: Callable[[], Awaitable[_Probe]], model: type[_Probe], reason: str) -> _Probe:
    try:
        return await asyncio.wait_for(operation(), timeout=_PROBE_TIMEOUT_SECONDS)
    except Exception:
        # Driver and Connect errors can contain credentials, full config or traces.
        return model(state="unavailable", reason=reason)


async def _status() -> CdcStatus:
    """Return bounded independent infrastructure probes, without implying ETL completion."""
    postgres, connect, mongo, kafka = await asyncio.gather(
        _probe(_postgres, PostgresState, "Unable to read PostgreSQL WAL status. Check connectivity and monitoring permissions."),
        _probe(_connect, ConnectState, "Unable to read Kafka Connect status. Check connectivity and permissions."),
        _probe(_mongo, ProbeState, "Unable to verify MongoDB topology and Change Streams read permissions"),
        _probe(_kafka, KafkaState, "Unable to read CDC offsets. Check Kafka connectivity and consumer group read permissions."),
    )
    return CdcStatus(
        checked_at=datetime.now(timezone.utc).isoformat(), output_mode=settings.etl_output_mode,
        connect=connect, mongo=mongo, postgres=postgres, kafka=kafka,
    )


async def status() -> CdcStatus:
    return await _STATUS.read("cdc", _status)
