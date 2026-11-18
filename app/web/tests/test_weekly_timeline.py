"""Historical schedule cadence is derived from the exact archived UTC job."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from app.web.api.models.transfer_plans import TransferAttemptRow, TransferRunRow
from app.web.api.models.transfer_schedules import TransferScheduleJobRow
from app.web.services.asset_job_quality import _decision
from app.web.services.transfer_runs import SANDBOX, SCHEDULED_SYSTEM_ACTOR
from app.web.services.transfer_timeline import _entry, _job_detail, _linked_jobs


class WeeklyTimelineTests(unittest.IsolatedAsyncioTestCase):
    def _records(self):
        # Local Monday in UTC+2 belongs to the previous UTC day.
        slot = datetime(2026, 1, 5, 0, 30, tzinfo=timezone(timedelta(hours=2)))
        plan, job_id, operator, identity = (uuid4() for _ in range(4))
        revision = slot - timedelta(days=8)
        job = TransferScheduleJobRow(
            id=job_id, plan_id=plan, generation=4, slot=slot, expires_at=slot + timedelta(minutes=5),
            dispatch_after=slot, plan_updated_at=revision, armed_by=operator, state="committed",
            schedule_weekday_utc=7, receipt_id=identity,
        )
        scope = dict(plan_id=plan, request_id=job_id, plan_updated_at=revision,
                     source_table="source", target_table="destination", target_oid=271, target_filenode=281)
        attempt = TransferAttemptRow(id=identity, schedule_job_id=job_id, scope_trigger="week_utc",
                                     status="committed", started_at=slot, receipt_id=identity, **scope)
        receipt = TransferRunRow(id=identity, initiated_by=SCHEDULED_SYSTEM_ACTOR, write_mode="upsert",
                                 rows_affected=1, source_schema=SANDBOX, target_schema=SANDBOX,
                                 recorded_at=slot, **scope)
        session = SimpleNamespace(scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [job])))
        return session, job, attempt, receipt

    async def test_exact_weekly_attempt_receipt_and_historical_detail(self):
        session, job, attempt, receipt = self._records()
        linked = await _linked_jobs(session, [attempt, receipt])
        self.assertEqual(linked, {("attempt", attempt.id): job, ("receipt", receipt.id): job})
        evidence = {attempt.id: receipt.id}
        for item in (attempt, receipt):
            entry = _entry(item, (899, 900), job, set(), evidence)
            self.assertEqual(entry.trigger, "weekly_utc")
            self.assertEqual(entry.physicalScope, "historical")
        detail = await _job_detail(session, job)
        self.assertEqual(detail.kind, "weekly_utc")
        self.assertEqual(detail.scheduleWeekdayUtc, 7)
        self.assertEqual(detail.dueAt, job.slot)

    async def test_missing_or_contradictory_weekly_proof_has_no_fallback_label(self):
        session, job, attempt, receipt = self._records()
        job.schedule_weekday_utc = 1  # Local weekday is deliberately not the UTC weekday.
        self.assertEqual(await _linked_jobs(session, [attempt, receipt]), {})
        self.assertIsNone(await _job_detail(session, job))
        self.assertIsNone(_entry(receipt, (271, 281), None, set(), {}).trigger)

        job.schedule_weekday_utc = None
        linked = await _linked_jobs(session, [attempt, receipt])
        self.assertNotIn(("attempt", attempt.id), linked)
        detail = await _job_detail(session, job)
        self.assertEqual(detail.kind, "daily_utc")
        self.assertNotIn("scheduleWeekdayUtc", detail.model_dump())
        self.assertEqual(_entry(receipt, (271, 281), job, set(), {}).trigger, "daily_utc")

        job.schedule_weekday_utc = 7
        attempt.scope_trigger = "daily_utc"
        receipt.plan_updated_at = job.plan_updated_at + timedelta(seconds=1)
        self.assertEqual(await _linked_jobs(session, [attempt, receipt]), {})

    async def test_dense_weekly_evidence_is_not_daily_quality_proof(self):
        _, job, _, _ = self._records()
        session = SimpleNamespace(get=AsyncMock())
        outcome = await _decision(session, None, job, None, job.slot)
        self.assertEqual(outcome, ("unknown", "unverified_receipt", None, None))
        session.get.assert_not_awaited()
