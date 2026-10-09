from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.web.services.ai_collect_store import ai_collect_store
from app.web.api.models.tasks import TaskAction, WorkspaceTaskRequest
from app.web.services.task_runs import delete_run, run_action

router = APIRouter(prefix="/tasks", tags=["tasks"])


class RunTaskRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    taskId: UUID


@router.get("/runs")
async def list_runs() -> dict[str, Any]:
    return {"items": await ai_collect_store.list_tasks()}


@router.get("/runs/{task_id}")
async def get_run(task_id: UUID) -> dict[str, Any]:
    task = await ai_collect_store.get_task(str(task_id))
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.post("/runs", status_code=201)
async def create_run(payload: WorkspaceTaskRequest) -> dict[str, Any]:
    template = await ai_collect_store.get_template_definition(payload.template_name, payload.template_version)
    if template is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return await ai_collect_store.create_task(payload.model_dump())


@router.post("/runs/{task_id}/action")
async def act_on_run(task_id: UUID, payload: TaskAction) -> dict[str, Any]:
    return await run_action(str(task_id), payload.action)


@router.delete("/runs/{task_id}")
async def remove_run(task_id: UUID) -> dict[str, bool]:
    return await delete_run(str(task_id))


@router.get("")
async def list_tasks() -> list[dict[str, Any]]:
    return await ai_collect_store.list_tasks()


@router.post("/run")
async def run_task(payload: RunTaskRequest) -> dict[str, Any]:
    return await run_action(str(payload.taskId), "start")


@router.delete("/{task_id}")
async def delete_task(task_id: UUID) -> dict[str, bool]:
    return await delete_run(str(task_id))
