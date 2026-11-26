"""Live connector positions and persisted handler drafts for the ETL workspace."""

from __future__ import annotations

import ast
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiokafka import AIOKafkaConsumer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient
from anyio import fail_after
from fastapi import HTTPException

from app.base.kafka import build_admin_client_kwargs, build_consumer_kwargs
from app.config.settings import settings
from app.etl.state.offsets import OVERRIDE_KEY_PREFIX, get_offset_manager
from app.etl.schema.catalogs import validate_table_name
from app.storage.postgres_client import get_pg_client
from app.storage.redis_client import RedisClient
from app.web.services.monitor_snapshots import MonitorSnapshots

_ETL_PATH = Path(__file__).resolve().parents[2] / "etl"
_REDIS = RedisClient(settings.etl_redis_url)
_STREAM = MonitorSnapshots(ttl=4, concurrent=6, capacity=128)
_DRAFT_DDL = """
CREATE TABLE IF NOT EXISTS app.flow_handler_drafts (
    schema_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    code TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (schema_name, table_name)
)
"""


async def _table(layer: str, table: str) -> tuple[str, str, dict[str, Any]]:
    from app.web.services.etl_layers import get_etl_layers

    row = await get_etl_layers().runtime_table(layer, table, include_hidden=False)
    return row["layer"], row["dataType"], row


def _source(layer: str) -> tuple[Path, str, set[str]]:
    source = _ETL_PATH / f"ts_{layer}.py"
    if layer not in {"rds", "ods", "task", "dwd", "dws", "dim"} or not source.is_file():
        return source, "", set()
    code = source.read_text(encoding="utf-8")
    handlers = {
        method.name.removeprefix("_handler_")
        for node in ast.parse(code).body if isinstance(node, ast.ClassDef)
        for method in node.body
        if isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef) and method.name.startswith("_handler_")
    }
    return source, code, handlers


def _binding(layer: str, table: str) -> tuple[str, list[str]]:
    if layer in {"rds", "ods"}:
        try:
            validate_table_name(table)
        except ValueError:
            return "", []
    elif table not in _source(layer)[2]:
        return "", []
    topics: list[str] = []
    group = getattr(settings, f"etl_{layer}_consumer_group", "")
    if layer == "rds":
        if settings.kafka_topic:
            topics.append(f"{settings.kafka_topic}-{table}")
        topics.append(settings.etl_raw_topic)
    elif layer == "ods":
        topics = [f"{settings.etl_rds_topic}-{table}"]
    elif layer in {"task", "dwd", "dim"}:
        topics = [settings.etl_ods_topic]
        if layer == "dwd":
            topics.append(settings.etl_task_topic)
    elif layer == "dws":
        topics = [settings.etl_dwd_topic]
    return group, list(dict.fromkeys(topic for topic in topics if topic))


async def topic_partitions(admin: AIOKafkaAdminClient, topic: str) -> list[TopicPartition] | None:
    # A metadata request for all topics cannot auto-create a missing topic.
    metadata = next((item for item in await admin.describe_topics() if item["topic"] == topic), None)
    if metadata is None:
        return None
    if metadata.get("error_code") or any(part.get("error_code") for part in metadata["partitions"]):
        raise HTTPException(503, "The broker could not provide partition metadata")
    return [TopicPartition(topic, p["partition"]) for p in metadata["partitions"]]


async def _kafka_positions(topic: str, group: str) -> list[dict[str, Any]]:
    # Neither client subscribes or joins a consumer group, so inspecting the
    # workspace cannot rebalance running workers or advance their positions.
    admin = AIOKafkaAdminClient(**build_admin_client_kwargs(request_timeout_ms=5000))
    consumer = AIOKafkaConsumer(**build_consumer_kwargs(group_id=None, request_timeout_ms=5000))
    try:
        await admin.start()
        partitions = await topic_partitions(admin, topic)
        if partitions is None:
            raise HTTPException(409, "No Kafka topic is visible for this worker input")
        if not partitions:
            raise HTTPException(503, "Kafka topic has no available partitions")
        committed = await admin.list_consumer_group_offsets(group, partitions=partitions)
        await consumer.start()
        beginning, end = await asyncio.gather(
            consumer.beginning_offsets(partitions), consumer.end_offsets(partitions),
        )
        return [
            {
                "partition": partition.partition,
                "beginningOffset": beginning[partition],
                "endOffset": end[partition],
                "committedOffset": (
                    committed[partition].offset
                    if partition in committed and committed[partition].offset >= 0 else None
                ),
            }
            for partition in sorted(partitions, key=lambda item: item.partition)
        ]
    finally:
        with suppress(Exception):
            await consumer.stop()
        with suppress(Exception):
            await admin.close()


async def stream(layer: str, table: str, topic: str | None = None, *, include_hidden: bool = False, fresh: bool = False) -> dict[str, Any]:
    from app.web.services.etl_layers import get_etl_layers

    row = await get_etl_layers().runtime_table(layer, table, include_hidden=include_hidden)
    group, topics = _binding(row["layer"], row["dataType"]) if row["tableRole"] == "current" else ("", [])
    selected = topic or (topics[0] if topics else None)
    result: dict[str, Any] = {
        "available": False, "consumerGroup": group, "topics": topics,
        "topic": selected, "offsets": [], "throughput": None, "resumeAvailable": False,
        "throughputReason": "Topic growth is measured from successive Kafka log-end offsets",
    }
    if topic and topic not in topics:
        raise HTTPException(400, "Topic does not belong to this connector")
    if not group or not selected:
        return {**result, "reason": "This table has no input topic defined by an ETL worker"}
    if not settings.kafka_brokers:
        return {**result, "reason": "No Kafka broker is configured for the Web monitor"}
    if fresh:
        observation = await _stream_positions(selected, group)
    else:
        observation = await _STREAM.read((group, selected), lambda: _stream_positions(selected, group))
    return {**result, **observation}


async def _stream_positions(selected, group):
    result = {"resumeAvailable": False}
    try:
        positions = await asyncio.wait_for(_kafka_positions(selected, group), timeout=15)
    except HTTPException as exc:
        return {**result, "reason": str(exc.detail)}
    except Exception:
        return {**result, "reason": "Unable to read Kafka partitions. Check broker connectivity, TLS and consumer group access."}
    try:
        redis = await _REDIS.ensure_connected(raise_errors=True)
        if redis is None:
            raise ConnectionError("Redis is unavailable")
        keys = [f"offset:{group}:{selected}:{item['partition']}" for item in positions]
        pending_keys = [f"{OVERRIDE_KEY_PREFIX}:{group}:{selected}:{item['partition']}" for item in positions]
        values = await asyncio.wait_for(redis.mget([*keys, *pending_keys]), timeout=3)
        saved, pending = values[:len(keys)], values[len(keys):]
        result["resumeAvailable"] = True
    except Exception:
        _REDIS.mark_unavailable()
        saved = pending = [None] * len(positions)
        result["reason"] = "Redis checkpoints and pending overrides could not be read."
    for item, checkpoint, override in zip(positions, saved, pending, strict=True):
        committed = item["committedOffset"]
        item["lag"] = max(0, item["endOffset"] - committed) if committed is not None else None
        item["currentOffset"] = int(checkpoint) if checkpoint is not None else committed
        item["pending"] = override is not None
        item["offset"] = int(override) if override is not None else item["currentOffset"]
        item["source"] = "pending_override" if override is not None else "redis" if checkpoint is not None else "kafka"
        if item["offset"] is None:
            item["offset"] = item["beginningOffset"]
            item["source"] = "earliest retained"
    return {**result, "available": True, "offsets": positions, "checkedAt": datetime.now(timezone.utc).isoformat()}


async def set_offset(layer: str, table: str, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("confirmation") != "SET OFFSET":
        raise HTTPException(400, "Offset changes require confirmation: SET OFFSET")
    partition, offset = payload.get("partition"), payload.get("offset")
    if type(partition) is not int or partition < 0 or type(offset) is not int or offset < 0:
        raise HTTPException(422, "Partition and offset must be non-negative integers")
    state = await stream(layer, table, payload.get("topic"), include_hidden=True, fresh=True)
    if not state["available"] or not state["resumeAvailable"]:
        raise HTTPException(503, state.get("reason", "Stream is unavailable"))
    position = next((item for item in state["offsets"] if item["partition"] == partition), None)
    if position is None:
        raise HTTPException(422, "Kafka partition does not exist")
    if not position["beginningOffset"] <= offset <= position["endOffset"]:
        raise HTTPException(422, "Offset must be within the retained Kafka partition bounds")
    try:
        redis = await _REDIS.ensure_connected(raise_errors=True)
        if redis is None:
            raise ConnectionError("Redis is unavailable")
        key = f"{OVERRIDE_KEY_PREFIX}:{state['consumerGroup']}:{state['topic']}:{partition}"
        if not await get_offset_manager().set_offset(state["consumerGroup"], state["topic"], partition, offset):
            raise ConnectionError("Redis offset override persistence failed")
        if await redis.get(key) != str(offset):
            raise ConnectionError("Redis offset verification failed")
    except Exception as exc:
        _REDIS.mark_unavailable()
        raise HTTPException(503, "Resume offset could not be persisted and verified in Redis") from exc
    return {"restartRequired": True, "pending": True, "topic": state["topic"], "partition": partition, "offset": offset}


async def script(layer: str, table: str) -> dict[str, Any]:
    layer_name, logical, row = await _table(layer, table)
    source, code, handlers = _source(layer_name)
    if not code:
        return {"available": False, "reason": "Deployed worker source is unavailable", "code": ""}
    handler_available = row["tableRole"] == "current" and (layer_name in {"rds", "ods"} or logical in handlers)
    return {
        "available": True, "code": code, "language": "python",
        "path": f"app/etl/{source.name}", "sourcePath": f"app/etl/{source.name}",
        "handler": "_dispatch" if layer_name in {"rds", "ods"} else f"_handler_{logical}",
        "handlerAvailable": handler_available,
        "reason": None if handler_available else "Layer source shown; this table has no deployed handler",
        "updatedAt": datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat(),
    }


def validate(code: str) -> dict[str, Any]:
    if not isinstance(code, str) or not code.strip():
        return {"valid": False, "errors": ["Code must not be empty"]}
    try:
        tree = ast.parse(code, filename="<handler-draft>")
        compile(tree, "<handler-draft>", "exec")
    except (SyntaxError, ValueError, TypeError) as exc:
        line = f"Line {exc.lineno}: " if isinstance(exc, SyntaxError) else ""
        message = exc.msg if isinstance(exc, SyntaxError) else str(exc)
        return {"valid": False, "errors": [f"{line}{message}"]}
    return {"valid": True, "errors": []}


async def read_handler(layer: str, table: str) -> dict[str, Any]:
    _, _, row = await _table(layer, table)
    pg = get_pg_client()
    draft = None
    try:
        with fail_after(12):
            exists = await pg.fetch_one("SELECT to_regclass('app.flow_handler_drafts') AS name")
            if exists and exists.get("name"):
                draft = await pg.fetch_one(
                    "SELECT code, updated_at FROM app.flow_handler_drafts "
                    "WHERE schema_name = :schema AND table_name = :table",
                    {"schema": row["schemaName"], "table": row["name"]},
                )
    except Exception as exc:
        raise HTTPException(503, "Handler drafts could not be read from PostgreSQL") from exc
    deployed = await script(layer, table)
    return {
        "layer": layer, "table": table,
        "code": draft["code"] if draft else deployed["code"],
        "updatedAt": draft["updated_at"].isoformat() if draft else deployed.get("updatedAt", ""),
        "draft": draft is not None, "deployed": False if draft else deployed.get("handlerAvailable", False),
        "available": draft is not None or deployed["available"],
        "reason": "Saved drafts require a separate worker deployment" if draft else deployed.get("reason"),
    }


async def save_handler(layer: str, table: str, code: str) -> dict[str, Any]:
    checked = validate(code)
    if not checked["valid"]:
        raise HTTPException(422, checked["errors"])
    _, _, row = await _table(layer, table)
    pg = get_pg_client()
    try:
        with fail_after(12):
            await pg.init_schema([_DRAFT_DDL])
            saved = await pg.fetch_one(
                """INSERT INTO app.flow_handler_drafts (schema_name, table_name, code)
                VALUES (:schema, :table, :code)
                ON CONFLICT (schema_name, table_name)
                DO UPDATE SET code = EXCLUDED.code, updated_at = now()
                RETURNING code, updated_at""",
                {"schema": row["schemaName"], "table": row["name"], "code": code},
            )
    except Exception as exc:
        raise HTTPException(503, "Handler draft could not be persisted in PostgreSQL") from exc
    if saved is None:
        raise HTTPException(503, "Handler draft could not be persisted")
    return {
        "layer": layer, "table": table, "code": saved["code"],
        "updatedAt": saved["updated_at"].isoformat(), "draft": True, "deployed": False,
        "reason": "Draft saved; a separate worker deployment is required to activate it",
    }


async def dry_run(layer: str, table: str, payload: dict[str, Any]) -> dict[str, Any]:
    from app.web.services.etl_layers import get_etl_layers

    code = payload.get("code")
    if code is None:
        code = (await read_handler(layer, table))["code"]
    checked = validate(code)
    sample = await get_etl_layers().data(layer, table, {"limit": 1})
    return {
        "available": False, **checked, "sample": sample["rows"], "rows": [],
        "reason": "Syntax checked only. Deployed handlers use external services; isolated execution is not configured",
    }
