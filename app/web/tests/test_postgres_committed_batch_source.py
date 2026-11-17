"""Real source-only publication/reader regression; requires a disposable UTF-8 PostgreSQL DB."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from ipaddress import ip_address
import os
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg

from app.web.services.postgres_committed_batch_source import (
    read_committed_batch, review_committed_batch_source,
)
from app.web.services.postgres_sync_source import SourceIdentityMismatch

_URL = os.getenv("ONTOLOGY_COMMITTED_SOURCE_TEST_URL")
_FIXTURE = Path(__file__).resolve().parents[3] / "tools/fixtures/committed_batch_source.sql"


@skipUnless(_URL, "Set ONTOLOGY_COMMITTED_SOURCE_TEST_URL to a disposable PostgreSQL test database")
class TestCommittedBatchSource(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        parsed = urlsplit(_URL)
        if (parsed.scheme != "postgresql" or parsed.path != "/ontology_committed_source_regression"
            or parsed.query or parsed.fragment or not ip_address(parsed.hostname or "").is_loopback):
            self.fail("Use the exact disposable local ontology_committed_source_regression database")
        self.owner = await asyncpg.connect(_URL)
        self.addAsyncCleanup(self.owner.close)
        self.competitor = await asyncpg.connect(_URL)
        self.addAsyncCleanup(self.competitor.close)
        self.role = f"committed_reader_{uuid4().hex}"
        self.marker = uuid4()
        # No IF NOT EXISTS: never adopt an existing source or tear down pre-existing schemas.
        async with self.owner.transaction():
            await self.owner.execute(_FIXTURE.read_text())
            await self.owner.execute(f"""
                CREATE ROLE {self.role} NOLOGIN;
                CREATE SCHEMA batch_sync_identity;
                CREATE TABLE batch_sync_identity.source_identity (marker uuid PRIMARY KEY);
                GRANT USAGE ON SCHEMA batch_sync_stream,batch_sync_identity TO {self.role};
                GRANT SELECT ON ALL TABLES IN SCHEMA batch_sync_stream,batch_sync_identity TO {self.role};
            """)
            await self.owner.execute("INSERT INTO batch_sync_identity.source_identity VALUES ($1)", self.marker)
        self.addAsyncCleanup(self._drop_fixture)
        self.reader = await asyncpg.connect(_URL)
        self.addAsyncCleanup(self.reader.close)
        await self.reader.execute(f"SET ROLE {self.role}")
        self.revision = datetime.now(timezone.utc)

        @asynccontextmanager
        async def saved(*args):
            async with self.reader.transaction(readonly=True, isolation="repeatable_read"):
                yield self.reader

        helper = patch("app.web.services.postgres_committed_batch_source._saved_loopback_source", saved)
        helper.start()
        self.addCleanup(helper.stop)
        self.pin = await review_committed_batch_source(None, "synthetic", self.revision, self.marker)

    async def _drop_fixture(self):
        await self.owner.execute(
            f"DROP SCHEMA batch_sync_stream CASCADE; DROP SCHEMA batch_sync_identity CASCADE; DROP ROLE {self.role}",
        )

    async def _publish(self, connection, rows):
        return await connection.fetchval("SELECT batch_sync_stream.publish($1::jsonb)", json.dumps(rows))

    async def _read(self, cursor, marker=None):
        return await read_committed_batch(
            None, "synthetic", expected_updated_at=self.revision, identity_marker=marker or self.marker,
            stream_digest=self.pin, cursor=cursor,
        )

    async def test_held_publication_blocks_competing_ordinal_and_rollback_leaves_no_gap(self):
        blocker = self.owner.transaction()
        await blocker.start()
        task = None
        try:
            self.assertEqual(await self._publish(self.owner, [{"id": 1, "value": "not committed"}]), 1)
            task = asyncio.create_task(self._publish(self.competitor, [
                {"id": 2, "value": "Σ:;"},
                {"id": "-9223372036854775808", "value": ""},
            ]))
            self.assertIsNone((await self._read(0)).ordinal)
            self.assertFalse(task.done())
        finally:
            await blocker.rollback()
        self.assertEqual(await asyncio.wait_for(task, 3), 1)
        batch = await self._read(0)
        self.assertEqual((batch.published_through, batch.ordinal), (1, 1))
        self.assertEqual(batch.rows, [{"id": -9223372036854775808, "value": ""}, {"id": 2, "value": "Σ:;"}])
        self.assertIsNone((await self._read(1)).ordinal)

    async def test_no_second_publication_same_tx_and_reader_blocks_corruption(self):
        with self.assertRaises(asyncpg.InvalidParameterValueError):
            async with self.owner.transaction():
                self.assertEqual(await self._publish(self.owner, [{"id": 1, "value": "reverted"}]), 1)
                await self._publish(self.owner, [{"id": 2, "value": "invalid second batch"}])
        self.assertIsNone((await self._read(0)).ordinal)
        for rows in [[{"id": 1, "value": "x" * 65529}], [{"id": 2, "value": ""}, {"id": 2, "value": "dup"}]]:
            with self.assertRaises(asyncpg.InvalidParameterValueError):
                await self._publish(self.owner, rows)
        self.assertEqual(await self._publish(self.owner, [{"id": 1, "value": "real"}]), 1)
        with self.assertRaises(SourceIdentityMismatch):
            await self._read(0, uuid4())
        await self.owner.execute("UPDATE batch_sync_stream.stream_head SET ordinal=0")
        with self.assertRaises(SourceIdentityMismatch):  # Do not report empty when a later manifest exists.
            await self._read(0)
        await self.owner.execute("UPDATE batch_sync_stream.stream_head SET ordinal=1")
        await self.owner.execute("UPDATE batch_sync_stream.batches SET manifest=repeat('a',64) WHERE ordinal=1")
        with self.assertRaises(SourceIdentityMismatch):
            await self._read(0)
        self.assertEqual(await self._publish(self.owner, [{"id": 1, "value": "next"}]), 2)
        await self.owner.execute("""
            DELETE FROM batch_sync_stream.batch_rows WHERE ordinal=1;
            DELETE FROM batch_sync_stream.batches WHERE ordinal=1;
        """)
        with self.assertRaises(SourceIdentityMismatch):  # The cursor cannot skip a committed manifest gap.
            await self._read(0)
