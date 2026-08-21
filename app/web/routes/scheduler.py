"""调度任务配置 API — 数据驱动的任务调度维护。

Endpoints:
    GET    /api/scheduler/tasks              — 列出所有任务配置
    GET    /api/scheduler/tasks/{name}       — 查询单个任务配置
    POST   /api/scheduler/tasks              — 创建任务配置
    PUT    /api/scheduler/tasks/{name}       — 更新任务配置
    DELETE /api/scheduler/tasks/{name}       — 删除任务配置
    POST   /api/scheduler/tasks/{name}/toggle — 启用/禁用任务
    POST   /api/scheduler/reload             — 从数据库重新加载 Beat 调度配置
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.logger import get_logger
from app.scheduler.task_repository import TaskConfig, TaskRepository

logger = get_logger(__name__)

router = APIRouter()
VALID_PRODUCT_DOMAINS = {"ai-collect", "data-lake", "etl-pipeline", "data-cockpit", "platform"}


# ══════════════════════════════════════════════════════════════════════
#  请求/响应模型
# ══════════════════════════════════════════════════════════════════════

class TaskConfigCreate(BaseModel):
    """创建任务配置请求体。"""

    task_name: str = Field(..., description="任务名（唯一标识）", examples=["google_patent_daily"])
    task_path: str = Field(..., description="Celery task 路径", examples=["app.scheduler.tasks.google_patent.crawl_daily"])
    product_domain: str = Field("platform", description="所属产品域")
    description: str | None = Field(None, description="任务描述")

    schedule_type: str = Field("crontab", description="调度类型: crontab / interval")

    # crontab 字段
    cron_minute: str = Field("*", description="cron 分钟")
    cron_hour: str = Field("*", description="cron 小时")
    cron_day_of_week: str = Field("*", description="cron 星期")
    cron_day_of_month: str = Field("*", description="cron 日")
    cron_month_of_year: str = Field("*", description="cron 月")

    # interval 字段
    interval_seconds: int | None = Field(None, description="间隔秒数（schedule_type=interval 时必填）")

    # 任务参数
    args: list[Any] = Field(default_factory=list, description="位置参数")
    kwargs: dict[str, Any] = Field(default_factory=dict, description="关键字参数")
    options: dict[str, Any] = Field(default_factory=dict, description="队列、过期等选项")

    enabled: bool = Field(True, description="是否启用")


class TaskConfigUpdate(BaseModel):
    """更新任务配置请求体（部分更新）。"""

    task_path: str | None = None
    product_domain: str | None = None
    description: str | None = None

    schedule_type: str | None = None
    cron_minute: str | None = None
    cron_hour: str | None = None
    cron_day_of_week: str | None = None
    cron_day_of_month: str | None = None
    cron_month_of_year: str | None = None
    interval_seconds: int | None = None

    args: list[Any] | None = None
    kwargs: dict[str, Any] | None = None
    options: dict[str, Any] | None = None

    enabled: bool | None = None


class TaskConfigResponse(BaseModel):
    """任务配置响应体。"""

    id: int | None = None
    task_name: str
    task_path: str
    product_domain: str
    description: str | None = None

    schedule_type: str
    cron_minute: str
    cron_hour: str
    cron_day_of_week: str
    cron_day_of_month: str
    cron_month_of_year: str
    interval_seconds: int | None = None

    args: list[Any]
    kwargs: dict[str, Any]
    options: dict[str, Any]

    enabled: bool
    updated_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ToggleRequest(BaseModel):
    """启用/禁用请求体。"""

    enabled: bool = Field(..., description="目标状态")


class ReloadResponse(BaseModel):
    """重新加载响应体。"""

    loaded: bool
    task_count: int
    message: str


# ══════════════════════════════════════════════════════════════════════
#  辅助函数
# ══════════════════════════════════════════════════════════════════════

def _config_to_response(cfg: TaskConfig) -> TaskConfigResponse:
    return TaskConfigResponse(
        id=cfg.id,
        task_name=cfg.task_name,
        task_path=cfg.task_path,
        product_domain=cfg.product_domain,
        description=cfg.description,
        schedule_type=cfg.schedule_type,
        cron_minute=cfg.cron_minute,
        cron_hour=cfg.cron_hour,
        cron_day_of_week=cfg.cron_day_of_week,
        cron_day_of_month=cfg.cron_day_of_month,
        cron_month_of_year=cfg.cron_month_of_year,
        interval_seconds=cfg.interval_seconds,
        args=cfg.args,
        kwargs=cfg.kwargs,
        options=cfg.options,
        enabled=cfg.enabled,
        updated_by=cfg.updated_by,
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


def _get_repo() -> TaskRepository:
    return TaskRepository()


# ══════════════════════════════════════════════════════════════════════
#  API 端点
# ══════════════════════════════════════════════════════════════════════

@router.get("/scheduler/tasks", response_model=list[TaskConfigResponse])
async def list_tasks() -> list[dict[str, Any]]:
    """列出所有任务配置。"""
    repo = _get_repo()
    configs = await repo.list_all()
    return [c.model_dump() for c in (_config_to_response(c) for c in configs)]


@router.get("/scheduler/tasks/{task_name}", response_model=TaskConfigResponse)
async def get_task(task_name: str) -> dict[str, Any]:
    """查询单个任务配置。"""
    repo = _get_repo()
    cfg = await repo.get_by_name(task_name)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_name}")
    return _config_to_response(cfg).model_dump()


@router.post("/scheduler/tasks", response_model=TaskConfigResponse, status_code=201)
async def create_task(req: TaskConfigCreate) -> dict[str, Any]:
    """创建任务配置。"""
    repo = _get_repo()

    # 校验调度类型
    if req.schedule_type == "interval" and (not req.interval_seconds or req.interval_seconds <= 0):
        raise HTTPException(status_code=400, detail="interval 调度类型必须指定 interval_seconds > 0")
    if req.product_domain not in VALID_PRODUCT_DOMAINS:
        raise HTTPException(status_code=400, detail="无效的产品域")

    config = TaskConfig(
        task_name=req.task_name,
        task_path=req.task_path,
        product_domain=req.product_domain,
        description=req.description,
        schedule_type=req.schedule_type,
        cron_minute=req.cron_minute,
        cron_hour=req.cron_hour,
        cron_day_of_week=req.cron_day_of_week,
        cron_day_of_month=req.cron_day_of_month,
        cron_month_of_year=req.cron_month_of_year,
        interval_seconds=req.interval_seconds,
        args=req.args,
        kwargs=req.kwargs,
        options=req.options,
        enabled=req.enabled,
    )

    try:
        created = await repo.create(config, updated_by="api")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _config_to_response(created).model_dump()


@router.put("/scheduler/tasks/{task_name}", response_model=TaskConfigResponse)
async def update_task(task_name: str, req: TaskConfigUpdate) -> dict[str, Any]:
    """更新任务配置（部分更新）。"""
    repo = _get_repo()

    # 过滤 None 字段
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="没有可更新的字段")
    if updates.get("product_domain") not in (None, *VALID_PRODUCT_DOMAINS):
        raise HTTPException(status_code=400, detail="无效的产品域")

    try:
        updated = await repo.update(task_name, updates, updated_by="api")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if updated is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_name}")
    return _config_to_response(updated).model_dump()


@router.delete("/scheduler/tasks/{task_name}", status_code=204, response_class=Response)
async def delete_task(task_name: str) -> Response:
    """删除任务配置。"""
    repo = _get_repo()
    existing = await repo.get_by_name(task_name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_name}")
    await repo.delete(task_name, updated_by="api")
    return Response(status_code=204)


@router.post("/scheduler/tasks/{task_name}/toggle", response_model=TaskConfigResponse)
async def toggle_task(task_name: str, req: ToggleRequest) -> dict[str, Any]:
    """启用/禁用任务。"""
    repo = _get_repo()
    updated = await repo.toggle(task_name, req.enabled, updated_by="api")
    if updated is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_name}")
    return _config_to_response(updated).model_dump()


@router.post("/scheduler/reload", response_model=ReloadResponse)
async def reload_schedule() -> dict[str, Any]:
    """从数据库重新加载 Beat 调度配置。

    修改任务配置后调用此接口，使 Beat 调度器立即生效。
    """
    from app.scheduler.beat_schedule import BeatScheduleRegistry

    loaded = await BeatScheduleRegistry.instance().load_from_db(force=True)
    count = len(BeatScheduleRegistry.instance())

    if loaded:
        return ReloadResponse(
            loaded=True,
            task_count=count,
            message=f"成功从数据库加载 {count} 个任务配置",
        ).model_dump()
    return ReloadResponse(
        loaded=False,
        task_count=count,
        message="数据库加载失败，使用内存默认配置",
    ).model_dump()
