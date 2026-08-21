"""Automation control-plane APIs for workflow definitions."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.scheduler.workflow_repository import WorkflowDefinition, WorkflowRepository

router = APIRouter()
VALID_DOMAINS = {"ai-collect", "data-lake", "etl-pipeline", "data-cockpit", "platform"}


class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    product_domain: str
    description: str = ""
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True


class WorkflowUpdate(BaseModel):
    product_domain: str | None = None
    description: str | None = None
    nodes: list[dict[str, Any]] | None = None
    enabled: bool | None = None


def _validate_domain(domain: str) -> None:
    if domain not in VALID_DOMAINS:
        raise HTTPException(status_code=400, detail=f"Invalid product domain: {domain}")


@router.get("/automation/workflows")
async def list_workflows() -> list[dict[str, Any]]:
    return [asdict(item) for item in await WorkflowRepository().list_all()]


@router.post("/automation/workflows", status_code=201)
async def create_workflow(body: WorkflowCreate) -> dict[str, Any]:
    _validate_domain(body.product_domain)
    try:
        created = await WorkflowRepository().create(WorkflowDefinition(**body.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return asdict(created)


@router.put("/automation/workflows/{name}")
async def update_workflow(name: str, body: WorkflowUpdate) -> dict[str, Any]:
    changes = {key: value for key, value in body.model_dump().items() if value is not None}
    if "product_domain" in changes:
        _validate_domain(str(changes["product_domain"]))
    try:
        updated = await WorkflowRepository().update(name, changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return asdict(updated)


@router.delete("/automation/workflows/{name}", status_code=204, response_class=Response)
async def delete_workflow(name: str) -> Response:
    if not await WorkflowRepository().delete(name):
        raise HTTPException(status_code=404, detail="Workflow not found")
    return Response(status_code=204)
