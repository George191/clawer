"""Authenticated management for reusable personal agents and model connections."""
import time
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from agent.model import AgentModel, ModelFailure
from agent.registry import AgentRegistry, ConnectionUpdate, DefinitionUpdate
from app.web.api.dependencies.common import CurrentUser

router = APIRouter(prefix="/agents", tags=["agents"])
registry = AgentRegistry()


@router.get("/catalog")
async def list_agents(user: CurrentUser):
    return await registry.list(user.id)


@router.post("/connections", status_code=201)
async def create_connection(payload: ConnectionUpdate, user: CurrentUser):
    return await registry.save_connection(user.id, payload)


@router.put("/connections/{ident}")
async def update_connection(ident: UUID, payload: ConnectionUpdate, user: CurrentUser):
    return await registry.save_connection(user.id, payload, ident)


@router.post("/definitions", status_code=201)
async def create_agent(payload: DefinitionUpdate, user: CurrentUser):
    return await registry.save_definition(user.id, payload)


@router.put("/definitions/{ident}")
async def update_agent(ident: UUID, payload: DefinitionUpdate, user: CurrentUser):
    return await registry.save_definition(user.id, payload, ident)


@router.delete("/{kind}/{ident}")
async def delete_item(kind: str, ident: UUID, user: CurrentUser, revision: int = Query(ge=1)):
    if kind not in {"connections", "definitions"}:
        raise HTTPException(404, "Unknown resource")
    await registry.delete(user.id, "agents" if kind == "definitions" else kind, ident, revision)
    return {"ok": True}


@router.post("/definitions/{ident}/test")
async def test_agent(ident: UUID, user: CurrentUser):
    config, key, snapshot = await registry.resolve(user.id, ident, require_enabled=False, require_owner=True)
    start = time.monotonic()
    try:
        await AgentModel(config, key).generate("Return only YAML: ready: true", max_tokens=4096)
    except ModelFailure as exc:
        raise HTTPException(502, exc.code) from exc
    await registry.mark_verified(user.id, snapshot)
    return {"ok": True, "latency_ms": round((time.monotonic() - start) * 1000)}
