"""Outbound sender contracts; two-database receipt integration is tested separately."""

from datetime import datetime, timezone
from importlib import import_module
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
import httpx
from pydantic import SecretStr, ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.storage.postgres_client import PostgresClient
from app.web.api.dependencies.common import get_current_user
from app.web.api.dependencies.session import get_db
from app.web.api.models.table_exports import TableExportReview
from app.web.api.routes.etl import router
from app.web.core.config import settings
from app.web.services.table_export_receiver import ReceiverFailure, ReceiverReceipt

URL = os.environ.get("ONTOLOGY_TABLE_EXPORT_TEST_URL")
DATABASE = "ontology_table_export_regression"
ENDPOINT = "/etl/table-export"
ACTOR = uuid4()
SOURCE_MARKER = uuid4()


def api(is_admin, provider=lambda: None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = provider
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=ACTOR, is_superuser=is_admin)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TableExportContracts(unittest.IsolatedAsyncioTestCase):
    def test_review_disallows_endpoint_and_requires_version(self):
        TableExportReview.model_validate({"name": "reviewed", "expectedUpdatedAt": None})
        for payload in [{"name": "reviewed"}, {"name": "reviewed", "expectedUpdatedAt": None, "host": "invalid"}]:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                TableExportReview.model_validate(payload)

    async def test_disabled_and_reader_gates(self):
        with patch.object(settings, "TABLE_EXPORT_SANDBOX_ENABLED", False):
            async with api(True) as client:
                self.assertFalse((await client.get(ENDPOINT + "/capabilities")).json()["enabled"])
                self.assertEqual((await client.post(ENDPOINT + "/runs", json={
                    "requestId": str(uuid4()), "expectedUpdatedAt": datetime.now(timezone.utc).isoformat(),
                })).status_code, 403)
            async with api(False) as client:
                for route in ("", "/capabilities", "/runs", "/attempts", "/lineage"):
                    self.assertEqual((await client.get(ENDPOINT + route)).status_code, 403)


@unittest.skipUnless(URL, "Requires explicitly isolated ontology_table_export_regression PostgreSQL")
class TableExportSenderIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if make_url(URL).database != DATABASE:
            raise RuntimeError("Sender fixture refuses other databases")
        self.pg = PostgresClient(URL)
        await self.pg.connect(max_retries=1)
        self.addAsyncCleanup(self.pg.close)
        if (await self.pg.fetch_one("SELECT current_database() AS name"))["name"] != DATABASE:
            raise RuntimeError("Connected to a non-fixture sender database")
        migration = import_module("app.web.alembic.versions.b31c72f6a9de_ontology_table_exports")
        async with self.pg.session() as session:
            def upgrade(sync_session):
                with Operations.context(MigrationContext.configure(sync_session.connection())):
                    migration.upgrade()

            await session.run_sync(upgrade)
            await session.execute(text("CREATE SCHEMA ts_table_export_sandbox"))
            await session.execute(text('CREATE TABLE ts_table_export_sandbox."identity" (singleton bool PRIMARY KEY CHECK(singleton), marker uuid NOT NULL)'))
            await session.execute(text("CREATE TABLE ts_table_export_sandbox.records (id bigint PRIMARY KEY, caption text NOT NULL)"))
            await session.execute(text('INSERT INTO ts_table_export_sandbox."identity" VALUES(true,:marker)'), {"marker": SOURCE_MARKER})
            await session.execute(text("INSERT INTO ts_table_export_sandbox.records VALUES (1, 'synthetic source')"))
        self.addAsyncCleanup(self.drop_fixture)
        configured = patch.multiple(
            settings, ENVIRONMENT="local", TABLE_EXPORT_SANDBOX_ENABLED=True,
            TABLE_EXPORT_SOURCE_MARKER=SOURCE_MARKER, TABLE_EXPORT_RECEIVER_HOST="127.0.0.1",
            TABLE_EXPORT_RECEIVER_DATABASE="isolated-fake-receiver", TABLE_EXPORT_RECEIVER_USERNAME="isolated",
            TABLE_EXPORT_RECEIVER_PASSWORD=SecretStr("local-test-only"), TABLE_EXPORT_RECEIVER_MARKER=uuid4(),
        )
        configured.start()
        self.addCleanup(configured.stop)

    async def drop_fixture(self):
        async with self.pg.session() as session:
            await session.execute(text("DROP TABLE ontology_table_export_attempts, ontology_table_export_receipts, ontology_table_export_requests, ontology_table_export_plan"))
            await session.execute(text("DROP SCHEMA ts_table_export_sandbox CASCADE"))

    async def provider(self):
        async with self.pg.session() as session:
            yield session

    async def test_durable_pin_drift_and_lost_receiver_ack_reconcile_without_reread(self):
        receipts = {}
        delivered = []

        async def lookup(config, request_id):
            return receipts.get(request_id)

        async def receive(config, request_id, version, digest, rows):
            pinned = await self.pg.fetch_one(
                "SELECT payload_digest FROM ontology_table_export_requests WHERE request_id=:id", {"id": request_id},
            )
            self.assertEqual(pinned["payload_digest"], digest)  # Independent transaction sees it before receiver work.
            with self.assertRaises(DBAPIError) as blocked:
                async with self.pg.session() as contender:
                    await contender.execute(text("SELECT singleton FROM ontology_table_export_plan FOR UPDATE NOWAIT"))
            self.assertEqual(getattr(blocked.exception.orig, "sqlstate", None), "55P03")
            delivered.append((request_id, rows))
            if len(delivered) == 1:
                raise ReceiverFailure(409, "receiver_busy", "private simulated message", "retryable")
            receipts[request_id] = ReceiverReceipt(request_id, version, digest, len(rows), datetime.now(timezone.utc))
            raise ReceiverFailure(503, "receiver_commit_unknown", "private simulated message", "outcome_unknown")

        async with api(True, self.provider) as client:
            with (
                patch("app.web.services.table_exports.find_export_receipt", side_effect=lookup),
                patch("app.web.services.table_exports.apply_export_rows", side_effect=receive),
            ):
                reviewed = await client.put(ENDPOINT, json={"name": "Synthetic pilot", "expectedUpdatedAt": None})
                self.assertEqual(reviewed.status_code, 200, reviewed.text)
                version = reviewed.json()["updatedAt"]

                async def run(key):
                    return await client.post(ENDPOINT + "/runs", json={"requestId": str(key), "expectedUpdatedAt": version})

                first = uuid4()
                busy = await run(first)
                self.assertEqual(busy.status_code, 409, busy.text)
                await self.pg.execute("UPDATE ts_table_export_sandbox.records SET caption='changed after pin'")
                rejected = await run(first)
                self.assertEqual(rejected.json()["detail"]["code"], "payload_changed_no_receipt")
                self.assertEqual(len(delivered), 1)
                self.assertEqual((await client.get(ENDPOINT + "/runs")).json(), [])
                second = uuid4()
                unknown = await run(second)
                self.assertEqual(unknown.status_code, 503, unknown.text)
                self.assertNotIn("private simulated message", unknown.text)
                self.assertEqual((await client.get(ENDPOINT + "/runs")).json(), [])
                unsettled = (await client.get(ENDPOINT + "/lineage")).json()
                self.assertFalse(unsettled["observed"])
                self.assertEqual(unsettled["receipts"], [])
                # Replay must not attempt source read, even if source is now unavailable.
                await self.pg.execute("ALTER TABLE ts_table_export_sandbox.records RENAME TO unavailable")
                replay = await run(second)
                self.assertEqual(replay.status_code, 200, replay.text)
                self.assertEqual((await run(second)).json(), replay.json())
                self.assertEqual(len(delivered), 2)
                history = (await client.get(ENDPOINT + "/attempts")).json()
                self.assertEqual([r["status"] for r in history], ["replayed", "replayed", "outcome_unknown", "rejected", "retryable"])
                self.assertNotIn("synthetic source", str(history))
                self.assertEqual((await client.get(ENDPOINT + "/runs")).json(), [replay.json()])

    async def test_receiver_receipt_without_sender_digest_fails_closed(self):
        async def rogue(config, request_id):
            return ReceiverReceipt(request_id, datetime(2026, 11, 17, tzinfo=timezone.utc), "e" * 64, 1,
                                   datetime.now(timezone.utc))

        async with api(True, self.provider) as client:
            review = (await client.put(ENDPOINT, json={"name": "Synthetic pilot", "expectedUpdatedAt": None})).json()
            with (patch("app.web.services.table_exports.find_export_receipt", side_effect=rogue),
                  patch("app.web.services.table_exports._source_rows") as source):
                response = await client.post(ENDPOINT + "/runs", json={
                    "requestId": str(uuid4()), "expectedUpdatedAt": review["updatedAt"],
                })
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["detail"]["code"], "receipt_conflict")
            source.assert_not_called()
            self.assertEqual((await client.get(ENDPOINT + "/runs")).json(), [])

    async def test_lineage_uses_positive_mirrors_and_preserves_write_time_review(self):
        recorded = datetime(2026, 11, 17, 8, 15, tzinfo=timezone.utc)

        async def receive(config, request_id, version, digest, rows):
            return ReceiverReceipt(request_id, version, digest, len(rows), recorded)

        async with api(True, self.provider) as client:
            path = ENDPOINT + "/lineage"
            before = (await client.get(path)).json()
            self.assertIsNone(before["declaration"])
            self.assertFalse(before["observed"])
            reviewed = (await client.put(ENDPOINT, json={"name": "Initial review", "expectedUpdatedAt": None})).json()
            version = reviewed["updatedAt"]
            self.assertEqual((await client.get(path)).json()["declaration"]["state"], "enabled")
            with (
                patch("app.web.services.table_exports.find_export_receipt", return_value=None) as lookup,
                patch("app.web.services.table_exports.apply_export_rows", side_effect=receive) as target,
            ):
                async def export():
                    response = await client.post(ENDPOINT + "/runs", json={
                        "requestId": str(uuid4()), "expectedUpdatedAt": version,
                    })
                    self.assertEqual(response.status_code, 200, response.text)
                    return response.json()

                await self.pg.execute("DELETE FROM ts_table_export_sandbox.records")
                empty = await export()
                no_flow = (await client.get(path)).json()
                self.assertEqual(no_flow["receipts"][0]["id"], empty["id"])
                self.assertFalse(no_flow["receipts"][0]["observed"])
                self.assertIsNone(no_flow["latestObservation"])
                await self.pg.execute("INSERT INTO ts_table_export_sandbox.records VALUES (2,'not lineage metadata')")
                visible = await export()
                with patch("app.web.services.table_exports._source_rows") as source:
                    current = (await client.get(path)).json()
                source.assert_not_called()
                self.assertTrue(current["observed"])
                self.assertEqual(current["latestObservation"], {
                    "id": visible["id"], "recordedAt": visible["recordedAt"], "rowsAffected": 1,
                    "reviewedAt": version, "reviewRelation": "current_review", "observed": True,
                })
                self.assertEqual(current["source"], {"schema": "ts_table_export_sandbox", "table": "records"})
                self.assertEqual(current["destination"]["schema"], "ts_table_export_receiver")
                self.assertNotIn("isolated-fake-receiver", str(current))
                self.assertNotIn("local-test-only", str(current))
                self.assertNotIn("not lineage metadata", str(current))
                with patch.object(settings, "TABLE_EXPORT_SANDBOX_ENABLED", False):
                    inactive = (await client.get(path)).json()
                self.assertEqual(inactive["declaration"]["state"], "inactive")
                self.assertTrue(inactive["observed"])
                renamed = (await client.put(ENDPOINT, json={"name": "Later review", "expectedUpdatedAt": version})).json()
                history = (await client.get(path)).json()
                self.assertFalse(history["observed"])
                self.assertIsNone(history["latestObservation"])
                old = next(item for item in history["receipts"] if item["id"] == visible["id"])
                self.assertEqual((old["reviewRelation"], old["reviewedAt"], old["observed"]),
                                 ("earlier_review", version, True))
                self.assertEqual(old["recordedAt"], visible["recordedAt"])
                with patch.object(settings, "TABLE_EXPORT_RECEIVER_MARKER", uuid4()):
                    stale = (await client.get(path)).json()
                    self.assertEqual(stale["declaration"]["state"], "stale")
                    rebound = await client.put(ENDPOINT, json={"name": "Different binding",
                                                              "expectedUpdatedAt": renamed["updatedAt"]})
                    self.assertEqual(rebound.status_code, 200, rebound.text)
                    different = (await client.get(path)).json()
                self.assertTrue(all(r["reviewRelation"] == "different_binding" for r in different["receipts"]))
                self.assertFalse(different["observed"])
                self.assertEqual((lookup.await_count, target.await_count), (2, 2))  # Evidence never opens receiver.
