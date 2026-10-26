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
from app.storage.postgres_client import get_pg_client
from app.storage.redis_client import RedisClient

_ETL_PATH = Path(__file__).resolve().parents[2] / "etl"
_REDIS = RedisClient(settings.etl_redis_url)
_DRAFT_DDL = """
CREATE TABLE IF NOT EXISTS public.flow_handler_drafts (
    schema_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    code TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (schema_name, table_name)
)
"""


async def _table(layer: str, table: str) -> tuple[str, str, dict[str, Any]]:
    from app.web.services.etl_layers import get_etl_layers

    row = await get_etl_layers().get_table(layer, table)
    schema = row["schemaName"]
    name = schema.removeprefix("ts_").removesuffix("_hist")
    logical = row["name"].removeprefix(f"{name}_")
    return name, logical, row


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
    if table not in _source(layer)[2]:
        return "", []
    topics: list[str] = []
    group = getattr(settings, f"etl_{layer}_consumer_group", "")
    if layer == "rds":
        topics = [settings.etl_raw_topic]
        if settings.kafka_topic:
            topics.append(f"{settings.kafka_topic}-{table}")
    elif layer == "ods":
        topics = [f"{settings.etl_rds_topic}-{table}"]
    elif layer in {"task", "dwd", "dim"}:
        topics = [settings.etl_ods_topic]
        if layer == "dwd":
            topics.append(settings.etl_task_topic)
    elif layer == "dws":
        topics = [settings.etl_dwd_topic]
    return group, list(dict.fromkeys(topic for topic in topics if topic))


async def _kafka_positions(topic: str, group: str) -> list[dict[str, Any]]:
    # Neither client subscribes or joins a consumer group, so inspecting the
    # workspace cannot rebalance running workers or advance their positions.
    admin = AIOKafkaAdminClient(**build_admin_client_kwargs(request_timeout_ms=5000))
    consumer = AIOKafkaConsumer(**build_consumer_kwargs(group_id=None, request_timeout_ms=5000))
    try:
        await admin.start()
        if topic not in await admin.list_topics():
            raise HTTPException(409, "Kafka topic has not been created")
        descriptions = await admin.describe_topics([topic])
        partitions = [
            TopicPartition(topic, item["partition"])
            for item in descriptions[0].get("partitions", [])
        ]
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


async def stream(layer: str, table: str, topic: str | None = None) -> dict[str, Any]:
    layer_name, logical, row = await _table(layer, table)
    group, topics = (
        _binding(layer_name, logical) if row["schemaName"] == f"ts_{layer_name}" else ("", [])
    )
    selected = topic or (topics[0] if topics else None)
    result: dict[str, Any] = {
        "available": False, "consumerGroup": group, "topics": topics,
        "topic": selected, "offsets": [], "throughput": None,
        "throughputReason": "Throughput metrics are not collected by the deployed workers",
    }
    if topic and topic not in topics:
        raise HTTPException(400, "Topic does not belong to this connector")
    if not group or not selected:
        return {**result, "reason": "No deployed consumer is registered for this schema"}
    if not settings.kafka_brokers or not settings.etl_redis_url:
        return {**result, "reason": "Kafka and Redis must both be configured to inspect resume positions"}
    try:
        redis = await _REDIS.ensure_connected(raise_errors=True)
        if redis is None:
            raise ConnectionError("Redis is unavailable")
        positions = await asyncio.wait_for(_kafka_positions(selected, group), timeout=15)
        keys = [f"offset:{group}:{selected}:{item['partition']}" for item in positions]
        pending_keys = [f"{OVERRIDE_KEY_PREFIX}:{group}:{selected}:{item['partition']}" for item in positions]
        values = await redis.mget([*keys, *pending_keys])
        saved, pending = values[:len(keys)], values[len(keys):]
        for item, value, override in zip(positions, saved, pending, strict=True):
            current = value
            if override is not None:
                value = override
            item["offset"] = (
                int(value) if value is not None else
                item["committedOffset"] if item["committedOffset"] is not None else
                item["beginningOffset"]
            )
            item["source"] = "pending_override" if override is not None else "redis" if value is not None else "kafka"
            item["pending"] = override is not None
            item["currentOffset"] = int(current) if current is not None else item["committedOffset"]
            item["lag"] = max(0, item["endOffset"] - item["offset"])
        return {**result, "available": True, "offsets": positions}
    except HTTPException as exc:
        return {**result, "reason": str(exc.detail)}
    except Exception:
        _REDIS.mark_unavailable()
        return {**result, "reason": "Kafka or Redis is unavailable; live resume positions could not be read"}


async def set_offset(layer: str, table: str, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("confirmation") != "SET OFFSET":
        raise HTTPException(400, "Offset changes require confirmation: SET OFFSET")
    partition, offset = payload.get("partition"), payload.get("offset")
    if type(partition) is not int or partition < 0 or type(offset) is not int or offset < 0:
        raise HTTPException(422, "Partition and offset must be non-negative integers")
    state = await stream(layer, table, payload.get("topic"))
    if not state["available"]:
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
    handler_available = logical in handlers and row["schemaName"] == f"ts_{layer_name}"
    return {
        "available": True, "code": code, "language": "python",
        "path": f"app/etl/{source.name}", "sourcePath": f"app/etl/{source.name}",
        "handler": f"_handler_{logical}",
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
            exists = await pg.fetch_one("SELECT to_regclass('public.flow_handler_drafts') AS name")
            if exists and exists.get("name"):
                draft = await pg.fetch_one(
                    "SELECT code, updated_at FROM public.flow_handler_drafts "
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
                """INSERT INTO public.flow_handler_drafts (schema_name, table_name, code)
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
