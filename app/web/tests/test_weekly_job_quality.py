"""Weekly UTC quality observes exact committed matches, never schedule intent."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from app.web.api.models.asset_job_quality import TransferTargetRejectionRow
from app.web.api.models.transfer_plans import TransferAttemptRow, TransferRunRow
from app.web.services.asset_weekly_job_quality import _decision, _expected_slot
from app.web.services.transfer_runs import SANDBOX, SCHEDULED_SYSTEM_ACTOR


class WeeklyJobQualityProofTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # UTC Sunday job observed just after UTC midnight Monday.
        self.slot = datetime(2031, 1, 5, 23, 59, tzinfo=timezone.utc)
        self.finished = self.slot + timedelta(minutes=2)
        self.now = self.finished + timedelta(seconds=10)
        self.plan = SimpleNamespace(id=uuid4(), source_table="quality_source")
        self.review = SimpleNamespace(plan_updated_at=self.slot - timedelta(days=10), target_table="weekly_target",
                                      target_oid=101, target_filenode=102)
        self.job = SimpleNamespace(id=uuid4(), plan_id=self.plan.id, slot=self.slot, state="committed",
                                   dispatched_at=self.slot, finished_at=self.finished,
                                   expires_at=self.slot + timedelta(minutes=5), receipt_id=uuid4())
        common = dict(plan_id=self.plan.id, request_id=self.job.id, plan_updated_at=self.review.plan_updated_at,
                      source_table=self.plan.source_table, target_table=self.review.target_table,
                      target_oid=self.review.target_oid, target_filenode=self.review.target_filenode)
        self.run = TransferRunRow(
            id=self.job.receipt_id, initiated_by=SCHEDULED_SYSTEM_ACTOR,
            recorded_at=self.finished, source_schema=SANDBOX, target_schema=SANDBOX,
            rows_affected=2, write_mode="upsert", **common,
        )
        self.attempt = TransferAttemptRow(
            id=uuid4(), scope_trigger="week_utc", schedule_job_id=self.job.id,
            started_at=self.slot + timedelta(seconds=1), finished_at=self.finished,
            status="committed", receipt_id=self.job.receipt_id, **common,
        )
        self.session = SimpleNamespace(
            get=AsyncMock(return_value=self.run),
            scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [self.attempt])),
        )

    def test_expected_slot_uses_utc_weekday_not_local(self):
        two_hours_ahead = timezone(timedelta(hours=2))
        self.assertEqual(_expected_slot(self.finished.astimezone(two_hours_ahead), "23:59", 7), self.slot)
        self.assertEqual(_expected_slot(self.slot - timedelta(microseconds=1), "23:59", 7),
                         self.slot - timedelta(days=7))

    async def test_positive_requires_exact_bounded_proof_and_carries_no_zero_health(self):
        self.assertEqual(await _decision(self.session, self.review, self.job, self.plan, self.now),
                         ("healthy", None, self.run.id, 2))
        self.run.rows_affected = 0
        self.assertEqual(await _decision(self.session, self.review, self.job, self.plan, self.now),
                         ("unknown", "zero_rows", None, 0))
        self.run.rows_affected = 101
        self.assertEqual(await _decision(self.session, self.review, self.job, self.plan, self.now),
                         ("unknown", "unverified_receipt", None, None))
        self.run.rows_affected = 2
        self.job.dispatched_at = self.slot + timedelta(seconds=30)
        self.run.recorded_at = self.slot + timedelta(seconds=20)
        self.assertEqual(await _decision(self.session, self.review, self.job, self.plan, self.now),
                         ("unknown", "unverified_receipt", None, None))
        self.run.recorded_at, self.job.dispatched_at = self.finished, self.slot
        self.attempt.scope_trigger = "daily_utc"
        self.assertEqual(await _decision(self.session, self.review, self.job, self.plan, self.now),
                         ("unknown", "unverified_receipt", None, None))
        self.attempt.scope_trigger = "week_utc"
        self.attempt.started_at = self.slot - timedelta(seconds=1)
        self.assertEqual((await _decision(self.session, self.review, self.job, self.plan, self.now))[0], "unknown")

    async def test_rejected_requires_scoped_diagnostics_and_neutral_never_forges_proof(self):
        self.job.state, self.job.receipt_id = "rejected", None
        self.attempt.status, self.attempt.receipt_id = "rejected", None
        proof = TransferTargetRejectionRow(
            job_id=self.job.id, attempt_id=self.attempt.id,
            target_oid=self.review.target_oid, target_filenode=self.review.target_filenode,
        )
        self.session.get.side_effect = [proof, self.attempt]
        self.assertEqual(await _decision(self.session, self.review, self.job, self.plan, self.now),
                         ("unhealthy", "target_rejected", None, None))
        self.attempt.schedule_job_id = uuid4()
        self.session.get.side_effect = [proof, self.attempt]
        self.assertEqual(await _decision(self.session, self.review, self.job, self.plan, self.now),
                         ("unknown", "unproven_rejection", None, None))
        self.job.state, self.job.finished_at = "expired", self.job.expires_at
        self.session.get.reset_mock()
        self.assertEqual(await _decision(self.session, self.review, self.job, self.plan, self.job.expires_at),
                         ("unknown", "expired", None, None))
        self.session.get.assert_not_awaited()
