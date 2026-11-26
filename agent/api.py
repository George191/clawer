"""Authenticated collection API and administrator-only encrypted model configuration."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager, suppress
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query

from agent.contracts import CollectionRequest, RunConflict, SettingsUpdate, SettingsView
from agent.model import CollectionModel, ModelFailure
from agent.runtime import CollectionHarness
from agent.store import CollectionStore
from app.web.api.dependencies.common import CurrentActiveSuperuser, CurrentUser

store = CollectionStore()


@asynccontextmanager
async def lifespan(_app):
    # Schema changes belong to Alembic, never to application request/startup paths.
    await store.settings()
    worker = asyncio.create_task(CollectionHarness(store).serve())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


router = APIRouter(prefix="/agents/collection", tags=["collection-agent"], lifespan=lifespan)


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
        await CollectionModel(config, key).generate("Return only the collection readiness YAML: ready: true", max_tokens=4096)
        await store.mark_verified(view.revision)
    except RunConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ModelFailure as exc:
        raise HTTPException(502, exc.code) from exc
    return {"ok": True, "model": config.model, "reasoning_effort": config.reasoning_effort,
            "revision": view.revision, "latency_ms": round((time.monotonic() - started) * 1000)}


@router.post("/runs", status_code=202)
async def create_run(payload: CollectionRequest, user: CurrentUser,
                     idempotency_key: Annotated[str, Header(min_length=1, max_length=128)]):
    try:
        return await store.enqueue(user.id, idempotency_key, payload)
    except RunConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/runs/{run_id}")
async def get_run(run_id: UUID, user: CurrentUser):
    return await store.get(user.id, run_id)


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
