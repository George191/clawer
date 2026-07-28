"""WebSocket 实时通信模块 — 前后端实时数据链路。

提供任务监控、实时分析、日志推送、双向指令等全双工通信能力。

WebSocket 消息协议:
    客户端发送 -> 服务端:
        {"type": "subscribe", "channel": "task:<task_id>", "data": {...}}
        {"type": "unsubscribe", "channel": "task:<task_id>"}
        {"type": "command", "action": "pause_task", "task_id": "..."}
        {"type": "command", "action": "resume_task", "task_id": "..."}
        {"type": "command", "action": "cancel_task", "task_id": "..."}
        {"type": "start_analyze", "request_id": "...", "url": "...", "prompt": "..."}
        {"type": "cancel_analyze", "request_id": "..."}

    服务端推送 -> 客户端:
        {"type": "task_status", "task_id": "...", "data": {...}}
        {"type": "task_log", "task_id": "...", "data": {...}}
        {"type": "task_progress", "task_id": "...", "data": {...}}
        {"type": "analyze_step", "data": {...}}
        {"type": "analyze_result", "data": {...}}
        {"type": "template_ready", "data": {...}}
        {"type": "adapter_ready", "data": {...}}
        {"type": "error", "code": "...", "message": "..."}
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.base.redis_connection import RedisConnection
from app.config.settings import settings
from app.logger import get_logger
from app.web.routes.ai_collect import (
    _analyze_events,
    _build_yaml_template,
    _generate_adapter_for_template,
)
from app.web.services.ai_collect_store import ai_collect_store
from app.web.services.task_events import TASK_EVENT_CHANNEL
from app.web.utils.validation import (
    scope_limit,
    validate_target_url,
)

logger = get_logger(__name__)

router = APIRouter()

# ── 连接管理 ───────────────────────────────────────────────────────────────────

class ClientConnection:
    """WebSocket客户端连接管理."""

    def __init__(self, websocket: WebSocket, client_id: str):
        self.websocket = websocket
        self.client_id = client_id
        self.subscriptions: set[str] = set()
        self.active_tasks: set[str] = set()
        self._send_lock = asyncio.Lock()
        self._closed = False
        self.analyze_task: asyncio.Task[None] | None = None
        self.adapter_task: asyncio.Task[None] | None = None

    async def send(self, message: dict[str, Any]) -> bool:
        """发送消息到客户端."""
        async with self._send_lock:
            if self._closed:
                return False
            try:
                await self.websocket.send_text(json.dumps(message, ensure_ascii=False))
                return True
            except Exception as e:
                logger.warning("Failed to send message to %s: %s", self.client_id, e)
                return False

    async def close(self) -> None:
        """关闭连接."""
        if self.analyze_task and not self.analyze_task.done():
            self.analyze_task.cancel()
        if self.adapter_task and not self.adapter_task.done():
            self.adapter_task.cancel()
        async with self._send_lock:
            if self._closed:
                return
            self._closed = True
            try:
                await self.websocket.close()
            except Exception:
                pass


class ConnectionManager:
    """WebSocket连接管理器."""

    def __init__(self):
        self.active_connections: dict[str, ClientConnection] = {}
        self.channel_subscribers: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> ClientConnection:
        """新客户端连接."""
        await websocket.accept()
        client_id = str(uuid.uuid4())[:8]
        connection = ClientConnection(websocket, client_id)
        async with self._lock:
            self.active_connections[client_id] = connection
        logger.info("WebSocket client connected: %s", client_id)
        return connection

    async def disconnect(self, client_id: str) -> None:
        """客户端断开连接."""
        async with self._lock:
            connection = self.active_connections.pop(client_id, None)
            if connection:
                for channel in connection.subscriptions:
                    subscribers = self.channel_subscribers.get(channel)
                    if subscribers:
                        subscribers.discard(client_id)
                        if not subscribers:
                            self.channel_subscribers.pop(channel, None)
        if connection:
            await connection.close()
        logger.info("WebSocket client disconnected: %s", client_id)

    async def subscribe(self, client_id: str, channel: str) -> None:
        """订阅频道."""
        async with self._lock:
            connection = self.active_connections.get(client_id)
            if connection:
                connection.subscriptions.add(channel)
                if channel not in self.channel_subscribers:
                    self.channel_subscribers[channel] = set()
                self.channel_subscribers[channel].add(client_id)

    async def unsubscribe(self, client_id: str, channel: str) -> None:
        """取消订阅频道."""
        async with self._lock:
            connection = self.active_connections.get(client_id)
            if connection:
                connection.subscriptions.discard(channel)
                subscribers = self.channel_subscribers.get(channel)
                if subscribers:
                    subscribers.discard(client_id)
                    if not subscribers:
                        self.channel_subscribers.pop(channel, None)

    async def broadcast(self, channel: str, message: dict[str, Any]) -> None:
        """向频道订阅者广播消息."""
        async with self._lock:
            subscriber_ids = self.channel_subscribers.get(channel, set()).copy()
            connections = [
                self.active_connections[subscriber_id]
                for subscriber_id in subscriber_ids
                if subscriber_id in self.active_connections
            ]
        if connections:
            await asyncio.gather(*(connection.send(message) for connection in connections))

    async def send_to(self, client_id: str, message: dict[str, Any]) -> None:
        """向特定客户端发送消息."""
        async with self._lock:
            connection = self.active_connections.get(client_id)
        if connection:
            await connection.send(message)


manager = ConnectionManager()
_task_event_connection = RedisConnection(settings.redis_url, retry_interval=3)
_task_event_listener_task: asyncio.Task[None] | None = None


async def _send_task_snapshot(task_id: str, connection: ClientConnection | None = None) -> None:
    task = await ai_collect_store.get_task(task_id)
    message = (
        {"type": "task_detail", "task_id": task_id, "data": jsonable_encoder(task)}
        if task is not None
        else {"type": "task_deleted", "task_id": task_id, "data": {}}
    )
    if connection is not None:
        await connection.send(message)
    else:
        await manager.broadcast(f"task:{task_id}", message)


async def _listen_for_task_events() -> None:
    while True:
        redis = await _task_event_connection.ensure_connected()
        if redis is None:
            await asyncio.sleep(1)
            continue

        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(TASK_EVENT_CHANNEL)
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1)
                if not message:
                    continue
                payload = json.loads(message["data"])
                task_id = str(payload.get("task_id") or "")
                if task_id:
                    if payload.get("type") == "task_log":
                        await manager.broadcast(
                            f"task:{task_id}",
                            {
                                "type": "task_log",
                                "task_id": task_id,
                                "data": payload.get("data") or {},
                            },
                        )
                    else:
                        await _send_task_snapshot(task_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _task_event_connection.mark_unavailable()
            logger.warning("Task event listener failed: %s", exc)
            await asyncio.sleep(1)
        finally:
            with suppress(Exception):
                await pubsub.aclose()


async def start_task_event_listener() -> None:
    global _task_event_listener_task
    if _task_event_listener_task is None or _task_event_listener_task.done():
        _task_event_listener_task = asyncio.create_task(_listen_for_task_events())


async def stop_task_event_listener() -> None:
    global _task_event_listener_task
    if _task_event_listener_task is not None:
        _task_event_listener_task.cancel()
        with suppress(asyncio.CancelledError):
            await _task_event_listener_task
        _task_event_listener_task = None
    await _task_event_connection.close()

# ── 消息模型 ───────────────────────────────────────────────────────────────────


class SubscribeRequest(BaseModel):
    type: str = "subscribe"
    channel: str = Field(..., description="订阅频道, 如 task:abc123")


class UnsubscribeRequest(BaseModel):
    type: str = "unsubscribe"
    channel: str = Field(..., description="取消订阅频道")


class CommandRequest(BaseModel):
    type: str = "command"
    action: str = Field(..., description="操作指令: pause_task/resume_task/cancel_task")
    task_id: str = Field(..., description="目标任务ID")


class StartAnalyzeRequest(BaseModel):
    type: str = "start_analyze"
    url: str = Field(..., description="目标页面URL")
    options: dict[str, Any] | None = None


# ── 任务运行时模拟 ─────────────────────────────────────────────────────────────

class TaskRuntime:
    """任务运行时状态管理."""

    def __init__(self, task_id: str, template: str, params: dict[str, str]):
        self.task_id = task_id
        self.template = template
        self.params = params
        self.status = "queued"
        self.progress = 0
        self.records = 0
        self.throughput = 0
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._is_running = False
        self._is_paused = False
        self._cancel_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动任务运行循环."""
        if self._is_running:
            return
        self._is_running = True
        self.status = "running"
        self._task = asyncio.create_task(self._run_loop())

    async def _run_loop(self) -> None:
        """任务运行主循环."""
        try:
            for iteration in range(100):
                if self._cancel_event.is_set():
                    self.status = "failed"
                    break

                await self._pause_event.wait()

                self.progress = iteration + 1
                self.records += 80 + iteration * 5
                self.throughput = 15 + (iteration % 10)

                await asyncio.sleep(1)

                await manager.broadcast(
                    f"task:{self.task_id}",
                    {
                        "type": "task_progress",
                        "task_id": self.task_id,
                        "data": {
                            "progress": self.progress,
                            "records": self.records,
                            "throughput": self.throughput,
                        },
                    },
                )

                if iteration % 3 == 0:
                    await manager.broadcast(
                        f"task:{self.task_id}",
                        {
                            "type": "task_log",
                            "task_id": self.task_id,
                            "data": {
                                "time": datetime.now(timezone.utc).isoformat(),
                                "level": "info",
                                "message": f"Processed batch {iteration // 3 + 1}, records: {self.records}",
                            },
                        },
                    )

            if not self._cancel_event.is_set() and self._is_paused:
                self.status = "paused"
            elif not self._cancel_event.is_set():
                self.status = "completed"
                self.progress = 100
        finally:
            self._is_running = False

    async def pause(self) -> None:
        """暂停任务."""
        if self.status == "running":
            self._pause_event.clear()
            self.status = "paused"
            self.throughput = 0

    async def resume(self) -> None:
        """恢复任务."""
        if self.status == "paused":
            self._pause_event.set()
            self.status = "running"

    async def cancel(self) -> None:
        """取消任务."""
        self._cancel_event.set()
        self._pause_event.set()
        if self._task:
            self._task.cancel()


_task_runtimes: dict[str, TaskRuntime] = {}


async def create_task_runtime(task_id: str, template: str, params: dict[str, str]) -> TaskRuntime:
    """创建任务运行时实例."""
    runtime = TaskRuntime(task_id, template, params)
    _task_runtimes[task_id] = runtime
    await runtime.start()
    return runtime


# ── WebSocket 端点 ─────────────────────────────────────────────────────────────


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket主端点 — 处理所有实时通信."""
    connection = await manager.connect(websocket)

    try:
        while True:
            raw_message = await websocket.receive_text()
            await handle_client_message(connection, raw_message)

    except WebSocketDisconnect:
        await manager.disconnect(connection.client_id)
    except asyncio.CancelledError:
        await manager.disconnect(connection.client_id)
    except Exception as e:
        logger.exception("WebSocket error for client %s: %s", connection.client_id, e)
        await manager.disconnect(connection.client_id)


# ── 消息处理 ───────────────────────────────────────────────────────────────────


async def handle_client_message(connection: ClientConnection, raw_message: str) -> None:
    """处理客户端消息."""
    try:
        message = json.loads(raw_message)
    except json.JSONDecodeError:
        await connection.send({"type": "error", "code": "INVALID_JSON", "message": "无效的JSON格式"})
        return

    message_type = message.get("type")

    handlers: dict[str, Callable] = {
        "subscribe": handle_subscribe,
        "unsubscribe": handle_unsubscribe,
        "command": handle_command,
        "start_analyze": handle_start_analyze,
        "cancel_analyze": handle_cancel_analyze,
        "start_dry_run": handle_start_dry_run,
        "generate_template": handle_generate_template,
        "generate_adapter": handle_generate_adapter,
    }

    handler = handlers.get(message_type)
    if handler:
        try:
            await handler(connection, message)
        except Exception as e:
            logger.exception("Error handling %s message: %s", message_type, e)
            await connection.send({"type": "error", "code": "INTERNAL_ERROR", "message": str(e)})
    else:
        await connection.send({"type": "error", "code": "UNKNOWN_TYPE", "message": f"未知消息类型: {message_type}"})


async def handle_subscribe(connection: ClientConnection, message: dict[str, Any]) -> None:
    """处理订阅请求."""
    channel = message.get("channel")
    if not channel:
        await connection.send({"type": "error", "code": "MISSING_CHANNEL", "message": "缺少频道参数"})
        return

    await manager.subscribe(connection.client_id, channel)

    if channel.startswith("task:"):
        task_id = channel.removeprefix("task:")
        await _send_task_snapshot(task_id, connection)

    await connection.send({"type": "subscribed", "channel": channel})


async def handle_unsubscribe(connection: ClientConnection, message: dict[str, Any]) -> None:
    """处理取消订阅请求."""
    channel = message.get("channel")
    if not channel:
        await connection.send({"type": "error", "code": "MISSING_CHANNEL", "message": "缺少频道参数"})
        return

    await manager.unsubscribe(connection.client_id, channel)
    await connection.send({"type": "unsubscribed", "channel": channel})


async def handle_command(connection: ClientConnection, message: dict[str, Any]) -> None:
    """处理任务控制指令."""
    action = message.get("action")
    task_id = message.get("task_id")

    if not action or not task_id:
        await connection.send({"type": "error", "code": "MISSING_PARAMS", "message": "缺少action或task_id参数"})
        return

    runtime = _task_runtimes.get(task_id)
    if not runtime:
        await connection.send({"type": "error", "code": "TASK_NOT_FOUND", "message": f"任务不存在: {task_id}"})
        return

    action_handlers = {
        "pause_task": runtime.pause,
        "resume_task": runtime.resume,
        "cancel_task": runtime.cancel,
    }

    handler = action_handlers.get(action)
    if handler:
        await handler()
        await connection.send(
            {
                "type": "command_response",
                "action": action,
                "task_id": task_id,
                "status": runtime.status,
            }
        )
    else:
        await connection.send({"type": "error", "code": "UNKNOWN_ACTION", "message": f"未知操作: {action}"})


async def handle_start_analyze(connection: ClientConnection, message: dict[str, Any]) -> None:
    """处理启动分析请求."""
    url = message.get("url")
    prompt = str(message.get("prompt") or "").strip()[:2000]
    existing_template_yaml = str(message.get("current_template_yaml") or "")[:100_000]
    request_id = str(message.get("request_id") or uuid.uuid4())
    try:
        viewport_width = max(320, min(int(message.get("viewport_width") or 1440), 3840))
    except (TypeError, ValueError):
        viewport_width = 1440
    if not url:
        await connection.send({"type": "analyze_error", "request_id": request_id, "data": {"code": "MISSING_URL", "message": "缺少url参数"}})
        return

    try:
        validate_target_url(url)
    except Exception as e:
        await connection.send({"type": "analyze_error", "request_id": request_id, "data": {"code": "INVALID_URL", "message": str(e)}})
        return

    if connection.analyze_task and not connection.analyze_task.done():
        connection.analyze_task.cancel()

    connection.analyze_task = asyncio.create_task(
        _stream_analyze_results(
            connection,
            url,
            prompt,
            request_id,
            viewport_width,
            existing_template_yaml,
        )
    )
    connection.active_tasks.add("analyze")
    await connection.send({"type": "analyze_started", "request_id": request_id, "data": {"url": url}})


async def handle_cancel_analyze(connection: ClientConnection, message: dict[str, Any]) -> None:
    """取消当前连接正在执行的分析任务."""
    request_id = str(message.get("request_id") or "")
    task = connection.analyze_task
    if task and not task.done():
        task.cancel()
        await connection.send({"type": "analyze_cancelled", "request_id": request_id, "data": {}})


async def _stream_analyze_results(
    connection: ClientConnection,
    url: str,
    prompt: str,
    request_id: str,
    viewport_width: int,
    existing_template_yaml: str = "",
) -> None:
    """流式发送分析结果到客户端."""
    try:
        async for event_type, data in _analyze_events(
            url,
            prompt,
            viewport_width,
            existing_template_yaml,
        ):
            await connection.send(
                {
                    "type": f"analyze_{event_type}",
                    "request_id": request_id,
                    "data": data,
                }
            )
    except asyncio.CancelledError:
        logger.info("Analyze stream cancelled for %s", url)
    except Exception as e:
        logger.exception("Analyze stream error for %s", url)
        await connection.send({"type": "analyze_error", "request_id": request_id, "data": {"code": "ANALYZE_ERROR", "message": str(e)}})
    finally:
        connection.active_tasks.discard("analyze")
        if connection.analyze_task is asyncio.current_task():
            connection.analyze_task = None


async def handle_start_dry_run(connection: ClientConnection, message: dict[str, Any]) -> None:
    """处理试跑请求."""
    template_id = message.get("template_id")
    limit = message.get("limit", 20)

    if not template_id:
        await connection.send({"type": "error", "code": "MISSING_TEMPLATE_ID", "message": "缺少template_id参数"})
        return

    limit = max(1, min(limit, scope_limit("max_dry_run_limit", 100)))

    dry_run_task = asyncio.create_task(_stream_dry_run_results(connection, template_id, limit))
    connection.active_tasks.add("dry_run")


async def _stream_dry_run_results(connection: ClientConnection, template_id: str, limit: int) -> None:
    """流式发送试跑结果."""
    try:
        await connection.send(
            {
                "type": "dry_run_progress",
                "data": {"stage": "initializing", "progress": 0, "message": "正在初始化试跑环境..."},
            }
        )
        await asyncio.sleep(0.5)

        await connection.send(
            {
                "type": "dry_run_progress",
                "data": {"stage": "fetching", "progress": 30, "message": "正在获取样本数据..."},
            }
        )
        await asyncio.sleep(0.8)

        await connection.send(
            {
                "type": "dry_run_progress",
                "data": {"stage": "parsing", "progress": 60, "message": "正在解析数据结构..."},
            }
        )
        await asyncio.sleep(0.6)

        await connection.send(
            {
                "type": "dry_run_progress",
                "data": {"stage": "validating", "progress": 85, "message": "正在验证字段完整性..."},
            }
        )
        await asyncio.sleep(0.4)

        sample_items = [
            {
                "title": f"示例项目 {index + 1}",
                "price": f"{50 + index * 1.5:.2f}",
                "link": f"https://example.com/item/{index + 1}",
                "date": "2026-06-10",
            }
            for index in range(min(limit, 45))
        ]

        await connection.send(
            {
                "type": "dry_run_complete",
                "data": {
                    "template_id": template_id,
                    "total_pages": max(1, (len(sample_items) + 9) // 10),
                    "total_items": len(sample_items),
                    "sample_items": sample_items,
                    "columns": list(sample_items[0].keys()) if sample_items else [],
                    "duration": 2.3,
                    "errors": [],
                },
            }
        )
    except asyncio.CancelledError:
        logger.info("Dry run cancelled for template %s", template_id)
    except Exception as e:
        logger.exception("Dry run error for template %s", template_id)
        await connection.send({"type": "dry_run_error", "data": {"code": "DRY_RUN_ERROR", "message": str(e)}})
    finally:
        connection.active_tasks.discard("dry_run")


async def handle_generate_template(connection: ClientConnection, message: dict[str, Any]) -> None:
    """处理生成模板请求."""
    url = message.get("url")
    if not url:
        await connection.send({"type": "error", "code": "MISSING_URL", "message": "缺少url参数"})
        return

    try:
        validate_target_url(url)
    except Exception as e:
        await connection.send({"type": "error", "code": "INVALID_URL", "message": str(e)})
        return

    template_task = asyncio.create_task(_stream_template_generation(connection, url))
    connection.active_tasks.add("template_generation")


async def _stream_template_generation(connection: ClientConnection, url: str) -> None:
    """流式发送模板生成进度."""
    try:
        from urllib.parse import urlparse

        domain = urlparse(url).hostname or "unknown"
        name = domain.replace(".", "_")
        template_id = f"tpl_{int(datetime.now().timestamp())}"

        await connection.send(
            {
                "type": "template_progress",
                "data": {"stage": "analyzing", "progress": 20, "message": "正在分析页面结构..."},
            }
        )
        await asyncio.sleep(0.5)

        await connection.send(
            {
                "type": "template_progress",
                "data": {"stage": "building", "progress": 60, "message": "正在构建YAML结构..."},
            }
        )
        await asyncio.sleep(0.6)

        fields = [
            {"name": "title", "selector": "h2.title a", "type": "text", "sample": "示例", "required": True},
            {"name": "price", "selector": "span.price", "type": "number", "sample": "99.00", "required": False},
            {"name": "link", "selector": "h2.title a", "type": "url", "sample": "https://...", "required": True},
            {"name": "date", "selector": "time.date", "type": "date", "sample": "2026-06-10", "required": False},
        ]
        pagination = {
            "type": "click",
            "selector": ".pagination .next",
            "maxPages": scope_limit("max_template_pages", 100),
        }

        yaml_content = _build_yaml_template(url, fields, pagination, pagination["maxPages"])

        await connection.send(
            {
                "type": "template_progress",
                "data": {"stage": "finalizing", "progress": 90, "message": "正在生成最终模板..."},
            }
        )
        await asyncio.sleep(0.3)

        await connection.send(
            {
                "type": "template_ready",
                "data": {
                    "template_id": template_id,
                    "name": name,
                    "domain": domain,
                    "yaml": yaml_content,
                    "fields": fields,
                    "pagination": pagination,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            }
        )
    except asyncio.CancelledError:
        logger.info("Template generation cancelled for %s", url)
    except Exception as e:
        logger.exception("Template generation error for %s", url)
        await connection.send({"type": "template_error", "data": {"code": "TEMPLATE_ERROR", "message": str(e)}})
    finally:
        connection.active_tasks.discard("template_generation")


async def handle_generate_adapter(connection: ClientConnection, message: dict[str, Any]) -> None:
    """Generate and stream an adapter from a stored template analysis."""
    url = message.get("url")
    template_id = message.get("template_id")
    request_id = message.get("request_id")
    prompt = str(message.get("prompt") or "").strip()[:2000]
    existing_adapter_code = str(message.get("current_adapter_code") or "")[:200_000]

    if not url or not template_id or not request_id:
        await connection.send(
            {
                "type": "adapter_error",
                "request_id": request_id,
                "data": {"message": "url, template_id and request_id are required"},
            }
        )
        return

    try:
        validate_target_url(url)
    except Exception as e:
        await connection.send(
            {
                "type": "adapter_error",
                "request_id": request_id,
                "data": {"message": str(e)},
            }
        )
        return

    if connection.adapter_task and not connection.adapter_task.done():
        connection.adapter_task.cancel()
    connection.adapter_task = asyncio.create_task(
        _stream_adapter_generation(
            connection,
            template_id,
            request_id,
            prompt,
            existing_adapter_code,
        )
    )
    connection.active_tasks.add("adapter_generation")


async def _stream_adapter_generation(
    connection: ClientConnection,
    template_id: str,
    request_id: str,
    prompt: str = "",
    existing_adapter_code: str = "",
) -> None:
    """Stream real model output, then validate and persist the final adapter."""
    chunk_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def generate() -> dict[str, Any]:
        try:
            return await _generate_adapter_for_template(
                template_id,
                prompt=prompt,
                existing_adapter_code=existing_adapter_code,
                on_chunk=chunk_queue.put_nowait,
            )
        finally:
            chunk_queue.put_nowait(None)

    try:
        await connection.send(
            {
                "type": "adapter_started",
                "request_id": request_id,
                "data": {"templateId": template_id},
            }
        )
        generation_task = asyncio.create_task(generate())
        while True:
            chunk = await chunk_queue.get()
            if chunk is None:
                break
            await connection.send(
                {
                    "type": "adapter_delta",
                    "request_id": request_id,
                    "data": {"content": chunk},
                }
            )

        result = await generation_task

        await connection.send(
            {
                "type": "adapter_ready",
                "request_id": request_id,
                "data": result,
            }
        )
    except asyncio.CancelledError:
        logger.info("Adapter generation cancelled for template %s", template_id)
    except Exception as e:
        logger.exception("Adapter generation error for template %s", template_id)
        await connection.send(
            {
                "type": "adapter_error",
                "request_id": request_id,
                "data": {"message": str(e)},
            }
        )
    finally:
        if connection.adapter_task is asyncio.current_task():
            connection.adapter_task = None
            connection.active_tasks.discard("adapter_generation")
