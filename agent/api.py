"""Global agent runs and administrator-only system model configuration."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager, suppress
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import ValidationError

from agent.contracts import AgentRunRequest, RunConflict, SettingsUpdate, SettingsView
from agent.model import AgentModel, ModelFailure
from agent.runtime import AgentWorker
from agent.store import AgentStore
from app.web.api.dependencies.common import CurrentActiveSuperuser, CurrentUser

store = AgentStore()


@asynccontextmanager
async def lifespan(_app):
    # Schema changes belong to Alembic, never to application request/startup paths.
    await store.settings()
    worker = asyncio.create_task(AgentWorker(store).serve())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


router = APIRouter(prefix="/agents", tags=["agents"], lifespan=lifespan)


@router.get("/settings", response_model=SettingsView)
async def read_settings(_user: CurrentActiveSuperuser):
    return await store.settings()


@router.put("/settings", response_model=SettingsView)
async def update_settings(payload: SettingsUpdate, user: CurrentActiveSuperuser):
    try:
        return await store.save_settings(payload, user.id)
    except RunConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/settings/test")
async def test_settings(_user: CurrentActiveSuperuser):
    view = await store.settings()
    config, key = await store.runtime_settings(view.revision, require_enabled=False)
    started = time.monotonic()
    try:
        await AgentModel(config, key).generate("Return only YAML: ready: true", max_tokens=4096)
        await store.mark_verified(view.revision)
    except RunConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ModelFailure as exc:
        raise HTTPException(502, exc.code) from exc
    return {"ok": True, "model": config.model, "reasoning_effort": config.reasoning_effort,
            "revision": view.revision, "latency_ms": round((time.monotonic() - started) * 1000)}


@router.post("/runs", status_code=202)
async def create_run(payload: AgentRunRequest, user: CurrentUser,
                     idempotency_key: Annotated[str, Header(min_length=1, max_length=128)]):
    try:
        return await store.enqueue(user.id, idempotency_key, payload)
    except RunConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(422, "Input does not match the agent executor contract") from exc


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, user: CurrentUser):
    return await store.get(user.id, run_id)


@router.get("/runs")
async def list_runs(user: CurrentUser, product_domain: str | None = Query(default=None, min_length=1, max_length=80),
                    limit: int = Query(default=100, ge=1, le=100)):
    return {"items": await store.list(user.id, product_domain, limit)}


@router.get("/runs/{run_id}/events")
async def get_events(run_id: UUID, user: CurrentUser,
                     after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=100)):
    return {"items": await store.events(user.id, run_id, after, limit)}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: UUID, user: CurrentUser):
    return await store.control(user.id, run_id, "cancel")


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: UUID, user: CurrentUser):
    try:
        return await store.control(user.id, run_id, "resume")
    except RunConflict as exc:
        raise HTTPException(409, str(exc)) from exc
