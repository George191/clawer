import asyncio
import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.scheduler.task_repository import TaskConfig
from app.web.api.routes import automation, scheduler, task_socket
from app.web.main import app
from app.web.services import task_events


@pytest.fixture(autouse=True)
def isolated_connections(monkeypatch):
    monkeypatch.setattr(task_socket, "_connections", set())
    monkeypatch.setattr(task_socket, "_stream_available", False)


@pytest.fixture
def client(monkeypatch):
    async def idle_listener():
        await asyncio.Event().wait()

    listener = AsyncMock(side_effect=idle_listener)
    connection = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(task_socket, "_listen_for_task_events", listener)
    monkeypatch.setattr(task_socket, "_task_event_connection", connection)
    monkeypatch.setattr(task_socket.ai_collect_store, "list_tasks", AsyncMock(return_value=[]))
    with TestClient(app) as client:
        yield client
    listener.assert_awaited_once()
    connection.close.assert_awaited_once()
    assert not task_socket._connections


def test_task_snapshot_updates_and_logs_use_existing_panel_protocol(client, monkeypatch):
    task_id = str(uuid4())
    channel = f"task:{task_id}"
    task = {"id": task_id, "status": "running", "progress": 12, "logs": []}
    get_task = AsyncMock(return_value=task)
    monkeypatch.setattr(task_socket.ai_collect_store, "get_task", get_task)
    with client.websocket_connect("/ws") as detail, client.websocket_connect("/ws") as listing:
        for websocket in (detail, listing):
            assert websocket.receive_json() == {"type": "task_stream_status", "data": {"available": False}}
            websocket.send_json({"type": "subscribe", "channel": "tasks"})
            assert websocket.receive_json() == {"type": "tasks_snapshot", "data": []}
            assert websocket.receive_json() == {"type": "subscribed", "channel": "tasks"}
        detail.send_json({"type": "subscribe", "channel": channel})
        assert detail.receive_json() == {"type": "task_detail", "task_id": task_id, "data": task}
        assert detail.receive_json() == {"type": "subscribed", "channel": channel}

        # Subscribing to both channels must still deliver each update only once.
        client.portal.call(task_socket._forward_task_event, {"task_id": task_id})
        for websocket in (detail, listing):
            assert websocket.receive_json()["data"] == task
        log_event = {"type": "task_log", "task_id": task_id, "data": {"message": "Collected 12 records"}}
        client.portal.call(task_socket._forward_task_event, log_event)
        assert detail.receive_json() == log_event
        listing.send_json({"type": "unsubscribe", "channel": "tasks"})
        # List-only subscribers never receive task log messages.
        assert listing.receive_json() == {"type": "unsubscribed", "channel": "tasks"}

        detail.send_json({"type": "unsubscribe", "channel": channel})
        assert detail.receive_json() == {"type": "unsubscribed", "channel": channel}
        client.portal.call(task_socket._forward_task_event, log_event)
        detail.send_json({"type": "subscribe", "channel": "tasks"})
        assert detail.receive_json() == {"type": "tasks_snapshot", "data": []}
        assert detail.receive_json() == {"type": "subscribed", "channel": "tasks"}

        get_task.return_value = None
        client.portal.call(task_socket._forward_task_event, {"task_id": task_id})
        assert detail.receive_json() == {"type": "task_deleted", "task_id": task_id}
    assert not task_socket._connections


@pytest.mark.parametrize("message", [
    {}, [], {"type": "subscribe", "channel": "task:invalid"},
    {"type": "subscribe", "channel": "unrelated"},
    {"type": "delete", "channel": "tasks"},
])
def test_bad_subscriptions_do_not_close_connection(client, message):
    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json(message)
        assert websocket.receive_json()["code"] == "INVALID_SUBSCRIPTION"
        websocket.send_json({"type": "subscribe", "channel": "tasks"})
        assert websocket.receive_json() == {"type": "tasks_snapshot", "data": []}
        assert websocket.receive_json() == {"type": "subscribed", "channel": "tasks"}


def test_stream_availability_notifies_existing_and_new_clients(client):
    with client.websocket_connect("/ws") as first:
        assert first.receive_json()["data"]["available"] is False
        client.portal.call(task_socket._set_stream_available, True)
        assert first.receive_json()["data"]["available"] is True
        with client.websocket_connect("/ws") as second:
            assert second.receive_json()["data"]["available"] is True
            client.portal.call(task_socket._set_stream_available, False)
            assert first.receive_json()["data"]["available"] is False
            assert second.receive_json()["data"]["available"] is False


def test_redis_recovery_resends_current_list_and_selected_task(client, monkeypatch):
    task_id = str(uuid4())
    task = {"id": task_id, "status": "running", "progress": 20, "logs": []}
    get_task = AsyncMock(return_value=task)
    list_tasks = AsyncMock(return_value=[task])
    monkeypatch.setattr(task_socket.ai_collect_store, "get_task", get_task)
    monkeypatch.setattr(task_socket.ai_collect_store, "list_tasks", list_tasks)
    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        for channel in ("tasks", f"task:{task_id}"):
            websocket.send_json({"type": "subscribe", "channel": channel})
            websocket.receive_json()
            assert websocket.receive_json()["type"] == "subscribed"
        updated = {**task, "status": "completed", "progress": 100, "logs": [{"message": "Finished"}]}
        get_task.return_value = updated
        list_tasks.return_value = [updated]
        client.portal.call(task_socket._set_stream_available, True)
        assert websocket.receive_json()["data"]["available"] is True
        snapshots = [websocket.receive_json(), websocket.receive_json()]
        by_type = {snapshot["type"]: snapshot["data"] for snapshot in snapshots}
        assert by_type == {"tasks_snapshot": [updated], "task_detail": updated}


@pytest.mark.asyncio
async def test_failed_client_does_not_block_other_subscribers():
    failed = task_socket.ClientConnection(SimpleNamespace(send_json=AsyncMock(side_effect=RuntimeError()), close=AsyncMock()))
    healthy = task_socket.ClientConnection(SimpleNamespace(send_json=AsyncMock(), close=AsyncMock()))
    for connection in (failed, healthy):
        connection.subscriptions.add("tasks")
        task_socket._connections.add(connection)
    event = {"type": "task_deleted", "task_id": str(uuid4())}
    await task_socket._broadcast(event, {"tasks"})
    healthy.websocket.send_json.assert_awaited_once_with(event)
    failed.websocket.close.assert_awaited_once_with(code=1011)
    assert task_socket._connections == {healthy}


@pytest.mark.asyncio
async def test_redis_listener_recovers_and_cleans_up(monkeypatch):
    messages = asyncio.Queue()
    pubsub = SimpleNamespace(subscribe=AsyncMock(), aclose=AsyncMock())

    async def get_message(**_kwargs):
        message = await messages.get()
        if isinstance(message, Exception):
            raise message
        return message

    pubsub.get_message = get_message
    redis = SimpleNamespace(pubsub=Mock(return_value=pubsub))
    connection = SimpleNamespace(ensure_connected=AsyncMock(return_value=redis), mark_unavailable=Mock())
    monkeypatch.setattr(task_socket, "_task_event_connection", connection)
    forwarded = AsyncMock()
    monkeypatch.setattr(task_socket, "_forward_task_event", forwarded)
    statuses = asyncio.Queue()
    monkeypatch.setattr(task_socket, "_set_stream_available", statuses.put)
    listener = asyncio.create_task(task_socket._listen_for_task_events())
    try:
        assert await asyncio.wait_for(statuses.get(), timeout=3) is True
        await messages.put({"data": "invalid json"})
        event = {"task_id": str(uuid4())}
        await messages.put({"data": json.dumps(event)})
        await messages.put(ConnectionError("Redis disconnected"))
        assert await asyncio.wait_for(statuses.get(), timeout=3) is False
        forwarded.assert_awaited_once_with(event)
        connection.mark_unavailable.assert_called_once()
        assert await asyncio.wait_for(statuses.get(), timeout=3) is True
        assert pubsub.subscribe.await_count == 2
        pubsub.subscribe.assert_awaited_with(task_socket.TASK_EVENT_CHANNEL)
    finally:
        listener.cancel()
        with pytest.raises(asyncio.CancelledError):
            await listener
    assert pubsub.aclose.await_count == 2


@pytest.mark.parametrize("channel", ["workflows", "schedules", "tasks"])
def test_subscription_and_reconnect_recover_latest_snapshot(client, monkeypatch, channel):
    rows = []
    loader = AsyncMock(side_effect=lambda: list(rows))
    monkeypatch.setattr(task_socket.automation_store, "list_workflows", loader)
    monkeypatch.setattr(task_socket.ai_collect_store, "list_tasks", loader)
    monkeypatch.setattr(task_socket, "get_task_repository", lambda: SimpleNamespace(list_all=loader))
    row = TaskConfig(task_name="daily", task_path="example.task") if channel == "schedules" else {"id": str(uuid4())}
    expected = asdict(row) if channel == "schedules" else row

    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "subscribe", "channel": channel})
        assert websocket.receive_json() == {"type": f"{channel}_snapshot", "data": []}
        assert websocket.receive_json() == {"type": "subscribed", "channel": channel}
        # The browser stays connected while Redis recovers: missed changes need a snapshot.
        rows.append(row)
        client.portal.call(task_socket._set_stream_available, True)
        assert websocket.receive_json() == {"type": "task_stream_status", "data": {"available": True}}
        assert websocket.receive_json() == {"type": f"{channel}_snapshot", "data": [expected]}

    rows.clear()
    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "subscribe", "channel": channel})
        assert websocket.receive_json() == {"type": f"{channel}_snapshot", "data": []}
        assert websocket.receive_json() == {"type": "subscribed", "channel": channel}


@pytest.mark.parametrize("channel,operation", [
    ("workflows", "create"), ("workflows", "update"), ("workflows", "delete"),
    ("schedules", "create"), ("schedules", "update"), ("schedules", "delete"),
    ("schedules", "toggle"),
])
def test_mutations_publish_to_subscribers_without_refresh(client, monkeypatch, channel, operation):
    workflow = {"name": "daily", "product_domain": "ai-collect", "enabled": True}
    schedule = TaskConfig(task_name="daily", task_path="example.task")
    rows = [] if operation == "create" else [schedule if channel == "schedules" else workflow]

    async def mutate(*_args, **_kwargs):
        rows[:] = [] if operation == "delete" else [schedule if channel == "schedules" else workflow]
        return True if operation == "delete" else rows[0]

    store = SimpleNamespace(
        list_workflows=AsyncMock(side_effect=lambda: list(rows)),
        save_workflow=AsyncMock(side_effect=mutate), delete_workflow=AsyncMock(side_effect=mutate),
    )
    monkeypatch.setattr(automation, "automation_store", store)
    monkeypatch.setattr(task_socket, "automation_store", store)
    repo = SimpleNamespace(
        list_all=AsyncMock(side_effect=lambda: list(rows)),
        get_by_name=AsyncMock(return_value=schedule),
        **{name: AsyncMock(side_effect=mutate) for name in ("create", "update", "toggle", "delete")},
    )
    monkeypatch.setattr(scheduler, "get_task_repository", lambda: repo)
    monkeypatch.setattr(task_socket, "get_task_repository", lambda: repo)

    async def publish(redis_channel, payload):
        assert redis_channel == task_events.TASK_EVENT_CHANNEL
        event = json.loads(payload)
        assert event == {"type": "automation_changed", "channel": channel}
        await task_socket._forward_task_event(event)

    redis = SimpleNamespace(publish=AsyncMock(side_effect=publish))
    monkeypatch.setattr(task_events, "_publisher_connection", SimpleNamespace(ensure_connected=AsyncMock(return_value=redis)))
    path = "/api/v1/automation/workflows" if channel == "workflows" else "/api/v1/scheduler/tasks"
    body = workflow if channel == "workflows" else {"task_name": "daily", "task_path": "example.task"}
    with client.websocket_connect("/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "subscribe", "channel": channel})
        assert websocket.receive_json()["type"] == f"{channel}_snapshot"
        assert websocket.receive_json()["type"] == "subscribed"
        if operation == "create":
            response = client.post(path, json=body)
        elif operation == "update":
            response = client.put(f"{path}/daily", json=body)
        elif operation == "toggle":
            response = client.post(f"{path}/daily/toggle", json={"enabled": False})
        else:
            response = client.delete(f"{path}/daily")
        assert response.is_success, response.text
        expected = [asdict(item) for item in rows] if channel == "schedules" else rows
        assert websocket.receive_json() == {"type": f"{channel}_snapshot", "data": expected}
        redis.publish.assert_awaited_once()

        websocket.send_json({"type": "unsubscribe", "channel": channel})
        assert websocket.receive_json() == {"type": "unsubscribed", "channel": channel}
        client.portal.call(task_events.publish_automation_change, channel)
        websocket.send_json({"type": "subscribe", "channel": "tasks"})
        # No automation snapshot may arrive after unsubscribe.
        assert websocket.receive_json() == {"type": "tasks_snapshot", "data": []}
        assert websocket.receive_json() == {"type": "subscribed", "channel": "tasks"}
