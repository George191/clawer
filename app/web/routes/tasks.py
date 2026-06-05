"""任务管理 API — 数据采集任务的启动、查询与调度。

Endpoints:
    GET  /api/tasks          — 任务列表
    POST /api/tasks/run      — 异步启动采集任务
    POST /api/tasks/schedule — 调度采集任务
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


class TaskStatus(str, Enum):
    """任务状态枚举."""

    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    paused = "paused"


# ── In-memory store (后续可接入持久化存储) ──────────────────────────────────
_tasks_store: dict[str, dict[str, Any]] = {}


class TaskRunRequest(BaseModel):
    """启动采集任务请求体."""

    template: str = Field(..., description="模板名称, 如 google_patent")
    params: dict[str, str] = Field(default_factory=dict, description="模板参数, 如 {'assignee': 'Google'}")


class TaskScheduleRequest(BaseModel):
    """调度采集任务请求体."""

    template: str = Field(..., description="模板名称")
    params: dict[str, str] = Field(default_factory=dict, description="模板参数")
    cron: str | None = Field(default=None, description="cron 表达式, 如 '0 2 * * *'")
    interval_seconds: int | None = Field(default=None, description="固定间隔(秒), 如 86400")
    enabled: bool = Field(default=True, description="调度是否启用")


class TaskInfo(BaseModel):
    """任务信息响应体."""

    id: str
    template: str
    status: TaskStatus
    progress: int = 0
    records: int = 0
    started_at: str | None = None
    duration: float | None = None


@router.get("/tasks", response_model=list[TaskInfo])
async def list_tasks() -> list[dict[str, Any]]:
    """获取任务列表。

    返回所有已创建的任务，包含 id、template、status 等字段。
    """
    return [
        {
            "id": tid,
            "template": t["template"],
            "status": t["status"],
            "progress": t.get("progress", 0),
            "records": t.get("records", 0),
            "started_at": t.get("started_at"),
            "duration": t.get("duration"),
        }
        for tid, t in _tasks_store.items()
    ]


@router.post("/tasks/run")
async def run_task(body: TaskRunRequest) -> dict[str, Any]:
    """异步启动一个采集任务。

    接受模板名称和参数，创建后台任务并立即返回任务 ID。
    """
    tid = str(uuid.uuid4())[:8]
    now = datetime.now(timezone.utc).isoformat()
    task = {
        "id": tid,
        "template": body.template,
        "params": body.params,
        "status": TaskStatus.queued.value,
        "progress": 0,
        "records": 0,
        "started_at": now,
        "duration": None,
    }
    _tasks_store[tid] = task

    # TODO: 调度到 SpiderEngine 异步执行
    # 当前仅标记为 queued，后续模块接入后自动转为 running
    return task


@router.post("/tasks/schedule")
async def schedule_task(body: TaskScheduleRequest) -> dict[str, Any]:
    """创建一个定时调度任务。

    支持 cron 表达式和固定间隔两种调度方式。
    """
    tid = str(uuid.uuid4())[:8]
    schedule = {
        "id": tid,
        "template": body.template,
        "params": body.params,
        "status": TaskStatus.queued.value if body.enabled else TaskStatus.paused.value,
        "schedule": {
            "cron": body.cron,
            "interval_seconds": body.interval_seconds,
            "enabled": body.enabled,
        },
        "progress": 0,
        "records": 0,
        "started_at": None,
        "duration": None,
    }
    _tasks_store[tid] = schedule
    return schedule
