import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from app.web.api.routes import automation, tasks
from app.web.services.automation_task_store import AutomationTaskStore


class AutomationConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_history_is_bounded_and_excludes_deleted_tasks(self):
        store = AutomationTaskStore()
        store.initialize = AsyncMock()
        store._pg = SimpleNamespace(fetch_all=AsyncMock(return_value=[]))
        self.assertEqual(await store.list_executions("ai-collect", 100), [])
        sql, params = store._pg.fetch_all.await_args.args
        self.assertIn("task.deleted_at IS NULL", sql)
        self.assertIn("LIMIT :limit", sql)
        self.assertIn("task.product_domain = :domain", sql)
        self.assertEqual(params, {"domain": "ai-collect", "limit": 100})

    async def test_history_endpoint_preserves_query(self):
        with patch.object(tasks.automation_task_store, "list_executions", AsyncMock(return_value=[])) as read:
            self.assertEqual(await tasks.list_executions("platform", 25), {"items": []})
            read.assert_awaited_once_with("platform", 25)

    async def test_worker_inspection_does_not_expose_broker_or_task_arguments(self):
        inspector = SimpleNamespace(
            stats=Mock(return_value={"worker-1": {"pid": 42, "uptime": 60, "pool": {"max-concurrency": 4}, "total": {"collect": 3}, "broker": {"password": "secret"}}}),
            active=Mock(return_value={"worker-1": [{"id": "run-1", "name": "collect", "args": "secret input"}]}),
            active_queues=Mock(return_value={"worker-1": [{"name": "collection"}]}),
        )
        with patch("app.scheduler.celery_app.app.control.inspect", return_value=inspector):
            result = await automation.list_workers(SimpleNamespace())
        self.assertEqual(result["items"][0]["concurrency"], 4)
        self.assertEqual(result["items"][0]["active"], [{"id": "run-1", "name": "collect", "started_at": None}])
        self.assertNotIn("secret", str(result))

    async def test_unresponsive_workers_are_an_explicit_failure(self):
        inspector = SimpleNamespace(stats=Mock(return_value=None), active=Mock(return_value=None), active_queues=Mock(return_value=None))
        with patch("app.scheduler.celery_app.app.control.inspect", return_value=inspector):
            with self.assertRaises(HTTPException) as raised:
                await automation.list_workers(SimpleNamespace())
        self.assertEqual(raised.exception.status_code, 503)
