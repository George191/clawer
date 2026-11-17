"""Isolated worker checks; no broker or database is used."""

import os
import sys
import unittest
from types import ModuleType
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.scheduler.tasks.transfers import tasks

SERVICE_MODULE = "app.web.services.transfer_schedules"
ENABLED = {
    "ENVIRONMENT": "local",
    "TRANSFER_SANDBOX_ENABLED": "true",
    "TRANSFER_SANDBOX_SCHEDULER_ENABLED": "true",
}


class TransferSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_tick_and_worker_do_not_publish_or_import_web_service(self):
        for overrides in (
            {"ENVIRONMENT": ""},
            {"ENVIRONMENT": "production"},
            {"TRANSFER_SANDBOX_ENABLED": "false"},
            {"TRANSFER_SANDBOX_SCHEDULER_ENABLED": ""},
        ):
            with (
                self.subTest(overrides=overrides),
                patch.dict(os.environ, ENABLED | overrides, clear=True),
                patch.dict(sys.modules, {SERVICE_MODULE: None}),
                patch.object(tasks.app, "send_task") as send,
            ):
                self.assertIsNone(await tasks.dispatch_transfer_schedule_jobs())
                self.assertEqual(tasks.execute_scheduled_transfer.run(str(uuid4())), "disabled")
                send.assert_not_called()

    async def test_existing_minute_tick_calls_transfer_dispatch_and_isolates_failure(self):
        from app.scheduler.tasks.automation import tasks as automation

        with (
            patch.object(automation.automation_task_store, "claim_due_recurring_tasks", new_callable=AsyncMock) as due,
            patch.object(tasks, "dispatch_transfer_schedule_jobs", new_callable=AsyncMock) as transfer_dispatch,
        ):
            due.return_value = []
            transfer_dispatch.return_value = {"reserved": 1, "dispatched": 1}
            self.assertEqual(await automation._dispatch_due_tasks(), {
                "claimed": 0, "dispatched": 0, "transferSchedules": {"reserved": 1, "dispatched": 1},
            })
            transfer_dispatch.side_effect = OSError("local sandbox unavailable")
            with self.assertLogs(automation.logger.name, level="ERROR"):
                self.assertEqual(await automation._dispatch_due_tasks(), {
                    "claimed": 0, "dispatched": 0, "transferSchedules": {"error": "dispatch_tick_failed"},
                })
            self.assertEqual(due.await_count, 2)

    async def test_tick_reuses_durable_job_identity_and_does_not_mark_publish_failure(self):
        first, second = uuid4(), uuid4()
        service = ModuleType(SERVICE_MODULE)
        service.list_due_dispatch_jobs = AsyncMock(return_value=[first, second])
        service.record_transfer_job_dispatched = AsyncMock()
        with (
            patch.dict(os.environ, ENABLED),
            patch.dict(sys.modules, {SERVICE_MODULE: service}),
            patch.object(tasks.app, "send_task", side_effect=[OSError("broker unavailable"), object()]) as send,
            self.assertLogs(tasks.logger.name, level="ERROR"),
        ):
            self.assertEqual(await tasks.dispatch_transfer_schedule_jobs(), {"reserved": 2, "dispatched": 1})
        service.record_transfer_job_dispatched.assert_awaited_once_with(second)
        for call, job_id in zip(send.call_args_list, [first, second], strict=True):
            self.assertEqual(call.args, (tasks.EXECUTE_TASK,))
            self.assertEqual(call.kwargs, {"args": [str(job_id)], "task_id": str(job_id), "queue": "scheduler", "retry": False})


class TransferExecutorTests(unittest.TestCase):
    def test_task_delegates_job_id_to_durable_executor(self):
        import asyncio

        job_id = uuid4()
        service = ModuleType(SERVICE_MODULE)
        service.execute_scheduled_transfer_job = AsyncMock(return_value="settled")
        with (
            patch.dict(os.environ, ENABLED),
            patch.dict(sys.modules, {SERVICE_MODULE: service}),
            patch.object(tasks, "run_async", side_effect=asyncio.run),
        ):
            self.assertEqual(tasks.execute_scheduled_transfer.run(str(job_id)), "settled")
        service.execute_scheduled_transfer_job.assert_awaited_once_with(job_id)
