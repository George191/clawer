"""Disposable sender PG tests: real frozen rows, replay, and atomic rollback."""

from contextlib import ExitStack
import importlib
import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.web.core.config import snapshot_intent
from app.web.services import table_export_snapshot_intent as intent
from app.web.services.table_export_snapshot_publication import SnapshotPublicationHead
from app.web.services.table_export_snapshot_publication_receiver import SnapshotPublicationProfile

_URL = os.getenv("ONTOLOGY_TABLE_EXPORT_TEST_URL")
_SCOPE = "ontology_snapshot_intent_private"
_SOURCE = "ts_table_export_sandbox"


@unittest.skipUnless(_URL, "requires disposable local table-export PostgreSQL")
class SnapshotIntentPGTests(unittest.IsolatedAsyncioTestCase):
    async def test_frozen_exact_retry_and_atomic_failure(self):
        url = make_url(_URL)
        if url.database != "ontology_table_export_regression" or url.host not in {"127.0.0.1", "::1"} or url.query:
            self.fail("Only the exact disposable sender database may be used")
        engine = create_async_engine(url)
        self.addAsyncCleanup(engine.dispose)
        source_marker, private_marker, actor = uuid4(), uuid4(), uuid4()
        migration = importlib.import_module("app.web.alembic.versions.fb804e491f6d_ontology_private_snapshot_intent")

        def provision(connection):
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

        async with engine.begin() as db:
            await db.run_sync(provision)
            await db.execute(text(f"""
                CREATE SCHEMA {_SOURCE}
            """))
            await db.execute(text(f"""
                CREATE TABLE {_SOURCE}.identity(singleton boolean PRIMARY KEY,marker uuid NOT NULL)
            """))
            await db.execute(text(f"""
                CREATE TABLE {_SOURCE}.records(id bigint PRIMARY KEY,caption text NOT NULL)
            """))
            await db.execute(text(f"INSERT INTO {_SOURCE}.identity VALUES (true,:marker)"), {"marker": source_marker})
            await db.execute(text(f"INSERT INTO {_SCOPE}.identity VALUES (true,:marker)"), {"marker": private_marker})
            await db.execute(text(f"INSERT INTO {_SOURCE}.records VALUES (-9223372036854775808,'fixture-original')"))

        async def cleanup():
            async with engine.begin() as db:
                await db.execute(text(f"DROP SCHEMA {_SCOPE} CASCADE"))
                await db.execute(text(f"DROP SCHEMA IF EXISTS {_SOURCE} CASCADE"))

        self.addAsyncCleanup(cleanup)
        async with AsyncSession(engine) as db, db.begin():
            await db.execute(text("SET LOCAL search_path=pg_catalog,pg_temp"))
            source_proof = await intent.compute_snapshot_intent_source_proof(db)
            private_proof = await intent.compute_snapshot_intent_private_proof(db)
        profile = SnapshotPublicationProfile(
            uuid4(), "127.0.0.1", (url.port or 5432) + 1,
            "sender_intent_provider_fixture", "receiver_writer", "dummy-test-only", uuid4(), "a"*64,
        )
        grant = snapshot_intent.SnapshotIntentGrant(
            sourceMarker=source_marker, sourceProofSha256=source_proof, privateMarker=private_marker,
            privateProofSha256=private_proof, configRevision=profile.config_revision,
        )

        async def freeze(request_id, *, selected_actor=actor, revision=profile.config_revision, prior=0,
                         session_type=AsyncSession):
            async with session_type(engine) as db:
                return await intent.freeze_snapshot_publication_intent(
                    db, actor_id=selected_actor, request_id=request_id,
                    expected_config_revision=revision, expected_previous_generation=prior,
                )

        class ActualSQLFault(AsyncSession):
            async def execute(self, statement, params=None, **kwargs):
                if str(statement).lstrip().startswith(f"INSERT INTO {_SCOPE}.records"):
                    # Runs on the actual transaction after intent header insertion.
                    return await super().execute(text(f"INSERT INTO {_SCOPE}.records (id) VALUES (1)"))
                return await super().execute(statement, params, **kwargs)

        receiver_head = AsyncMock(return_value=SnapshotPublicationHead(generation=0))
        with ExitStack() as patches:
            for field, value in {
                "ENVIRONMENT": "local", "TABLE_EXPORT_SANDBOX_ENABLED": True, "TABLE_EXPORT_SNAPSHOT_INTENT_ENABLED": True,
                "TABLE_EXPORT_SOURCE_MARKER": source_marker, "TABLE_EXPORT_SNAPSHOT_INTENT_GRANTS": {actor: grant},
            }.items():
                patches.enter_context(patch.object(intent.settings, field, value))
            patches.enter_context(patch("app.web.core.config._resolved_pg_url", return_value=_URL))
            patches.enter_context(patch.object(intent, "configured_snapshot_publication_profile", return_value=profile))
            patches.enter_context(patch.object(intent, "read_snapshot_publication_head", new=receiver_head))

            with self.assertRaises(intent.SnapshotPublicationIntentUnavailable):
                await freeze(uuid4(), selected_actor=uuid4())
            with self.assertRaises(intent.SnapshotPublicationIntentUnavailable):
                await freeze(uuid4(), revision=uuid4())
            receiver_head.assert_not_awaited()
            with patch.object(intent, "_read_source", new_callable=AsyncMock) as source:
                receiver_head.return_value = SnapshotPublicationHead(generation=1)
                with self.assertRaises(intent.SnapshotPublicationIntentUnavailable):
                    await freeze(uuid4())
                source.assert_not_awaited()
            for apply_ddl, undo_ddl in (
                ("ADD CONSTRAINT drift_check CHECK (row_count > 0)", "DROP CONSTRAINT drift_check"),
                ("ALTER COLUMN row_count SET DEFAULT 1", "ALTER COLUMN row_count DROP DEFAULT"),
            ):
                async with engine.begin() as db:
                    await db.execute(text(f"ALTER TABLE {_SCOPE}.intents {apply_ddl}"))
                before = receiver_head.await_count
                with self.assertRaises(intent.SnapshotPublicationIntentUnavailable):
                    await freeze(uuid4())
                self.assertEqual(receiver_head.await_count, before)
                async with engine.begin() as db:
                    await db.execute(text(f"ALTER TABLE {_SCOPE}.intents {undo_ddl}"))
            receiver_head.return_value = SnapshotPublicationHead(generation=0)
            request_id = uuid4()
            with self.assertRaises(intent.SnapshotPublicationIntentUnavailable):
                await freeze(request_id, session_type=ActualSQLFault)
            async with engine.connect() as db:
                self.assertIsNone(await db.scalar(text(f"SELECT 1 FROM {_SCOPE}.intents LIMIT 1")))
                self.assertIsNone(await db.scalar(text(f"SELECT 1 FROM {_SCOPE}.records LIMIT 1")))

            completed = await freeze(request_id)
            self.assertEqual(completed.row_count, 1)
            self.assertEqual(set(completed.model_dump()),
                             {"id", "created_at", "row_count", "config_revision", "expected_previous_generation"})
            async with engine.begin() as db:
                self.assertEqual(await db.scalar(text(f"SELECT caption FROM {_SCOPE}.records")), "fixture-original")
                await db.execute(text(f"DROP SCHEMA {_SOURCE} CASCADE"))
            calls = receiver_head.await_count
            receiver_head.side_effect = OSError("fixture: receiver is unavailable")
            self.assertEqual(await freeze(request_id), completed)
            with self.assertRaises(intent.SnapshotPublicationIntentUnavailable):
                await freeze(request_id, prior=1)
            with patch.object(intent.settings, "TABLE_EXPORT_SNAPSHOT_INTENT_GRANTS", {}):
                with self.assertRaises(intent.SnapshotPublicationIntentUnavailable):
                    await freeze(request_id)
            self.assertEqual(receiver_head.await_count, calls)
            async with engine.begin() as db:
                await db.execute(text(f"UPDATE {_SCOPE}.records SET caption='tampered'"))
            with self.assertRaises(intent.SnapshotPublicationIntentUnavailable):
                await freeze(request_id)
            self.assertEqual(receiver_head.await_count, calls)
