from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.scheduler.beat_schedule import BeatScheduleRegistry
from app.scheduler.task_repository import TaskConfig, TaskRepository
from app.web.api.routes import ai_collect, scheduler, tasks
from app.web.services import task_runs


@pytest_asyncio.fixture
async def client(monkeypatch):
    monkeypatch.setattr(scheduler, "publish_automation_change", AsyncMock())
    app = FastAPI()
    for router in (scheduler.router, tasks.router, ai_collect.router):
        app.include_router(router, prefix="/api/v1")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_schedules_and_runs_share_existing_tasks(client, monkeypatch):
    rows = [
        {"id": str(uuid4()), "schedule": schedule, "status": "queued"}
        for schedule in (
            {"mode": "recurring", "recurring_mode": "daily", "daily_time": "09:00"},
            {"mode": "interval", "interval_value": 6, "interval_unit": "hour"},
            {"mode": "once"},
        )
    ]
    monkeypatch.setattr(tasks.ai_collect_store, "list_tasks", AsyncMock(return_value=rows))
    schedules = await client.get("/api/v1/scheduler/workspace-tasks")
    runs = await client.get("/api/v1/tasks/runs")
    legacy = await client.get("/api/v1/ai/workspace/tasks")
    assert schedules.status_code == runs.status_code == legacy.status_code == 200
    assert schedules.json()["items"] == rows[:2]
    assert runs.json() == legacy.json() == {"items": rows}


@pytest.mark.asyncio
async def test_schema_is_available_without_ignored_scripts(monkeypatch):
    import app.scheduler.task_repository as repository

    monkeypatch.setattr(repository, "_READY", False)
    pg = SimpleNamespace(connect=AsyncMock(), fetch_one=AsyncMock(return_value={"reg": None}), init_schema=AsyncMock())
    await TaskRepository(pg).ensure_table()
    ddl = pg.init_schema.call_args.args[0][0]
    assert "CREATE TABLE IF NOT EXISTS public.scheduler_tasks" in ddl
    assert "knowledge-rag" in ddl


@pytest.mark.asyncio
async def test_disabling_all_schedules_clears_previous_entries(monkeypatch):
    import app.scheduler.task_repository as repository

    repo = SimpleNamespace(list_enabled=AsyncMock(return_value=[]))
    monkeypatch.setattr(repository, "get_task_repository", lambda: repo)
    registry = BeatScheduleRegistry()
    assert await registry.load_from_db(force=True)
    assert set(registry.build_schedule()) == {"workspace_recurring_dispatch"}


@pytest.mark.asyncio
async def test_partial_schedule_update_preserves_cron_and_arguments(client, monkeypatch):
    current = TaskConfig(task_name="daily", task_path="example.task", cron_hour="9", args=["existing"])
    repo = SimpleNamespace(
        get_by_name=AsyncMock(return_value=current),
        update=AsyncMock(return_value=current),
    )
    monkeypatch.setattr(scheduler, "get_task_repository", lambda: repo)
    response = await client.put("/api/v1/scheduler/tasks/daily", json={"enabled": False})
    assert response.status_code == 200
    changes = repo.update.call_args.args[1]
    assert changes["enabled"] is False
    assert changes["cron_hour"] == "9"
    assert changes["args"] == ["existing"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"task_name": "bad", "task_path": "example.task", "schedule_type": "interval"},
    {"task_name": "bad", "task_path": "example.task", "cron_hour": "99"},
    {"task_name": "bad", "task_path": "example.task", "interval_seconds": -1},
])
async def test_invalid_schedule_is_rejected_before_storage(client, monkeypatch, body):
    get_repo = Mock()
    monkeypatch.setattr(scheduler, "get_task_repository", get_repo)
    response = await client.post("/api/v1/scheduler/tasks", json=body)
    assert response.status_code == 422
    get_repo.assert_not_called()


@pytest.fixture
def run_store(monkeypatch):
    row = {"id": str(uuid4()), "template_name": "example", "parameters": {"page": "1"}, "status": "queued", "control_state": None}

    async def claim(task_id, delivery_id):
        return {**row, "status": "running", "celery_task_id": delivery_id}

    store = SimpleNamespace(
        get_task_control=AsyncMock(return_value=deepcopy(row)),
        start_task=AsyncMock(side_effect=claim),
        restart_task=AsyncMock(side_effect=claim),
        set_active_task_status=AsyncMock(return_value=deepcopy(row)),
    )
    monkeypatch.setattr(task_runs, "ai_collect_store", store)
    return row, store


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["/tasks/runs", "/ai/workspace/tasks"])
async def test_start_dispatches_claimed_delivery(client, monkeypatch, run_store, prefix):
    row, store = run_store
    dispatch = Mock()
    monkeypatch.setattr(task_runs, "dispatch_run", dispatch)
    response = await client.post(f"/api/v1{prefix}/{row['id']}/action", json={"action": "start"})
    assert response.status_code == 200
    dispatched = dispatch.call_args.args[0]
    assert dispatched["status"] == "running"
    assert dispatched["celery_task_id"] == store.start_task.call_args.args[1]
    assert response.json()["celery_task_id"] == dispatched["celery_task_id"]


@pytest.mark.asyncio
async def test_duplicate_start_does_not_dispatch(client, monkeypatch, run_store):
    row, store = run_store
    store.start_task.side_effect = None
    store.start_task.return_value = None
    dispatch = Mock()
    monkeypatch.setattr(task_runs, "dispatch_run", dispatch)
    response = await client.post(f"/api/v1/tasks/runs/{row['id']}/action", json={"action": "start"})
    assert response.status_code == 409
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_failure_marks_only_its_delivery_failed(client, monkeypatch, run_store):
    row, store = run_store
    monkeypatch.setattr(task_runs, "dispatch_run", Mock(side_effect=ConnectionError("broker unavailable")))
    response = await client.post(f"/api/v1/tasks/runs/{row['id']}/action", json={"action": "start"})
    assert response.status_code == 503
    store.set_active_task_status.assert_awaited_once_with(
        row["id"], "running", "failed", expected_celery_task_id=store.start_task.call_args.args[1],
    )


@pytest.mark.asyncio
async def test_canceled_run_cannot_be_resumed(client, monkeypatch, run_store):
    row, store = run_store
    store.get_task_control.return_value["control_state"] = "canceled"
    dispatch = Mock()
    monkeypatch.setattr(task_runs, "dispatch_run", dispatch)
    response = await client.post(f"/api/v1/tasks/runs/{row['id']}/action", json={"action": "resume"})
    assert response.status_code == 409
    store.set_active_task_status.assert_not_awaited()
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_run_id_is_422(client):
    response = await client.post("/api/v1/tasks/runs/not-a-uuid/action", json={"action": "start"})
    assert response.status_code == 422
