"""Boundaries of the read-only snapshot used by managed PostgreSQL syncs."""

from datetime import datetime, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.web.services import postgres_sync_source as reader
from app.web.services.data_connections import DataConnections


class PostgreSQLSyncSourceTests(IsolatedAsyncioTestCase):
    async def test_does_not_decrypt_or_connect_to_non_loopback_host(self):
        reference = datetime.now(timezone.utc)
        connections = object.__new__(DataConnections)
        for host in ("source.example.test", "192.0.2.1"):
            with (
                self.subTest(host=host),
                patch.object(connections, "_stored", new_callable=AsyncMock, return_value={
                    "name": "Synthetic source",
                    "engine": "postgresql",
                    "updated_at": reference,
                    "config": {"host": host, "port": 5432, "database": "demo", "username": "readonly",
                               "auth": "password", "tls": True},
                }),
                patch.object(connections, "_credentials", new_callable=AsyncMock) as credentials,
                patch.object(reader, "_driver") as driver,
                self.assertRaises(HTTPException) as failure,
            ):
                await reader.read_batch_sync_snapshot(connections, "saved", expected_updated_at=reference,
                                                      schema="demo", table="source", columns=["id"], key_column="id")
            self.assertEqual(failure.exception.status_code, 422)
            credentials.assert_not_called()
            driver.assert_not_called()

    def test_plan_guard_requires_exact_index_below_limit(self):
        def plan(child):
            return [{"Plan": {"Node Type": "Limit", "Plans": [child]}}]

        self.assertTrue(reader._lookup_uses_primary_index(
            plan({"Node Type": "Index Only Scan", "Index Name": "source_pkey"}), "source_pkey",
        ))
        for child in ({"Node Type": "Seq Scan"}, {"Node Type": "Index Scan", "Index Name": "another_idx"},
                      {"Node Type": "Sort", "Plans": [{"Node Type": "Index Scan", "Index Name": "source_pkey"}]}):
            self.assertFalse(reader._lookup_uses_primary_index(plan(child), "source_pkey"))
