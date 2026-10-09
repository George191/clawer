from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.scheduler.beat_schedule import BeatScheduleRegistry
from app.scheduler.task_repository import TaskConfig, get_task_repository
from app.web.services.ai_collect_store import ai_collect_store
from app.web.services.task_events import publish_automation_change

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_name: str = Field(min_length=1, max_length=255)
    task_path: str = Field(min_length=1, max_length=500)
    product_domain: Literal[
        "ai-collect", "data-lake", "etl-pipeline", "data-cockpit",
        "knowledge-graph", "knowledge-rag", "platform",
    ] = "platform"
    description: str | None = None
    schedule_type: Literal["crontab", "interval"] = "crontab"
    cron_minute: str = "*"
    cron_hour: str = "*"
    cron_day_of_week: str = "*"
    cron_day_of_month: str = "*"
    cron_month_of_year: str = "*"
    interval_seconds: int | None = Field(default=None, gt=0)
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.schedule_type == "interval" and self.interval_seconds is None:
            raise ValueError("interval_seconds is required for interval schedules")
        TaskConfig(**self.model_dump()).to_beat_entry()
        return self


class ToggleRequest(BaseModel):
    enabled: bool


@router.get("/tasks")
async def list_schedules() -> list[dict[str, Any]]:
    return [asdict(config) for config in await get_task_repository().list_all()]


@router.get("/workspace-tasks")
async def list_workspace_schedules() -> dict[str, Any]:
    tasks = await ai_collect_store.list_tasks()
    items = []
    for task in tasks:
        schedule = task.get("schedule") or {}
        mode = schedule.get("recurring_mode") if schedule.get("mode") == "recurring" else schedule.get("mode")
        if mode in {"daily", "interval"}:
            items.append(task)
    return {"items": items}


@router.post("/tasks", status_code=201)
async def create_schedule(payload: ScheduleRequest) -> dict[str, Any]:
    try:
        result = await get_task_repository().create(TaskConfig(**payload.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await publish_automation_change("schedules")
    return asdict(result)


@router.put("/tasks/{task_name}")
async def update_schedule(task_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    repo = get_task_repository()
    current = await repo.get_by_name(task_name)
    if current is None:
        raise HTTPException(status_code=404, detail="调度任务不存在")
    values = {key: value for key, value in asdict(current).items() if key in ScheduleRequest.model_fields}
    try:
        validated = ScheduleRequest.model_validate({**values, **payload})
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_context=False)) from exc
    if validated.task_name != task_name:
        raise HTTPException(status_code=422, detail="不能修改调度任务名称")
    result = await repo.update(task_name, validated.model_dump(exclude={"task_name"}))
    if result is None:
        raise HTTPException(status_code=404, detail="调度任务不存在")
    await publish_automation_change("schedules")
    return asdict(result)


@router.post("/tasks/{task_name}/toggle")
async def toggle_schedule(task_name: str, payload: ToggleRequest) -> dict[str, Any]:
    result = await get_task_repository().toggle(task_name, payload.enabled)
    if result is None:
        raise HTTPException(status_code=404, detail="调度任务不存在")
    await publish_automation_change("schedules")
    return asdict(result)


@router.delete("/tasks/{task_name}")
async def delete_schedule(task_name: str) -> dict[str, bool]:
    repo = get_task_repository()
    if await repo.get_by_name(task_name) is None:
        raise HTTPException(status_code=404, detail="调度任务不存在")
    await repo.delete(task_name)
    await publish_automation_change("schedules")
    return {"deleted": True}


@router.post("/reload")
async def reload_schedules() -> dict[str, Any]:
    registry = BeatScheduleRegistry.instance()
    loaded = await registry.load_from_db(force=True)
    return {
        "loaded": loaded,
        "task_count": len(registry),
        "restart_required": loaded,
        "message": "配置已校验；独立运行的 Celery Beat 需重启后生效" if loaded else "调度配置加载失败",
    }
