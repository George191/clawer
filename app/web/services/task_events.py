"""Cross-process notifications for AI Collect task changes."""

from __future__ import annotations

import asyncio
import json

from app.base.redis_connection import RedisConnection
from app.config.settings import settings
from app.logger import get_logger

logger = get_logger(__name__)

TASK_EVENT_CHANNEL = "task_events"
_PUBLISH_TIMEOUT_SECONDS = 3.0
_publisher_connection = RedisConnection(settings.task_redis_url)


async def publish_task_change(task_id: str) -> None:
    """Best-effort notification; PostgreSQL remains the source of truth."""
    try:
        redis = await asyncio.wait_for(
            _publisher_connection.ensure_connected(),
            timeout=_PUBLISH_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        _publisher_connection.mark_unavailable()
        logger.warning("Timed out connecting task event publisher for %s", task_id)
        return
    if redis is None:
        return
    try:
        await asyncio.wait_for(
            redis.publish(TASK_EVENT_CHANNEL, json.dumps({"task_id": task_id})),
            timeout=_PUBLISH_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        _publisher_connection.mark_unavailable()
        logger.warning("Timed out publishing task change for %s", task_id)
    except Exception as exc:
        _publisher_connection.mark_unavailable()
        logger.warning("Failed to publish task change for %s: %s", task_id, exc)


async def publish_task_log(task_id: str, log: dict) -> None:
    """Publish one persisted task log line for genuine incremental rendering."""
    try:
        redis = await asyncio.wait_for(
            _publisher_connection.ensure_connected(),
            timeout=_PUBLISH_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        _publisher_connection.mark_unavailable()
        logger.warning("Timed out connecting task log publisher for %s", task_id)
        return
    if redis is None:
        return
    payload = dict(log)
    created_at = payload.get("created_at")
    if hasattr(created_at, "isoformat"):
        payload["created_at"] = created_at.isoformat()
    try:
        await asyncio.wait_for(
            redis.publish(
                TASK_EVENT_CHANNEL,
                json.dumps({"type": "task_log", "task_id": task_id, "data": payload}),
            ),
            timeout=_PUBLISH_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        _publisher_connection.mark_unavailable()
        logger.warning("Timed out publishing task log for %s", task_id)
    except Exception as exc:
        _publisher_connection.mark_unavailable()
        logger.warning("Failed to publish task log for %s: %s", task_id, exc)


async def close_task_event_publisher() -> None:
    await _publisher_connection.close()
