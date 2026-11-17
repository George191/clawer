"""Exercise registered batch sync task wiring without a broker or database."""

import asyncio
import os
import sys
import unittest
from types import ModuleType
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.scheduler.beat_schedule import BeatScheduleRegistry
from app.scheduler.celery_app import CeleryAppFactory
from app.scheduler.tasks.batch_syncs import tasks

_SERVICE = "app.web.services.batch_sync_schedules"
_ENABLED = {
    "ENVIRONMENT": "local", "BATCH_SYNC_SANDBOX_ENABLED": "true",
    "BATCH_SYNC_SANDBOX_SCHEDULER_ENABLED": "true",
}


class BatchSyncSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_gate_short_circuits_without_import_or_publish(self):
        for overrides in (
            {"ENVIRONMENT": ""},
            {"ENVIRONMENT": "production"},
            {"BATCH_SYNC_SANDBOX_ENABLED": "false"},
            {"BATCH_SYNC_SANDBOX_SCHEDULER_ENABLED": ""},
        ):
            with (
                self.subTest(overrides=overrides),
                patch.dict(os.environ, _ENABLED | overrides, clear=True),
                patch.dict(sys.modules, {_SERVICE: None}),
                patch.object(tasks.app, "send_task") as send,
            ):
                self.assertIsNone(await tasks.dispatch_batch_sync_schedule_jobs())
                self.assertEqual(tasks.execute_scheduled_batch_sync.run(str(uuid4())), "disabled")
                send.assert_not_called()

    async def test_publishes_durable_id_without_recording_failed_publish(self):
        failed, sent = uuid4(), uuid4()
        service = ModuleType(_SERVICE)
        service.list_due_batch_sync_jobs = AsyncMock(return_value=[failed, sent])
        service.record_batch_sync_job_dispatched = AsyncMock()
        with (
            patch.dict(os.environ, _ENABLED),
            patch.dict(sys.modules, {_SERVICE: service}),
            patch.object(tasks.app, "send_task", side_effect=[OSError("no broker"), object()]) as send,
            self.assertLogs(tasks.logger.name, level="ERROR"),
        ):
            self.assertEqual(await tasks.dispatch_batch_sync_schedule_jobs(), {"reserved": 2, "dispatched": 1})
        service.record_batch_sync_job_dispatched.assert_awaited_once_with(sent)
        for call, job_id in zip(send.call_args_list, (failed, sent), strict=True):
            self.assertEqual(call.args, (tasks.EXECUTE_TASK,))
            self.assertEqual(call.kwargs, {
                "args": [str(job_id)], "task_id": str(job_id), "queue": "scheduler", "retry": False,
            })

    async def test_existing_automation_tick_runs_both_independently(self):
        from app.scheduler.tasks.automation import tasks as automation
        from app.scheduler.tasks.transfers import tasks as transfers

        with (
            patch.object(automation.automation_task_store, "claim_due_recurring_tasks", new=AsyncMock(return_value=[])),
            patch.object(transfers, "dispatch_transfer_schedule_jobs", new=AsyncMock(side_effect=OSError("unavailable"))),
            patch.object(tasks, "dispatch_batch_sync_schedule_jobs", new=AsyncMock(return_value={"reserved": 1, "dispatched": 1})),
            self.assertLogs(automation.logger.name, level="ERROR"),
        ):
            self.assertEqual(await automation._dispatch_due_tasks(), {
                "claimed": 0, "dispatched": 0,
                "transferSchedules": {"error": "dispatch_tick_failed"},
                "batchSyncSchedules": {"reserved": 1, "dispatched": 1},
            })


class BatchSyncExecutorTests(unittest.TestCase):
    def test_worker_discovery_uses_same_beat_and_queue(self):
        self.assertIn(tasks.__name__, CeleryAppFactory()._discover_task_modules())
        registered = tasks.app.tasks[tasks.EXECUTE_TASK]
        self.assertTrue(registered.acks_late)
        self.assertTrue(registered.reject_on_worker_lost)
        schedule = BeatScheduleRegistry().build_schedule()
        self.assertEqual(set(schedule), {"automation_recurring_dispatch"})
        tick = schedule["automation_recurring_dispatch"]
        self.assertEqual(tick["task"], "app.scheduler.tasks.automation.dispatch_due")
        self.assertEqual(tick["options"]["queue"], "scheduler")

    def test_registered_worker_delegates_only_uuid(self):
        job_id = uuid4()
        service = ModuleType(_SERVICE)
        service.execute_scheduled_batch_sync_job = AsyncMock(return_value="committed")
        with (
            patch.dict(os.environ, _ENABLED),
            patch.dict(sys.modules, {_SERVICE: service}),
            patch.object(tasks, "run_async", side_effect=asyncio.run),
        ):
            self.assertEqual(tasks.app.tasks[tasks.EXECUTE_TASK].run(str(job_id)), "committed")
        service.execute_scheduled_batch_sync_job.assert_awaited_once_with(job_id)
