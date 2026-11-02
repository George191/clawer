"""Read CDC infrastructure without changing connectors, slots or consumer offsets."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, TypeVar

import httpx
from aiokafka import AIOKafkaConsumer
from aiokafka.admin import AIOKafkaAdminClient
from aiokafka.structs import OffsetAndMetadata, TopicPartition

from app.base.kafka import build_admin_client_kwargs, build_consumer_kwargs
from app.config.settings import settings
from app.records.change_feed import MONGO_CDC_GROUP_PREFIX, mongo_cdc_topic_prefix
from app.storage.postgres_client import get_pg_client
from app.web.api.models.cdc import (
    CdcStatus, ConnectState, ConnectorState, ConsumerLag, KafkaState,
    PostgresState, ProbeState, WalSlot,
)

_PROBE_TIMEOUT_SECONDS = 5
_CONNECTOR_STATES = {"RUNNING", "FAILED", "PAUSED", "UNASSIGNED", "RESTARTING", "STOPPED"}
_Probe = TypeVar("_Probe", bound=ProbeState)


def _state(value: Any) -> str:
    return value if value in _CONNECTOR_STATES else "UNKNOWN"


def _postgres_identity() -> dict[str, str]:
    from app.syncer.postgres_cdc import postgres_cdc_identity

    return postgres_cdc_identity()


async def _connect() -> ConnectState:
    if not settings.cdc_connect_url:
        return ConnectState(state="unconfigured", reason="未配置 Kafka Connect 管理地址")
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS, trust_env=False) as client:
        response = await client.get(
            f"{settings.cdc_connect_url.rstrip('/')}/connectors", params={"expand": "status"},
        )
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, dict):
        raise ValueError("Expected expanded connector statuses")
    connectors = []
    for name, item in body.items():
        if not name.startswith(f"{settings.kafka_topic}-"):
            continue
        status = item["status"]
        connectors.append(ConnectorState(
            name=name, state=_state(status["connector"]["state"]),
            task_states=[_state(task["state"]) for task in status.get("tasks", [])],
        ))
    if not connectors:
        return ConnectState(state="not_ready", reason="Connect 可达，但未发现本项目连接器")
    expected = set()
    if settings.db_url and settings.db_name:
        expected.add(f"{mongo_cdc_topic_prefix()}-{settings.db_name}")
    if settings.etl_output_mode == "debezium":
        expected.add(_postgres_identity()["connector"])
    missing = expected - {item.name for item in connectors}
    ready = all(item.state == "RUNNING" and item.task_states and
                all(state == "RUNNING" for state in item.task_states)
                for item in connectors if item.name in expected)
    return ConnectState(
        state="available" if ready and not missing else "not_ready", connectors=connectors,
        reason=f"尚未注册连接器：{'、'.join(sorted(missing))}" if missing else
        None if ready else "连接器或任务尚未全部运行；请检查受保护的服务端日志",
    )


async def _mongo() -> ProbeState:
    if not settings.db_url or not settings.db_name:
        return ProbeState(state="unconfigured", reason="未配置 MongoDB 数据源")
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(
        settings.db_url, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000,
        socketTimeoutMS=3000,
    )
    try:
        hello = await client.admin.command("hello")
        if not hello.get("setName") and hello.get("msg") != "isdbgrid":
            return ProbeState(state="not_ready", reason="MongoDB 尚未启用副本集或分片拓扑")
        async with client[settings.db_name].watch(max_await_time_ms=100) as changes:
            await changes.try_next()
        return ProbeState(state="available", reason="已验证拓扑和数据库 Change Streams 读取权限")
    finally:
        client.close()


async def _postgres() -> PostgresState:
    if not settings.pg_url:
        return PostgresState(state="unconfigured", reason="未配置 PostgreSQL 数据源")
    pg = get_pg_client()
    level = await pg.fetch_one(
        "SELECT current_setting('wal_level') AS wal_level, pg_is_in_recovery() AS in_recovery",
    )
    if not level or level["wal_level"] != "logical":
        return PostgresState(
            state="not_ready", wal_level=level["wal_level"] if level else None,
            reason="PostgreSQL wal_level 尚未设为 logical",
        )
    if level["in_recovery"]:
        return PostgresState(state="not_ready", wal_level="logical", reason="当前连接为备库，需单独验证逻辑复制支持")
    rows = await pg.fetch_all("""
        SELECT slot_name AS name, active,
               pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint AS retained_bytes,
               pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)::bigint AS unconfirmed_bytes
        FROM pg_replication_slots
        WHERE database = current_database() AND slot_type = 'logical'
        ORDER BY slot_name
    """)
    slots = [WalSlot(**row) for row in rows]
    expected = _postgres_identity()["slot"]
    active = any(slot.name == expected and slot.active for slot in slots)
    return PostgresState(
        state="available" if active else "not_ready", wal_level="logical", slots=slots,
        expected_slot=expected,
        reason=None if active else "本项目逻辑复制槽尚未创建或没有活跃的 CDC 读取连接",
    )


async def _consumer_offsets(admin: AIOKafkaAdminClient) -> dict[str, dict[TopicPartition, OffsetAndMetadata]]:
    pg_prefix = _postgres_identity()["prefix"]
    mongo_prefix = f"{mongo_cdc_topic_prefix()}.{settings.db_name}."
    groups = [name for name, _ in await admin.list_consumer_groups()
              if name.startswith(MONGO_CDC_GROUP_PREFIX) or name == f"{pg_prefix}-relay"]
    offsets = await asyncio.gather(*(admin.list_consumer_group_offsets(group) for group in groups))
    return {
        group: {partition: position for partition, position in positions.items()
                if position.offset >= 0 and (partition.topic.startswith(mongo_prefix)
                                            or partition.topic.startswith(f"{pg_prefix}."))}
        for group, positions in zip(groups, offsets, strict=True)
    }


async def _kafka() -> KafkaState:
    if not settings.kafka_brokers:
        return KafkaState(state="unconfigured", reason="未配置 Kafka")
    admin = AIOKafkaAdminClient(**build_admin_client_kwargs(request_timeout_ms=3000))
    # This observer never subscribes, joins a group or commits offsets.
    reader = AIOKafkaConsumer(**build_consumer_kwargs(group_id=None, request_timeout_ms=3000))
    try:
        await admin.start()
        offsets = await _consumer_offsets(admin)
        partitions = {partition for positions in offsets.values() for partition in positions}
        if not partitions:
            return KafkaState(state="not_ready", reason="尚未发现本项目 CDC 消费组的已提交位点")
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


async def status() -> CdcStatus:
    """Return bounded independent infrastructure probes, without implying ETL completion."""
    connect, mongo, postgres, kafka = await asyncio.gather(
        _probe(_connect, ConnectState, "无法读取 Kafka Connect 状态；请检查连接和访问权限"),
        _probe(_mongo, ProbeState, "无法验证 MongoDB 拓扑和 Change Streams 读取权限"),
        _probe(_postgres, PostgresState, "无法读取 PostgreSQL WAL 状态；请检查连接和监控权限"),
        _probe(_kafka, KafkaState, "无法读取 CDC 消费位点；请检查 Kafka 连接和组读取权限"),
    )
    return CdcStatus(
        checked_at=datetime.now(timezone.utc).isoformat(), output_mode=settings.etl_output_mode,
        connect=connect, mongo=mongo, postgres=postgres, kafka=kafka,
    )
