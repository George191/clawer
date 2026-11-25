"""Disposable sender PG tests: real frozen rows, replay, and atomic rollback."""

from contextlib import ExitStack
from datetime import datetime, timezone
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
from app.web.services import table_export_snapshot_delivery as delivery
from app.web.services import table_export_snapshot_intent_web as web_intent
from app.web.services.table_export_snapshot_publication import SnapshotPublicationHead, SnapshotPublicationResult
from app.web.services.table_export_snapshot_publication_receiver import SnapshotPublicationProfile
from app.web.tests.test_transfer_plans import TEST_ACTOR, api

_URL = os.getenv("ONTOLOGY_TABLE_EXPORT_TEST_URL")
_SCOPE = "snapshot"
_SOURCE = "ts_table_export_sandbox"


@unittest.skipUnless(_URL, "requires disposable local table-export PostgreSQL")
class SnapshotIntentPGTests(unittest.IsolatedAsyncioTestCase):
    async def test_frozen_exact_retry_and_atomic_failure(self):
        url = make_url(_URL)
        if url.database != "ontology_table_export_regression" or url.host not in {"127.0.0.1", "::1"} or url.query:
            self.fail("Only the exact disposable sender database may be used")
        engine = create_async_engine(url)
        self.addAsyncCleanup(engine.dispose)
        source_marker, private_marker, actor = uuid4(), uuid4(), TEST_ACTOR
        migration = importlib.import_module("app.web.alembic.versions.fb804e491f6d_ontology_private_snapshot_intent")

        delivery_migration = importlib.import_module(
            "app.web.alembic.versions.e57b4216c9a8_ontology_snapshot_intent_delivery_ledger"
        )

        def provision(connection):
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                delivery_migration.upgrade()
                connection.execute(text("ALTER SCHEMA ontology_snapshot_intent_private RENAME TO snapshot"))

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
            delivery_proof = await delivery.compute_snapshot_intent_delivery_proof(db)
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

            async def web_session():
                async with AsyncSession(engine) as db:
                    yield db

            web_path = "/etl/table-export-sandbox/snapshot-intent"
            original_reader = intent._read_source
            with (
                patch.object(intent.settings, "TABLE_EXPORT_SNAPSHOT_INTENT_WEB_ENABLED", False),
                patch.object(web_intent, "read_snapshot_publication_head", new=receiver_head),
            ):
                async with api(True, web_session) as client, api(False, web_session) as reader:
                    self.assertEqual((await reader.get(web_path)).status_code, 403)
                    self.assertEqual((await client.get(web_path)).json(), {"available": False})
                    intent.settings.TABLE_EXPORT_SNAPSHOT_INTENT_WEB_ENABLED = True
                    before = receiver_head.await_count
                    cap = await client.get(web_path)
                    self.assertEqual(cap.status_code, 200)
                    self.assertEqual(cap.headers["cache-control"], "private, no-store")
                    self.assertTrue(cap.json()["available"], cap.text)
                    self.assertEqual(receiver_head.await_count, before)
                    config = cap.json()["configRevision"]
                    self.assertNotEqual(config, str(profile.config_revision))
                    self.assertEqual((await client.post(web_path + "/head", json={"expectedConfigRevision": config})).json(),
                                     {"generation": 0})
                    proof = {"requestId": str(uuid4()), "expectedConfigRevision": config, "expectedPreviousGeneration": 0}
                    receiver_head.return_value = SnapshotPublicationHead(generation=1)
                    with patch.object(intent, "_read_source", new_callable=AsyncMock) as source:
                        mismatch = await client.post(web_path, json=proof)
                        self.assertEqual((mismatch.status_code, mismatch.json()["detail"]["code"]), (409, "predecessor_changed"))
                        source.assert_not_awaited()
                    receiver_head.return_value = SnapshotPublicationHead(generation=0)

                    async def revoke_before_business_read(*args, **kwargs):
                        intent.settings.TABLE_EXPORT_SNAPSHOT_INTENT_WEB_ENABLED = False
                        return SnapshotPublicationHead(generation=0)

                    with (
                        patch.object(intent, "read_snapshot_publication_head", new=AsyncMock(side_effect=revoke_before_business_read)),
                        patch.object(intent, "_read_source", new_callable=AsyncMock) as source,
                    ):
                        revoked = await client.post(web_path, json=proof)
                        self.assertEqual((revoked.status_code, revoked.headers["cache-control"]), (404, "private, no-store"))
                        source.assert_not_awaited()
                    intent.settings.TABLE_EXPORT_SNAPSHOT_INTENT_WEB_ENABLED = True

                    async def revoke_before_local_writes(*args, **kwargs):
                        rows = await original_reader(*args, **kwargs)
                        intent.settings.TABLE_EXPORT_SNAPSHOT_INTENT_WEB_ENABLED = False
                        return rows

                    with patch.object(intent, "_read_source", new=AsyncMock(side_effect=revoke_before_local_writes)):
                        self.assertEqual((await client.post(web_path, json=proof)).status_code, 404)
                    async with engine.connect() as db:
                        self.assertIsNone(await db.scalar(text(f"SELECT 1 FROM {_SCOPE}.intents LIMIT 1")))
                        self.assertIsNone(await db.scalar(text(f"SELECT 1 FROM {_SCOPE}.records LIMIT 1")))
                    intent.settings.TABLE_EXPORT_SNAPSHOT_INTENT_WEB_ENABLED = True
                    for bad in ({**proof, "extra": 0}, {**proof, "expectedPreviousGeneration": True}):
                        self.assertEqual((await client.post(web_path, json=bad)).status_code, 404)

                    original_web = await client.post(web_path, json={**proof, "requestId": str(request_id)})
                    self.assertEqual(original_web.status_code, 200, original_web.text)
                    self.assertEqual(original_web.headers["cache-control"], "private, no-store")
                    self.assertEqual(set(original_web.json()), {"id", "expectedPreviousGeneration", "rowCount", "createdAt"})

            completed = await freeze(request_id)
            self.assertEqual(completed.row_count, 1)
            self.assertEqual(str(completed.id), original_web.json()["id"])
            async with engine.connect() as db:
                frozen_scope = await db.scalar(text(f"SELECT scope_digest FROM {_SCOPE}.intents"))
                frozen_digest = await db.scalar(text(f"SELECT payload_hash FROM {_SCOPE}.intents"))
            execute_grant = snapshot_intent.SnapshotDeliveryGrant(
                intentId=completed.id, requestId=request_id, expectedPreviousGeneration=0, sourceMarker=source_marker,
                privateMarker=private_marker, privateProofSha256=private_proof, scopeDigest=frozen_scope,
                deliveryProofSha256=delivery_proof, configRevision=profile.config_revision,
            )
            receipt = SnapshotPublicationResult(
                request_id=request_id, generation=1, previous_generation=0, row_count=1,
                recorded_at=datetime.now(timezone.utc),
            )

            async def send(original, previous, payload, **params):
                rows = list(payload)
                self.assertEqual((original, previous, rows[0].caption), (request_id, 0, "fixture-original"))
                async with engine.connect() as db:
                    claim = (await db.execute(text(f"""
                        SELECT request_id,payload_hash,receiver_generation FROM {_SCOPE}.deliveries
                    """))).one()
                self.assertEqual(tuple(claim), (request_id, frozen_digest, None))
                raise OSError("receiver publication not confirmed")

            async def confirm(original, previous, digest, **params):
                self.assertEqual((original, previous, digest), (request_id, 0, frozen_digest))
                return receipt

            publish_once = AsyncMock(side_effect=send)
            reconcile = AsyncMock(side_effect=OSError("no verified receipt"))
            patches.enter_context(patch.object(delivery, "configured_snapshot_publication_profile", return_value=profile))
            patches.enter_context(patch.object(delivery, "publish_snapshot", new=publish_once))
            patches.enter_context(patch.object(delivery, "reconcile_snapshot", new=reconcile))

            async def deliver():
                async with AsyncSession(engine) as db:
                    return await delivery.deliver_snapshot_publication_intent(
                        db, actor_id=actor, intent_id=completed.id, request_id=request_id,
                        expected_config_revision=profile.config_revision, expected_previous_generation=0,
                    )

            with self.assertRaises(delivery.SnapshotDeliveryUnavailable):
                await deliver()
            publish_once.assert_not_awaited()
            patches.enter_context(patch.object(intent.settings, "TABLE_EXPORT_SNAPSHOT_DELIVERY_ENABLED", True))
            patches.enter_context(patch.object(intent.settings, "TABLE_EXPORT_SNAPSHOT_DELIVERY_GRANTS", {actor: execute_grant}))
            web_delivery_path = "/etl/table-export-sandbox/snapshot-delivery"
            known = {"intentId": str(completed.id), "requestId": str(request_id)}
            async with api(True, web_session) as client:
                self.assertEqual((await client.get(web_delivery_path)).json(), {"available": False})
                off = await client.post(web_delivery_path + "/review", json=known)
                self.assertEqual((off.status_code, off.headers["cache-control"]), (404, "private, no-store"))
                with patch.object(intent.settings, "TABLE_EXPORT_SNAPSHOT_DELIVERY_WEB_ENABLED", True):
                    self.assertEqual((await client.get(web_delivery_path)).json(), {"available": True})
                    reviewed = await client.post(web_delivery_path + "/review", json=known)
                    self.assertEqual(reviewed.status_code, 200, reviewed.text)
                    self.assertEqual(reviewed.headers["cache-control"], "private, no-store")
                    self.assertEqual(set(reviewed.json()), {"rowCount", "expectedPreviousGeneration", "webRevision"})
                    self.assertEqual((reviewed.json()["rowCount"], reviewed.json()["expectedPreviousGeneration"]), (1, 0))
                    proof = {**known, "expectedWebRevision": reviewed.json()["webRevision"]}
                    for denied in (
                        await client.post(web_delivery_path + "?unexpected=true", json=proof),
                        await client.post(web_delivery_path, json={**proof, "expectedWebRevision": str(uuid4())}),
                        await client.post(web_delivery_path + "/review", json={**known, "intentId": str(uuid4())}),
                    ):
                        self.assertEqual((denied.status_code, denied.json()), (404, off.json()))
                    original_seal_reader = delivery._verify_frozen

                    async def lose_approval(*args, **kwargs):
                        rows = await original_seal_reader(*args, **kwargs)
                        intent.settings.TABLE_EXPORT_SNAPSHOT_DELIVERY_WEB_ENABLED = False
                        return rows

                    # The native checkpoint after seal verification must see revocation before admission.
                    with patch.object(delivery, "_verify_frozen", new=lose_approval):
                        rejected = await client.post(web_delivery_path, json=proof)
                        self.assertEqual(rejected.status_code, 503)
                    intent.settings.TABLE_EXPORT_SNAPSHOT_DELIVERY_WEB_ENABLED = True
                    async with engine.connect() as db:
                        self.assertIsNone(await db.scalar(text(f"SELECT 1 FROM {_SCOPE}.deliveries LIMIT 1")))
                    publish_once.assert_not_awaited()
                    reconcile.assert_not_awaited()
                    # The one permitted local admission is COMMITTED before the transport fault.
                    unconfirmed = await client.post(web_delivery_path, json=proof)
                    self.assertEqual((unconfirmed.status_code, unconfirmed.headers["cache-control"]), (503, "private, no-store"))
                    self.assertIn("do not resend", unconfirmed.json()["detail"])
                    self.assertEqual((await client.get(web_delivery_path)).json(), {"available": False})
                    for path, data in ((web_delivery_path + "/review", known), (web_delivery_path, proof)):
                        replay = await client.post(path, json=data)
                        self.assertEqual((replay.status_code, replay.json()), (404, off.json()))
                    publish_once.assert_awaited_once()
                    reconcile.assert_not_awaited()
            with self.assertRaises(delivery.SnapshotDeliveryUnavailable):
                await deliver()
            publish_once.assert_awaited_once()
            reconcile.assert_awaited_once()
            reconcile.side_effect = confirm
            self.assertEqual(await deliver(), receipt)
            self.assertEqual(await deliver(), receipt)
            publish_once.assert_awaited_once()
            self.assertEqual(reconcile.await_count, 3)
            async with engine.connect() as db:
                self.assertEqual((await db.execute(text(f"""
                    SELECT receiver_generation,receiver_recorded_at,receiver_row_count FROM {_SCOPE}.deliveries
                """))).one(), (1, receipt.recorded_at, 1))
            self.assertEqual(set(completed.model_dump()),
                             {"id", "created_at", "row_count", "config_revision", "expected_previous_generation"})
            async with engine.begin() as db:
                self.assertEqual(await db.scalar(text(f"SELECT caption FROM {_SCOPE}.records")), "fixture-original")
                await db.execute(text(f"DROP SCHEMA {_SOURCE} CASCADE"))
            calls = receiver_head.await_count
            receiver_head.side_effect = OSError("fixture: receiver is unavailable")
            with patch.object(intent.settings, "TABLE_EXPORT_SNAPSHOT_INTENT_WEB_ENABLED", True):
                async with api(True, web_session) as client:
                    cap = await client.get(web_path)
                    proof = {"requestId": str(request_id), "expectedConfigRevision": cap.json()["configRevision"],
                             "expectedPreviousGeneration": 0}
                    web_replay = await client.post(web_path, json=proof)
                    self.assertEqual(web_replay.status_code, 200, web_replay.text)
                    self.assertEqual(web_replay.json()["id"], str(completed.id))
                    self.assertEqual(set(web_replay.json()), {"id", "expectedPreviousGeneration", "rowCount", "createdAt"})
                    self.assertEqual((await client.post(web_path, json={**proof, "expectedPreviousGeneration": 1})).status_code, 404)
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
