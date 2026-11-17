"""Generic raw SQL must not expose the local push inbox or race its first receipt."""

from contextlib import asynccontextmanager
from ipaddress import ip_address
import os
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.web.services.etl_layers import EtlLayers, _PUSH_INBOX_MARKER, _PUSH_INBOX_RECEIPTS

_URL = os.getenv("ONTOLOGY_PUSH_QUERY_GUARD_TEST_URL")


@skipUnless(_URL, "Set ONTOLOGY_PUSH_QUERY_GUARD_TEST_URL to a disposable PostgreSQL DB")
class TestPushInboxQueryGuard(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        url = make_url(_URL)
        if (url.drivername != "postgresql+asyncpg" or url.database != "ontology_push_query_guard_regression"
            or url.query or not ip_address(url.host or "").is_loopback):
            self.fail("Use the exact disposable local ontology_push_query_guard_regression database")
        self.engine = create_async_engine(url)
        self.addAsyncCleanup(self.engine.dispose)
        self.layers = EtlLayers()
        self.publish_on_gate_read = False

        owner = self

        class TestPostgres:
            async def connect(self, **kwargs):
                pass

            @asynccontextmanager
            async def session(self):
                async with AsyncSession(bind=owner.engine) as session:
                    async with session.begin():
                        real = session.scalar

                        async def observe(statement, *args, **kwargs):
                            value = await real(statement, *args, **kwargs)
                            if owner.publish_on_gate_read and str(statement) == (
                                f"SELECT EXISTS (SELECT 1 FROM {_PUSH_INBOX_RECEIPTS})"
                            ):
                                owner.publish_on_gate_read = False
                                if value:
                                    raise AssertionError("Race setup must observe an initially empty inbox")
                                await owner._write(
                                    f"UPDATE {_PUSH_INBOX_MARKER} SET database_marker=1 WHERE singleton",
                                    f"INSERT INTO {_PUSH_INBOX_RECEIPTS} VALUES ('not readable in the old snapshot')",
                                )
                            return value

                        with patch.object(session, "scalar", side_effect=observe):
                            yield session

        self.layers._pg = TestPostgres()

    async def _write(self, *statements):
        async with self.engine.begin() as connection:
            for statement in statements:
                await connection.execute(text(statement))

    async def _create_empty(self):
        # No IF NOT EXISTS: tests cannot adopt or drop a pre-existing inbox.
        await self._write(
            f"CREATE TABLE {_PUSH_INBOX_MARKER} (singleton boolean PRIMARY KEY, database_marker integer)",
            f"INSERT INTO {_PUSH_INBOX_MARKER} VALUES (true,NULL)",
            f"CREATE TABLE {_PUSH_INBOX_RECEIPTS} (payload text)",
        )
        self.addAsyncCleanup(self._write, f"DROP TABLE {_PUSH_INBOX_RECEIPTS}", f"DROP TABLE {_PUSH_INBOX_MARKER}")

    async def _normal(self):
        output = await self.layers.query("SELECT 1 AS value")
        self.assertEqual(output["rows"], [{"value": 1}])

    async def _blocked(self):
        with self.assertRaises(HTTPException) as error:
            await self.layers.query("SELECT 1 AS value")
        self.assertEqual(error.exception.status_code, 403)

    async def test_absent_and_empty_allow_existing_sql_but_marker_or_receipt_fail_closed(self):
        await self._normal()
        await self._create_empty()
        await self._normal()
        await self._write(f"UPDATE {_PUSH_INBOX_MARKER} SET database_marker=1 WHERE singleton")
        await self._blocked()
        await self._write(f"UPDATE {_PUSH_INBOX_MARKER} SET database_marker=NULL WHERE singleton", f"INSERT INTO {_PUSH_INBOX_RECEIPTS} VALUES ('historical')")
        await self._blocked()

    async def test_new_receipt_cannot_race_generic_sql_snapshot(self):
        await self._create_empty()
        self.publish_on_gate_read = True
        result = await self.layers.query(f"SELECT payload FROM {_PUSH_INBOX_RECEIPTS}")
        self.assertFalse(self.publish_on_gate_read)
        self.assertEqual(result["rows"], [])
        await self._blocked()
