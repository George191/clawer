"""Receiver transport tests, fault injection without a database or network."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from app.web.services import table_export_receiver as receiver


class TableExportReceiverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = receiver.ReceiverConfig("127.0.0.1", 15552, "synthetic", "writer", "hidden-password", uuid4())
        self.version = datetime.now(timezone.utc)
        self.request_id = uuid4()
        self.rows = [{"id": 123, "caption": "样例"}]

    def test_digest_validates_exact_types_and_bytes(self):
        for rows in (
            [{"id": True, "caption": "x"}], [{"id": 1, "caption": "x", "extra": 1}],
            [{"id": 2, "caption": "x"}] * 2, [{"id": 1, "caption": "γ" * 5000}],
            [{"id": 1, "caption": "x\x00"}], [{"id": 1, "caption": None}],
            [{"id": int(2**63), "caption": "overflow"}],
            [{"id": i, "caption": "x"} for i in range(101)],
        ):
            with self.subTest(rows=len(rows)), self.assertRaises(receiver.ReceiverFailure):
                receiver.export_payload_digest(rows)
        self.assertTrue(receiver.export_payload_digest([{"id": 1, "caption": "γ" * 4096}]))
        other = {"id": -1, "caption": "other"}
        self.assertEqual(receiver.export_payload_digest([other, *self.rows]),
                         receiver.export_payload_digest([*self.rows, other]))
        self.assertNotIn(self.config.password, repr(self.config))

    async def test_receipt_lookup_retries_when_commit_fence_is_busy(self):
        connection = AsyncMock()
        connection.transaction = Mock(return_value=AsyncMock())
        expected = receiver.ReceiverReceipt(
            self.request_id, self.version, receiver.export_payload_digest(self.rows), 1, self.version,
        )

        @asynccontextmanager
        async def fake_connect(config):
            yield connection

        with (patch.object(receiver, "_connect", fake_connect),
              patch.object(receiver, "_fence_receiver", new=AsyncMock(side_effect=[TimeoutError(), None])),
              patch.object(receiver, "_verify_receiver", new=AsyncMock()) as verify,
              patch.object(receiver, "_read_receipt", new=AsyncMock(return_value=expected)) as read):
            with self.assertRaises(receiver.ReceiverFailure) as failure:
                await receiver.find_export_receipt(self.config, self.request_id)
            self.assertEqual(failure.exception.outcome, "retryable")
            verify.assert_not_awaited()
            read.assert_not_awaited()
            self.assertEqual(await receiver.find_export_receipt(self.config, self.request_id), expected)
            connection.transaction.assert_called_with(isolation="read_committed", readonly=True)
            read.assert_awaited_once_with(connection, self.request_id)

    async def test_known_failure_before_commit_vs_unknown_after_send(self):
        connection = AsyncMock()
        transaction = AsyncMock()
        connection.transaction = Mock(return_value=transaction)
        connection.fetchval.return_value = 0
        connection.fetchrow.return_value = {
            "request_id": self.request_id, "plan_updated_at": self.version,
            "payload_digest": receiver.export_payload_digest(self.rows),
            "rows_affected": 1, "committed_at": self.version,
        }

        @asynccontextmanager
        async def fake_connect(config):
            yield connection

        with (patch.object(receiver, "_connect", fake_connect),
              patch.object(receiver, "_verify_receiver", new=AsyncMock()),
              patch.object(receiver, "_read_receipt", new=AsyncMock(return_value=None))):
            with self.assertRaises(receiver.ReceiverFailure) as rejected:
                await receiver.apply_export_rows(self.config, self.request_id, self.version,
                                                 receiver.export_payload_digest(self.rows), self.rows)
            self.assertEqual(rejected.exception.code, "receiver_row_mismatch")
            transaction.commit.assert_not_awaited()
            connection.fetchrow.assert_not_awaited()

            connection.fetchval.return_value = 1
            transaction.commit.side_effect = OSError("never reveal hidden-password")
            with self.assertRaises(receiver.ReceiverFailure) as unknown:
                await receiver.apply_export_rows(self.config, self.request_id, self.version,
                                                 receiver.export_payload_digest(self.rows), self.rows)
            self.assertEqual(unknown.exception.outcome, "outcome_unknown")
            self.assertNotIn(self.config.password, unknown.exception.detail)
