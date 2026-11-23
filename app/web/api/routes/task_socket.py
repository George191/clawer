"""Task subscriptions from the historical workspace panel's WebSocket protocol."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from uuid import UUID

from anyio import create_task_group
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from jwt import InvalidTokenError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.logger import get_logger
from app.scheduler.task_repository import get_task_repository
from app.storage.redis_client import RedisClient
from app.web.services.automation_store import automation_store
from app.web.services.automation_task_store import automation_task_store
from app.web.services.task_events import TASK_EVENT_CHANNEL
from app.web.api.dependencies.common import get_current_user
from app.web.api.routes.ai_collect import AdapterRequest, GenerateRequest, build_adapter, build_template
from app.web.core.db import engine
from app.web.core.security import decode_session_token, TokenType

logger = get_logger(__name__)


class ClientConnection:
    def __init__(self, websocket: WebSocket, expires_at: float):
        self.websocket = websocket
        self.expires_at = expires_at
        self.subscriptions: set[str] = set()
        self.send_lock = asyncio.Lock()

    async def send(self, message: dict | None = None, *, snapshot_channel: str | None = None) -> None:
        async with self.send_lock:
            if time.time() >= self.expires_at:
                await self.websocket.close(code=4401)
                return
            if snapshot_channel is not None:
                message = await _channel_snapshot(snapshot_channel)
            if time.time() >= self.expires_at:
                await self.websocket.close(code=4401)
                return
            await asyncio.wait_for(self.websocket.send_json(jsonable_encoder(message)), timeout=5)


_connections: set[ClientConnection] = set()
_task_event_connection = RedisClient(settings.task_redis_url, retry_interval=3)
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
    task = await automation_task_store.get_task(task_id)
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
        return {"type": "tasks_snapshot", "data": await automation_task_store.list_tasks()}
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
    if payload.get("type") in {"task_log", "task_run"}:
        await _broadcast({"type": payload["type"], "task_id": task_id, "data": payload["data"]}, {channel})
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


async def _close_at_expiry(connection: ClientConnection) -> None:
    await asyncio.sleep(max(0, connection.expires_at - time.time()))
    with suppress(Exception):
        await connection.websocket.close(code=4401)


async def _run_scout(message: dict, send: Callable[[dict], Awaitable[None]]) -> None:
    request_id = message["request_id"]
    analyzing = message["type"] == "start_analyze"
    kind = "analyze" if analyzing else "adapter"
    events: asyncio.Queue[dict | None] = asyncio.Queue()

    def emit(event: str, data: dict) -> None:
        events.put_nowait({"request_id": request_id, "type": event, "data": data})

    async def forward() -> None:
        while (event := await events.get()) is not None:
            await send(event)

    async with create_task_group() as group:
        group.start_soon(forward)
        try:
            emit(f"{kind}_started", {})
            if analyzing:
                payload = GenerateRequest.model_validate({
                    "url": message.get("url"),
                    "options": {
                        "userRequest": message.get("prompt", ""),
                        "currentTemplateYaml": message.get("current_template_yaml", ""),
                        "viewportWidth": message.get("viewport_width", 1440),
                        "viewportHeight": message.get("viewport_height", 1000),
                    },
                })
                result = await build_template(payload, emit)
                emit("analyze_fields", {"fields": result["fields"]})
                emit("analyze_pagination", result["pagination"])
                emit("analyze_complete", {**result, "templateYaml": result["yaml"]})
            else:
                payload = AdapterRequest.model_validate({
                    "url": message.get("url"), "templateId": message.get("template_id"),
                    "prompt": message.get("prompt", ""),
                    "currentAdapterCode": message.get("current_adapter_code", ""),
                })
                result = await build_adapter(payload, lambda delta: emit("adapter_delta", {"content": delta}))
                emit("adapter_ready", result)
        except HTTPException as exc:
            emit(f"{kind}_error", {"message": str(exc.detail)})
        except ValidationError:
            emit(f"{kind}_error", {"message": "采集参数无效或超出长度限制"})
        except Exception:
            logger.exception("Scout %s request failed", kind)
            emit(f"{kind}_error", {"message": "采集分析失败，请查看服务日志"})
        finally:
            events.put_nowait(None)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    protocols = [value.strip() for value in websocket.headers.get("sec-websocket-protocol", "").split(',')]
    if len(protocols) != 2 or protocols[0] != 'bearer':
        await websocket.close(code=4401)
        return
    try:
        payload = decode_session_token(protocols[1], TokenType.ACCESS_TOKEN)
        async with AsyncSession(engine) as session:
            await get_current_user(session, protocols[1])
    except (InvalidTokenError, HTTPException):
        await websocket.close(code=4401)
        return
    await websocket.accept(subprotocol='bearer')
    connection = ClientConnection(websocket, payload['exp'])
    expiry = asyncio.create_task(_close_at_expiry(connection))
    _connections.add(connection)
    scout_task: asyncio.Task | None = None
    scout_request_id: str | None = None
    try:
        await connection.send({"type": "task_stream_status", "data": {"available": _stream_available}})
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=max(0, connection.expires_at - time.time()))
                action = message["type"]
                if action in {"start_analyze", "generate_adapter", "cancel_analyze"}:
                    request_id = message.get("request_id")
                    if not isinstance(request_id, str) or not 0 < len(request_id) <= 128:
                        await connection.send({"type": "error", "message": "Invalid Scout request ID"})
                        continue
                    if action == "cancel_analyze" and request_id != scout_request_id:
                        continue
                    if scout_task:
                        scout_task.cancel()
                        await asyncio.gather(scout_task, return_exceptions=True)
                    if action == "cancel_analyze":
                        scout_task = None
                        scout_request_id = None
                        await connection.send({"type": "analyze_cancelled", "request_id": request_id, "data": {}})
                    else:
                        scout_request_id = request_id
                        scout_task = asyncio.create_task(_run_scout(message, connection.send))
                    continue
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
    except asyncio.TimeoutError:
        with suppress(Exception):
            await websocket.close(code=4401)
    except Exception:
        logger.exception("Task WebSocket connection failed")
        with suppress(Exception):
            await websocket.close(code=1011)
    finally:
        if scout_task:
            scout_task.cancel()
            await asyncio.gather(scout_task, return_exceptions=True)
        expiry.cancel()
        with suppress(asyncio.CancelledError):
            await expiry
        _connections.discard(connection)
