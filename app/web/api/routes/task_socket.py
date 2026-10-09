"""Task subscriptions from the historical workspace panel's WebSocket protocol."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from app.base.redis_connection import RedisConnection
from app.config.settings import settings
from app.logger import get_logger
from app.scheduler.task_repository import get_task_repository
from app.web.services.ai_collect_store import ai_collect_store
from app.web.services.automation_store import automation_store
from app.web.services.task_events import TASK_EVENT_CHANNEL

logger = get_logger(__name__)


class ClientConnection:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.subscriptions: set[str] = set()
        self.send_lock = asyncio.Lock()

    async def send(self, message: dict | None = None, *, snapshot_channel: str | None = None) -> None:
        async with self.send_lock:
            if snapshot_channel is not None:
                message = await _channel_snapshot(snapshot_channel)
            await asyncio.wait_for(self.websocket.send_json(jsonable_encoder(message)), timeout=5)


_connections: set[ClientConnection] = set()
_task_event_connection = RedisConnection(settings.task_redis_url, retry_interval=3)
_stream_available = False


async def _broadcast(message: dict | None = None, channels: set[str] | None = None, *, snapshot_channel: str | None = None) -> None:
    recipients = [
        connection for connection in tuple(_connections)
        if channels is None or connection.subscriptions.intersection(channels)
    ]
    results = await asyncio.gather(
        *(connection.send(message, snapshot_channel=snapshot_channel) for connection in recipients),
        return_exceptions=True,
    )
    for connection, result in zip(recipients, results):
        if isinstance(result, Exception):
            _connections.discard(connection)
            with suppress(Exception):
                await connection.websocket.close(code=1011)


async def _set_stream_available(available: bool) -> None:
    global _stream_available
    if _stream_available != available:
        _stream_available = available
        await _broadcast({"type": "task_stream_status", "data": {"available": available}})
        if available:
            # Redis may reconnect while browser sockets stay open. Recover missed changes.
            channels = {channel for connection in tuple(_connections) for channel in connection.subscriptions}
            for channel in channels:
                await _broadcast(channels={channel}, snapshot_channel=channel)


async def _task_snapshot(task_id: str) -> dict:
    task = await ai_collect_store.get_task(task_id)
    if task is None:
        return {"type": "task_deleted", "task_id": task_id}
    return {"type": "task_detail", "task_id": task_id, "data": task}


async def _channel_snapshot(channel: str) -> dict:
    if channel == "workflows":
        return {"type": "workflows_snapshot", "data": await automation_store.list_workflows()}
    if channel == "schedules":
        schedules = [asdict(config) for config in await get_task_repository().list_all()]
        return {"type": "schedules_snapshot", "data": schedules}
    if channel == "tasks":
        return {"type": "tasks_snapshot", "data": await ai_collect_store.list_tasks()}
    return await _task_snapshot(channel.removeprefix("task:"))


async def _forward_task_event(payload: dict) -> None:
    if payload.get("type") == "automation_changed":
        channel = payload["channel"]
        if channel not in {"workflows", "schedules"}:
            raise ValueError("Unsupported automation channel")
        await _broadcast(channels={channel}, snapshot_channel=channel)
        return
    task_id = str(UUID(str(payload["task_id"])))
    channel = f"task:{task_id}"
    if payload.get("type") == "task_log":
        await _broadcast({"type": "task_log", "task_id": task_id, "data": payload["data"]}, {channel})
    elif any(connection.subscriptions.intersection({"tasks", channel}) for connection in _connections):
        await _broadcast(await _task_snapshot(task_id), {"tasks", channel})


async def _listen_for_task_events() -> None:
    while True:
        redis = await _task_event_connection.ensure_connected()
        if redis is None:
            await _set_stream_available(False)
            await asyncio.sleep(1)
            continue
        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(TASK_EVENT_CHANNEL)
            await _set_stream_available(True)
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                if not message:
                    continue
                try:
                    await _forward_task_event(json.loads(message["data"]))
                except (KeyError, TypeError, ValueError):
                    logger.warning("Ignoring malformed task event")
        except asyncio.CancelledError:
            raise
        except Exception:
            _task_event_connection.mark_unavailable()
            await _set_stream_available(False)
            logger.exception("Task event listener failed")
            await asyncio.sleep(1)
        finally:
            with suppress(Exception):
                await pubsub.aclose()


@asynccontextmanager
async def lifespan(_app):
    listener = asyncio.create_task(_listen_for_task_events())
    try:
        yield
    finally:
        listener.cancel()
        with suppress(asyncio.CancelledError):
            await listener
        await _set_stream_available(False)
        for connection in tuple(_connections):
            with suppress(Exception):
                await connection.websocket.close(code=1001)
        _connections.clear()
        await _task_event_connection.close()


router = APIRouter(lifespan=lifespan)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connection = ClientConnection(websocket)
    _connections.add(connection)
    try:
        await connection.send({"type": "task_stream_status", "data": {"available": _stream_available}})
        while True:
            try:
                message = await websocket.receive_json()
                action = message["type"]
                channel = message["channel"]
                if action not in {"subscribe", "unsubscribe"} or not isinstance(channel, str):
                    raise ValueError("Unsupported subscription")
                if channel not in {"tasks", "workflows", "schedules"}:
                    if not channel.startswith("task:"):
                        raise ValueError("Unsupported channel")
                    channel = f"task:{UUID(channel.removeprefix('task:'))}"
            except (KeyError, TypeError, ValueError):
                await connection.send({"type": "error", "code": "INVALID_SUBSCRIPTION", "message": "Use workflows, schedules, tasks or task:<uuid> subscriptions."})
                continue
            if action == "subscribe":
                connection.subscriptions.add(channel)
                await connection.send(snapshot_channel=channel)
                await connection.send({"type": "subscribed", "channel": channel})
            else:
                connection.subscriptions.discard(channel)
                await connection.send({"type": "unsubscribed", "channel": channel})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Task WebSocket connection failed")
        with suppress(Exception):
            await websocket.close(code=1011)
    finally:
        _connections.discard(connection)
