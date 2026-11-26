"""Authenticated management for reusable personal agents and model connections."""
import time
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from agent.model import CollectionModel, ModelFailure
from agent.registry import AgentRegistry, ConnectionUpdate, DefinitionUpdate
from app.web.api.dependencies.common import CurrentUser

router = APIRouter(prefix="/agent-management", tags=["agent-management"])
registry = AgentRegistry()


@router.get("")
async def list_agents(user: CurrentUser):
    return await registry.list(user.id)


@router.post("/connections", status_code=201)
async def create_connection(payload: ConnectionUpdate, user: CurrentUser):
    return await registry.save_connection(user.id, payload)


@router.put("/connections/{ident}")
async def update_connection(ident: UUID, payload: ConnectionUpdate, user: CurrentUser):
    return await registry.save_connection(user.id, payload, ident)


@router.post("/agents", status_code=201)
async def create_agent(payload: DefinitionUpdate, user: CurrentUser):
    return await registry.save_definition(user.id, payload)


@router.put("/agents/{ident}")
async def update_agent(ident: UUID, payload: DefinitionUpdate, user: CurrentUser):
    return await registry.save_definition(user.id, payload, ident)


@router.delete("/{kind}/{ident}")
async def delete_item(kind: str, ident: UUID, user: CurrentUser, revision: int = Query(ge=1)):
    if kind not in {"connections", "agents"}:
        raise HTTPException(404, "Unknown resource")
    await registry.delete(user.id, kind, ident, revision)
    return {"ok": True}


@router.post("/agents/{ident}/test")
async def test_agent(ident: UUID, user: CurrentUser):
    config, key, snapshot = await registry.resolve(user.id, ident, require_enabled=False)
    start = time.monotonic()
    try:
        await CollectionModel(config, key).generate("Return only YAML: ready: true", max_tokens=4096)
    except ModelFailure as exc:
        raise HTTPException(502, exc.code) from exc
    await registry.mark_verified(user.id, snapshot)
    return {"ok": True, "latency_ms": round((time.monotonic() - start) * 1000)}
