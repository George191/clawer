"""Cross-process notifications for AI Collect task changes."""

from __future__ import annotations

import json

from app.base.redis_connection import RedisConnection
from app.config.settings import settings
from app.logger import get_logger

logger = get_logger(__name__)

TASK_EVENT_CHANNEL = "ai_collect:task_events"
_publisher_connection = RedisConnection(settings.redis_url)


async def publish_task_change(task_id: str) -> None:
    """Best-effort notification; PostgreSQL remains the source of truth."""
    redis = await _publisher_connection.ensure_connected()
    if redis is None:
        return
    try:
        await redis.publish(TASK_EVENT_CHANNEL, json.dumps({"task_id": task_id}))
    except Exception as exc:
        _publisher_connection.mark_unavailable()
        logger.warning("Failed to publish task change for %s: %s", task_id, exc)


async def close_task_event_publisher() -> None:
    await _publisher_connection.close()
