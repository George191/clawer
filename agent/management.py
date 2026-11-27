"""Authenticated management for reusable personal agents and model connections."""
import time
import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.model import AgentModel, ModelFailure
from agent.registry import AgentRegistry, ConnectionUpdate, DefinitionUpdate
from app.web.api.dependencies.common import CurrentUser

router = APIRouter(prefix="/agents", tags=["agents"])
registry = AgentRegistry()


class AgentBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=4000)
    expected_revision: int = Field(ge=1)


class AgentBuildDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(max_length=500)
    instructions: str = Field(min_length=1, max_length=12000)


@router.post("/definitions/{ident}/build", response_model=AgentBuildDraft)
async def build_agent(ident: UUID, payload: AgentBuildRequest, user: CurrentUser):
    config, key, snapshot = await registry.resolve(user.id, ident, require_enabled=False, require_owner=True)
    if snapshot["agent_revision"] != payload.expected_revision:
        raise HTTPException(409, "Agent changed. Reload before building.")
    prompt = (
        'Draft an AI agent configuration. Return only JSON with exactly three string keys: '
        '"name" (at most 80 characters), "description" (at most 500), "instructions" (at most 12000). '
        'The agent uses its existing registered executor; do not claim to add tools, files, credentials or services. '
        'Do not perform the requested task; write instructions for it. '
        f'Existing instructions: {json.dumps(snapshot["instructions"])}\n'
        f'Configuration request: {json.dumps(payload.message)}'
    )
    try:
        output = await AgentModel(config, key).generate(prompt, max_tokens=8192)
        return AgentBuildDraft.model_validate_json(output)
    except ModelFailure as exc:
        raise HTTPException(502, exc.code) from exc
    except ValidationError as exc:
        raise HTTPException(502, "Model returned an invalid configuration draft") from exc


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
