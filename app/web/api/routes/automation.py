from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.web.services.automation_store import automation_store
from app.web.services.task_events import publish_automation_change
from app.web.api.dependencies.common import CurrentActiveSuperuser

router = APIRouter(prefix="/automation", tags=["automation"])


@router.get("/workers")
async def list_workers(_user: CurrentActiveSuperuser) -> dict[str, Any]:
    from app.scheduler.celery_app import app

    try:
        stats, active, queues = await asyncio.gather(*(
            asyncio.to_thread(getattr(app.control.inspect(timeout=2), command))
            for command in ("stats", "active", "active_queues")
        ))
    except Exception as exc:
        raise HTTPException(503, detail="Unable to inspect execution workers") from exc
    if not stats:
        raise HTTPException(503, detail="No workers responded to the inspection request")
    return {"items": [{
        "name": name, "pid": value.get("pid"), "uptime": value.get("uptime"),
        "concurrency": value.get("pool", {}).get("max-concurrency"),
        "processed": sum(value.get("total", {}).values()),
        "active": None if active is None or name not in active else [
            {"id": task.get("id"), "name": task.get("name"), "started_at": task.get("time_start")}
            for task in active[name]],
        "queues": None if queues is None or name not in queues else [queue["name"] for queue in queues[name]],
    } for name, value in sorted(stats.items())]}


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    product_domain: str
    description: str = ""
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True


@router.get("/workflows")
async def list_workflows() -> list[dict[str, Any]]:
    return await automation_store.list_workflows()


@router.post("/workflows")
async def create_workflow(payload: WorkflowRequest) -> dict[str, Any]:
    try:
        result = await automation_store.save_workflow(payload.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await publish_automation_change("workflows")
    return result


@router.put("/workflows/{name}")
async def update_workflow(name: str, payload: WorkflowRequest) -> dict[str, Any]:
    try:
        result = await automation_store.save_workflow(payload.model_dump(), name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await publish_automation_change("workflows")
    return result


@router.delete("/workflows/{name}")
async def delete_workflow(name: str) -> dict[str, bool]:
    if not await automation_store.delete_workflow(name):
        raise HTTPException(status_code=404, detail="工作流不存在")
    await publish_automation_change("workflows")
    return {"deleted": True}
