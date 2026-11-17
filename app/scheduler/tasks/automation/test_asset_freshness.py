"""Test the local asset freshness hook on the existing Automation tick."""

import os
import sys
import unittest
from types import ModuleType
from unittest.mock import AsyncMock, patch

from app.scheduler.beat_schedule import BeatScheduleRegistry
from app.scheduler.tasks.automation import tasks
from app.scheduler.tasks.batch_syncs import tasks as batch_syncs
from app.scheduler.tasks.transfer_upstream import tasks as upstream
from app.scheduler.tasks.transfers import tasks as transfers

_SERVICE = "app.web.services.asset_freshness"
_FLAG = "TRANSFER_SANDBOX_FRESHNESS_ENABLED"
_ENABLED = {"ENVIRONMENT": "local", "TRANSFER_SANDBOX_ENABLED": "true", _FLAG: "true"}


class FreshnessSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_scope_required_before_import(self):
        overrides = (
            {"ENVIRONMENT": ""},
            {"ENVIRONMENT": "production"},
            {"TRANSFER_SANDBOX_ENABLED": "false"},
            {_FLAG: ""},
            {_FLAG: "false"},
        )
        for override in overrides:
            with (
                self.subTest(override=override),
                patch.dict(os.environ, _ENABLED | override, clear=True),
                patch.dict(sys.modules, {_SERVICE: None}),
            ):
                self.assertIsNone(await tasks._evaluate_transfer_freshness_checks())

    async def test_minute_tick_evaluates_directly_only_once(self):
        service = ModuleType(_SERVICE)
        service.dispatch_asset_freshness_checks = AsyncMock(return_value={"checked": 1})
        with (
            patch.dict(os.environ, _ENABLED, clear=True),
            patch.dict(sys.modules, {_SERVICE: service}),
            patch.object(tasks.automation_task_store, "claim_due_recurring_tasks", AsyncMock(return_value=[])),
            patch.object(tasks.app, "send_task") as send,
        ):
            self.assertEqual(await tasks._dispatch_due_tasks(), {
                "claimed": 0, "dispatched": 0, "transferFreshnessChecks": {"checked": 1},
            })
        service.dispatch_asset_freshness_checks.assert_awaited_once_with()
        send.assert_not_called()
        schedule = BeatScheduleRegistry().build_schedule()
        self.assertEqual(set(schedule), {"automation_recurring_dispatch"})
        self.assertEqual(schedule["automation_recurring_dispatch"]["task"], "app.scheduler.tasks.automation.dispatch_due")

    async def test_failure_does_not_hide_existing_schedule_results(self):
        service = ModuleType(_SERVICE)
        service.dispatch_asset_freshness_checks = AsyncMock(side_effect=OSError("unavailable"))
        reserved = {"reserved": 1, "dispatched": 1}
        with (
            patch.dict(os.environ, _ENABLED, clear=True),
            patch.dict(sys.modules, {_SERVICE: service}),
            patch.object(tasks.automation_task_store, "claim_due_recurring_tasks", AsyncMock(return_value=[])),
            patch.object(transfers, "dispatch_transfer_schedule_jobs", AsyncMock(return_value=reserved)),
            patch.object(batch_syncs, "dispatch_batch_sync_schedule_jobs", AsyncMock(return_value=reserved)),
            patch.object(upstream, "dispatch_upstream_transfer_jobs", AsyncMock(return_value=reserved)),
            self.assertLogs(tasks.logger.name, level="ERROR"),
        ):
            self.assertEqual(await tasks._dispatch_due_tasks(), {
                "claimed": 0, "dispatched": 0,
                "transferSchedules": reserved, "batchSyncSchedules": reserved, "upstreamTransfers": reserved,
                "transferFreshnessChecks": {"error": "dispatch_tick_failed"},
            })

    async def test_existing_schedule_failure_does_not_prevent_evaluation(self):
        service = ModuleType(_SERVICE)
        service.dispatch_asset_freshness_checks = AsyncMock(return_value={"checked": 1})
        with (
            patch.dict(os.environ, _ENABLED, clear=True),
            patch.dict(sys.modules, {_SERVICE: service}),
            patch.object(tasks.automation_task_store, "claim_due_recurring_tasks", AsyncMock(return_value=[])),
            patch.object(upstream, "dispatch_upstream_transfer_jobs", AsyncMock(side_effect=OSError("unavailable"))),
            self.assertLogs(tasks.logger.name, level="ERROR"),
        ):
            self.assertEqual(await tasks._dispatch_due_tasks(), {
                "claimed": 0, "dispatched": 0, "upstreamTransfers": {"error": "dispatch_tick_failed"},
                "transferFreshnessChecks": {"checked": 1},
            })
