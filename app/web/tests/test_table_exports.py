"""Outbound sender contracts; two-database receipt integration is tested separately."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from importlib import import_module
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
import httpx
from pydantic import SecretStr, ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError

from app.storage.postgres_client import PostgresClient
from app.web.api.dependencies.common import get_current_user
from app.web.api.dependencies.session import get_db
from app.web.api.models.table_exports import TableExportGrantChange, TableExportReview
from app.web.api.routes.etl import router
from app.web.core.config import settings
from app.web.services import table_export_snapshot, table_exports
from app.web.services.table_export_snapshot_receiver import SnapshotReceiverRow
from app.web.services.table_export_receiver import ReceiverFailure, ReceiverReceipt

URL = os.environ.get("ONTOLOGY_TABLE_EXPORT_TEST_URL")
DATABASE = "ontology_table_export_regression"
SECONDARY_URL = os.environ.get("ONTOLOGY_TABLE_EXPORT_SECONDARY_TEST_URL")
SECONDARY_DATABASE = "ontology_table_export_secondary_regression"
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

    def test_grant_disallows_unclassified_and_wildcard_markings(self):
        base = {"approved": True, "expectedPlanUpdatedAt": datetime.now(timezone.utc),
                "expectedGeneration": None, "reason": "Synthetic pilot"}
        for marking in ("unclassified", "*", "internal", ""):
            with self.subTest(marking=marking), self.assertRaises(ValidationError):
                TableExportGrantChange.model_validate({**base, "marking": marking})

    async def test_disabled_and_reader_gates(self):
        with patch.object(settings, "TABLE_EXPORT_SANDBOX_ENABLED", False):
            async with api(True) as client:
                self.assertFalse((await client.get(ENDPOINT + "/capabilities")).json()["enabled"])
                self.assertFalse((await client.get(ENDPOINT + "/capabilities?destination=secondary")).json()["enabled"])
                for invalid in ("arbitrary", "PRIMARY", "primary&destination=secondary"):
                    self.assertEqual((await client.get(ENDPOINT + "/capabilities?destination=" + invalid)).status_code, 422)
                self.assertEqual((await client.post(ENDPOINT + "/runs", json={
                    "requestId": str(uuid4()), "expectedUpdatedAt": datetime.now(timezone.utc).isoformat(),
                    "expectedGrantGeneration": 1,
                })).status_code, 403)
            async with api(False) as client:
                for route in ("", "/capabilities", "/grant", "/grant/history", "/runs", "/attempts", "/lineage"):
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
        migrations = (
            import_module("app.web.alembic.versions.b31c72f6a9de_ontology_table_exports"),
            import_module("app.web.alembic.versions.aa4c7d2e1f90_ontology_table_export_grants"),
            import_module("app.web.alembic.versions.ad6207fa991b_ontology_table_export_destinations"),
            import_module("app.web.alembic.versions.849ba892a412_ontology_table_export_append"),
        )
        async with self.pg.session() as session:
            def upgrade(sync_session):
                with Operations.context(MigrationContext.configure(sync_session.connection())):
                    for migration in migrations:
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
            await session.execute(text("DROP TABLE ontology_table_export_grants"))
            await session.execute(text("DROP FUNCTION IF EXISTS public.guard_table_export_write_mode()"))
            await session.execute(text("DROP SCHEMA ts_table_export_sandbox CASCADE"))

    async def provider(self):
        async with self.pg.session() as session:
            yield session

    async def test_snapshot_preview_proves_local_source_then_returns_counts_only(self):
        endpoint = "/etl/table-export-sandbox/snapshot-preview"
        revision, target_marker = uuid4(), uuid4()
        sender_port = make_url(URL).port or 5432
        receiver_port = next(port for port in (16532, 16533, 16534)
                             if port not in {sender_port, settings.TABLE_EXPORT_RECEIVER_PORT,
                                             settings.TABLE_EXPORT_SECONDARY_RECEIVER_PORT})
        await self.pg.execute("""
            INSERT INTO ts_table_export_sandbox.records VALUES(2,'synthetic change'), (-9223372036854775808,'source only')
        """)
        with patch.multiple(
            settings, TABLE_EXPORT_SNAPSHOT_PREVIEW_ENABLED=True, TABLE_EXPORT_SNAPSHOT_PREVIEW_CONFIG_REVISION=revision,
            TABLE_EXPORT_SNAPSHOT_PREVIEW_HOST="127.0.0.1", TABLE_EXPORT_SNAPSHOT_PREVIEW_PORT=receiver_port,
            TABLE_EXPORT_SNAPSHOT_PREVIEW_DATABASE="isolated_snapshot", TABLE_EXPORT_SNAPSHOT_PREVIEW_USERNAME="snapshot_ro",
            TABLE_EXPORT_SNAPSHOT_PREVIEW_PASSWORD=SecretStr("never-return-the-password"),
            TABLE_EXPORT_SNAPSHOT_PREVIEW_MARKER=target_marker, TABLE_EXPORT_SNAPSHOT_PREVIEW_PROOF_SHA256="1" * 64,
        ), patch.object(table_export_snapshot, "read_snapshot_preview", new_callable=AsyncMock) as receiver:
            receiver.return_value = (SnapshotReceiverRow(1, "synthetic source"), SnapshotReceiverRow(2, None),
                                     SnapshotReceiverRow(2**63 - 1, "target only"))
            async with api(True, self.provider) as client:
                caps = await client.get(endpoint)
                self.assertEqual(caps.status_code, 200, caps.text)
                self.assertEqual(caps.headers["cache-control"], "private, no-store")
                self.assertEqual(set(caps.json()), {"available", "configRevision"})
                self.assertTrue(caps.json()["available"])
                self.assertNotEqual(caps.json()["configRevision"], str(revision))
                receiver.assert_not_awaited()
                request = {"expectedConfigRevision": caps.json()["configRevision"]}
                for malformed in ({**request, "destination": "primary"}, {}, {"expectedConfigRevision": "bad"}):
                    self.assertEqual((await client.post(endpoint, json=malformed)).status_code, 422)
                self.assertEqual((await client.post(endpoint + "?mode=upsert", json=request)).status_code, 422)
                async with api(False, self.provider) as reader:
                    self.assertEqual((await reader.get(endpoint)).status_code, 403)
                    self.assertEqual((await reader.post(endpoint, json=request)).status_code, 403)
                async with self.pg.session() as session:
                    sender = table_export_snapshot._sender_scope(session)
                with patch.object(table_export_snapshot, "_read_source", side_effect=AssertionError("read sender rows")):
                    relocated = replace(sender, database="other_sender")
                    with patch.object(table_export_snapshot, "_sender_scope", return_value=relocated):
                        self.assertEqual((await client.post(endpoint, json=request)).status_code, 409)
                        false_token = table_export_snapshot._snapshot_revision(
                            table_export_snapshot.configured_snapshot_profile(), relocated, SOURCE_MARKER,
                        )
                        self.assertFalse((await client.get(endpoint)).json()["available"])
                        self.assertEqual((await client.post(endpoint, json={"expectedConfigRevision": str(false_token)})).status_code,
                                         403)
                    with patch.object(settings, "TABLE_EXPORT_SNAPSHOT_PREVIEW_PROOF_SHA256", "2" * 64):
                        self.assertEqual((await client.post(endpoint, json=request)).status_code, 409)
                    with patch.object(settings, "TABLE_EXPORT_SOURCE_MARKER", uuid4()):
                        wrong = table_export_snapshot._snapshot_revision(
                            table_export_snapshot.configured_snapshot_profile(), sender, settings.TABLE_EXPORT_SOURCE_MARKER,
                        )
                        self.assertFalse((await client.get(endpoint)).json()["available"])
                        self.assertEqual((await client.post(endpoint, json={"expectedConfigRevision": str(wrong)})).status_code, 403)
                    with patch.object(settings, "TABLE_EXPORT_SNAPSHOT_PREVIEW_PORT", sender_port):
                        alias = table_export_snapshot._snapshot_revision(
                            table_export_snapshot.configured_snapshot_profile(), sender, SOURCE_MARKER,
                        )
                        self.assertFalse((await client.get(endpoint)).json()["available"])
                        self.assertEqual((await client.post(endpoint, json={"expectedConfigRevision": str(alias)})).status_code, 403)
                receiver.assert_not_awaited()

                compared = await client.post(endpoint, json=request)
                self.assertEqual(compared.status_code, 200, compared.text)
                self.assertEqual(compared.json(), dict(configRevision=request["expectedConfigRevision"],
                                                      sourceRows=3, targetRows=3, added=1, changed=1,
                                                      unchanged=1, wouldRemove=1))
                receiver.assert_awaited_once_with(table_export_snapshot.configured_snapshot_profile(),
                                                  sender_port=sender_port, sender_marker=SOURCE_MARKER,
                                                  sender_database=sender.database, sender_username=sender.username)
                for secret in ("127.0.0.1", "snapshot_ro", str(target_marker), "source only", "target only",
                               "never-return-the-password"):
                    self.assertNotIn(secret, compared.text)
                receiver.reset_mock()
                await self.pg.execute("TRUNCATE ts_table_export_sandbox.records")
                self.assertEqual((await client.post(endpoint, json=request)).status_code, 422)
                await self.pg.execute("INSERT INTO ts_table_export_sandbox.records SELECT n, repeat('x',8192) FROM generate_series(1,8) n")
                original_execute = AsyncSession.execute

                async def deny_payload(session, statement, *args, **kwargs):
                    sql = str(statement)
                    if sql.lstrip().startswith("SELECT id, CASE") and "ts_table_export_sandbox.records" in sql:
                        raise AssertionError("Source captions were materialized beyond 64 KiB")
                    return await original_execute(session, statement, *args, **kwargs)

                with patch.object(AsyncSession, "execute", deny_payload):
                    sized = await client.post(endpoint, json=request)
                    self.assertEqual(sized.status_code, 422, sized.text)
                receiver.assert_not_awaited()
                for table in ("plan", "grants", "requests", "attempts", "receipts"):
                    self.assertEqual(await self.pg.fetch_all(f"SELECT 1 FROM ontology_table_export_{table}"), [])

    @unittest.skipUnless(SECONDARY_URL, "Requires a separate disposable loopback receiver")
    async def test_secondary_real_commit_and_lost_ack_never_use_primary(self):
        url = make_url(SECONDARY_URL)
        if (url.database != SECONDARY_DATABASE or url.host != "127.0.0.1" or not url.port
                or url.port == make_url(URL).port):
            raise RuntimeError("Receiver fixture refuses any endpoint except its separate named loopback database")
        target = PostgresClient(SECONDARY_URL)
        await target.connect(max_retries=1)
        self.addAsyncCleanup(target.close)
        self.assertEqual((await target.fetch_one("SELECT current_database() AS name"))["name"], SECONDARY_DATABASE)
        marker = uuid4()
        async with target.session() as session:
            await session.execute(text("CREATE SCHEMA ts_table_export_receiver"))
            await session.execute(text("CREATE TABLE ts_table_export_receiver.export_receiver_identity (marker uuid PRIMARY KEY)"))
            await session.execute(text("CREATE TABLE ts_table_export_receiver.records (id bigint PRIMARY KEY, caption text NOT NULL)"))
            await session.execute(text("""
                CREATE TABLE ts_table_export_receiver.receipts (request_id uuid PRIMARY KEY,
                    plan_updated_at timestamptz NOT NULL, payload_digest text NOT NULL,
                    rows_affected int NOT NULL, committed_at timestamptz NOT NULL)
            """))
            await session.execute(text("INSERT INTO ts_table_export_receiver.export_receiver_identity VALUES (:marker)"),
                                  {"marker": marker})
        self.addAsyncCleanup(target.execute, "DROP SCHEMA ts_table_export_receiver CASCADE")
        revision = uuid4()
        with patch.multiple(
            settings, TABLE_EXPORT_SECONDARY_ENABLED=True, TABLE_EXPORT_SECONDARY_CONFIG_REVISION=revision,
            TABLE_EXPORT_SECONDARY_RECEIVER_HOST=url.host, TABLE_EXPORT_SECONDARY_RECEIVER_PORT=url.port,
            TABLE_EXPORT_SECONDARY_RECEIVER_DATABASE=url.database, TABLE_EXPORT_SECONDARY_RECEIVER_USERNAME=url.username,
            TABLE_EXPORT_SECONDARY_RECEIVER_PASSWORD=SecretStr(url.password or "local-test-only"),
            TABLE_EXPORT_SECONDARY_RECEIVER_MARKER=marker,
        ):
            primary_binding = table_exports._provisioned()[2]
            secondary_binding = table_exports._provisioned("secondary")[2]
            with patch.object(settings, "TABLE_EXPORT_SECONDARY_RECEIVER_PASSWORD", SecretStr("test-rotated")):
                self.assertEqual(secondary_binding, table_exports._provisioned("secondary")[2])
            async with api(True, self.provider) as client:
                secondary = {"destination": "secondary"}
                caps = (await client.get(ENDPOINT + "/capabilities")).json()
                self.assertEqual([option["id"] for option in caps["approvedDestinations"]], ["primary", "secondary"])
                self.assertNotIn("destinationLabel", caps)
                primary = (await client.put(ENDPOINT, json={"name": "Primary", "expectedUpdatedAt": None})).json()
                first = await self.decision(client, primary["updatedAt"])
                self.assertEqual(first.json()["generation"], 1)
                migration = import_module("app.web.alembic.versions.ad6207fa991b_ontology_table_export_destinations")

                append_migration = import_module("app.web.alembic.versions.849ba892a412_ontology_table_export_append")

                def migrated(session, action):
                    with Operations.context(MigrationContext.configure(session.connection())):
                        action()

                # Existing primary review and grant are attributed without rebuilding either.
                async with self.pg.session() as session:
                    await session.run_sync(migrated, append_migration.downgrade)
                    await session.run_sync(migrated, migration.downgrade)
                    await session.run_sync(migrated, migration.upgrade)
                    await session.run_sync(migrated, append_migration.upgrade)
                self.assertEqual((await client.get(ENDPOINT)).json(), primary)
                self.assertEqual((await client.get(ENDPOINT + "/grant")).json(), first.json())
                with patch.object(settings, "TABLE_EXPORT_SECONDARY_RECEIVER_MARKER", uuid4()):
                    bad = await client.put(ENDPOINT, params=secondary, json={"name": "Wrong", "expectedUpdatedAt": None})
                    self.assertEqual(bad.status_code, 422, bad.text)
                    self.assertIsNone((await client.get(ENDPOINT, params=secondary)).json())
                plan = await client.put(ENDPOINT, params=secondary, json={"name": "Secondary", "expectedUpdatedAt": None})
                self.assertEqual(plan.status_code, 200, plan.text)
                version = plan.json()["updatedAt"]
                with self.assertRaisesRegex(RuntimeError, "secondary export evidence"):
                    async with self.pg.session() as session:
                        await session.run_sync(migrated, append_migration.downgrade)
                        await session.run_sync(migrated, migration.downgrade)


                async def secondary_grant(approved, expected):
                    return await client.put(ENDPOINT + "/grant", params=secondary, json={
                        "approved": approved, "expectedPlanUpdatedAt": version, "expectedGeneration": expected,
                        "marking": "sandbox-synthetic", "reason": "Verified isolated fixture",
                    })

                grant = await secondary_grant(True, None)
                self.assertEqual(grant.status_code, 200, grant.text)
                self.assertEqual(grant.json()["generation"], 1)
                request = {"requestId": str(uuid4()), "expectedUpdatedAt": version, "expectedGrantGeneration": 1}

                with patch.object(table_exports, "find_export_receipt",
                                  side_effect=AssertionError("unexpected receiver lookup")) as lookup:
                    await self.pg.execute('UPDATE ts_table_export_sandbox."identity" SET marker=:marker', {"marker": uuid4()})
                    try:
                        bad = await client.post(ENDPOINT + "/runs", params=secondary, json=request)
                        self.assertEqual(bad.status_code, 409, bad.text)
                        self.assertIsNone(await self.pg.fetch_one(
                            "SELECT request_id FROM ontology_table_export_requests WHERE request_id=CAST(:id AS uuid)",
                            {"id": request["requestId"]},
                        ))
                        self.assertEqual((await secondary_grant(False, 1)).status_code, 409)
                        lookup.assert_not_called()
                    finally:
                        await self.pg.execute('UPDATE ts_table_export_sandbox."identity" SET marker=:marker',
                                              {"marker": SOURCE_MARKER})

                original_apply = table_exports.apply_export_rows

                async def lose_ack(config, *args):
                    self.assertEqual(config.marker, marker)
                    await original_apply(config, *args)
                    raise ReceiverFailure(503, "receiver_commit_unknown", "no acknowledgment", "outcome_unknown")

                with patch.object(table_exports, "apply_export_rows", side_effect=lose_ack):
                    unknown = await client.post(ENDPOINT + "/runs", params=secondary, json=request)
                    self.assertEqual(unknown.status_code, 503, unknown.text)
                with patch.object(table_exports, "_source_rows", side_effect=AssertionError("unexpected source read")) as read:
                    denied = await client.post(ENDPOINT + "/runs", json={**request, "expectedUpdatedAt": primary["updatedAt"]})
                    self.assertEqual(denied.status_code, 409, denied.text)
                    recovered = await client.post(ENDPOINT + "/runs", params=secondary, json=request)
                    self.assertEqual(recovered.status_code, 200, recovered.text)
                    read.assert_not_called()
                self.assertEqual(recovered.json()["rowsAffected"], 1)
                self.assertEqual((await client.get(ENDPOINT + "/runs")).json(), [])
                self.assertEqual((await client.get(ENDPOINT + "/attempts")).json(), [])
                self.assertFalse((await client.get(ENDPOINT + "/lineage")).json()["observed"])
                self.assertTrue((await client.get(ENDPOINT + "/lineage", params=secondary)).json()["observed"])
                self.assertEqual((await target.fetch_one("SELECT caption FROM ts_table_export_receiver.records WHERE id=1"))
                                 ["caption"], "synthetic source")
                self.assertEqual((await target.fetch_one("SELECT count(*) AS n FROM ts_table_export_receiver.receipts"))
                                 ["n"], 1)

                with patch.object(settings, "TABLE_EXPORT_SECONDARY_CONFIG_REVISION", uuid4()):
                    self.assertNotEqual(secondary_binding, table_exports._provisioned("secondary")[2])
                    self.assertEqual(primary_binding, table_exports._provisioned()[2])
                    self.assertFalse((await client.get(ENDPOINT + "/grant", params=secondary)).json()["effective"])
                    self.assertTrue((await client.get(ENDPOINT + "/grant")).json()["effective"])

                # Same-name, same-OID TRUNCATE rewrites the reviewed physical table.
                await target.execute("TRUNCATE ts_table_export_receiver.records")
                with patch.object(table_exports, "_source_rows", side_effect=AssertionError("unexpected source read")) as read:
                    rejected = await client.post(ENDPOINT + "/runs", params=secondary,
                                                 json={**request, "requestId": str(uuid4())})
                    self.assertEqual(rejected.status_code, 422, rejected.text)
                    read.assert_not_called()
                self.assertEqual((await secondary_grant(False, 1)).json()["generation"], 2)
                self.assertEqual((await client.get(ENDPOINT + "/runs", params=secondary)).json(), [])
                self.assertTrue((await client.get(ENDPOINT + "/grant")).json()["effective"])

    @unittest.skipUnless(SECONDARY_URL, "Requires a separate disposable loopback receiver")
    async def test_explicit_primary_append_atomically_rejects_duplicate_and_recovers_exact_prior_off(self):
        url = make_url(SECONDARY_URL)
        if (url.database != SECONDARY_DATABASE or url.host != "127.0.0.1" or not url.port
            or url.port == make_url(URL).port):
            raise RuntimeError("Use a distinct disposable PostgreSQL receiver")
        target = PostgresClient(SECONDARY_URL)
        await target.connect(max_retries=1)
        self.addAsyncCleanup(target.close)
        self.assertEqual((await target.fetch_one("SELECT current_database() AS name"))["name"], SECONDARY_DATABASE)
        marker = uuid4()
        async with target.session() as session:
            for sql in (
                "CREATE SCHEMA ts_table_export_receiver",
                "CREATE TABLE ts_table_export_receiver.export_receiver_identity (marker uuid PRIMARY KEY)",
                "CREATE TABLE ts_table_export_receiver.records (id bigint PRIMARY KEY, caption text NOT NULL)",
                """CREATE TABLE ts_table_export_receiver.receipts (request_id uuid PRIMARY KEY,
                    plan_updated_at timestamptz NOT NULL, payload_digest text NOT NULL,
                    rows_affected int NOT NULL, committed_at timestamptz NOT NULL)""",
                "INSERT INTO ts_table_export_receiver.records VALUES (2, 'must stay unchanged')",
            ):
                await session.execute(text(sql))
            await session.execute(text("INSERT INTO ts_table_export_receiver.export_receiver_identity VALUES (:marker)"),
                                  {"marker": marker})
        self.addAsyncCleanup(target.execute, "DROP SCHEMA ts_table_export_receiver CASCADE")
        await self.pg.execute("INSERT INTO ts_table_export_sandbox.records VALUES (2, 'new source caption')")
        with patch.multiple(
            settings, TABLE_EXPORT_APPEND_ENABLED=True,
            TABLE_EXPORT_RECEIVER_HOST=url.host, TABLE_EXPORT_RECEIVER_PORT=url.port,
            TABLE_EXPORT_RECEIVER_DATABASE=url.database, TABLE_EXPORT_RECEIVER_USERNAME=url.username,
            TABLE_EXPORT_RECEIVER_PASSWORD=SecretStr(url.password or "local-test-only"), TABLE_EXPORT_RECEIVER_MARKER=marker,
        ):
            async with api(True, self.provider) as client:
                self.assertTrue((await client.get(ENDPOINT + "/capabilities")).json()["appendAvailable"])
                plan = await client.put(ENDPOINT, json={"name": "Exact append", "expectedUpdatedAt": None, "writeMode": "append"})
                self.assertEqual(plan.status_code, 200, plan.text)
                version = plan.json()["updatedAt"]
                self.assertEqual(plan.json()["writeMode"], "append")
                self.assertEqual((await client.put(ENDPOINT, json={"name": "Old caller", "expectedUpdatedAt": version}))
                                 .status_code, 409)
                grant = {"approved": True, "expectedPlanUpdatedAt": version, "expectedGeneration": None,
                         "marking": "sandbox-synthetic", "reason": "Review exact Append mode"}
                self.assertEqual((await client.put(ENDPOINT + "/grant", json=grant)).status_code, 409)
                old_shaped = (
                    """INSERT INTO ontology_table_export_grants
                         (destination,generation,id,action,marking,reason,actor_id,scope_digest,plan_updated_at,recorded_at)
                       SELECT destination,1,:id,'approved','sandbox-synthetic','old binary',reviewed_by,
                              binding_digest,updated_at,clock_timestamp() FROM ontology_table_export_plan""",
                    """INSERT INTO ontology_table_export_requests
                         (destination,request_id,plan_updated_at,binding_digest)
                       SELECT destination,:id,updated_at,binding_digest FROM ontology_table_export_plan""",
                )
                for sql in old_shaped:
                    with self.assertRaisesRegex(DBAPIError, "Export admission mode or revision"):
                        await self.pg.execute(sql, {"id": uuid4()})
                approved = await client.put(ENDPOINT + "/grant", json={**grant, "writeMode": "append"})
                self.assertEqual(approved.status_code, 200, approved.text)
                self.assertEqual(approved.json()["writeMode"], "append")
                request = {"requestId": str(uuid4()), "expectedUpdatedAt": version,
                           "expectedGrantGeneration": 1, "writeMode": "append"}
                self.assertEqual((await client.post(ENDPOINT + "/runs", json={**request, "writeMode": "upsert"})).status_code, 409)
                duplicate = await client.post(ENDPOINT + "/runs", json=request)
                self.assertEqual(duplicate.status_code, 409, duplicate.text)
                self.assertEqual(duplicate.json()["detail"]["code"], "append_duplicate")
                self.assertEqual([tuple(row.values()) for row in await target.fetch_all(
                    "SELECT id,caption FROM ts_table_export_receiver.records ORDER BY id")], [(2, "must stay unchanged")])
                self.assertEqual((await target.fetch_one("SELECT count(*) AS n FROM ts_table_export_receiver.receipts"))["n"], 0)
                attempts = (await client.get(ENDPOINT + "/attempts")).json()
                self.assertEqual((attempts[0]["status"], attempts[0]["writeMode"], attempts[0]["code"]),
                                 ("rejected", "append", "append_duplicate"))

                # A concurrent/later identical request cannot become admitted after the key disappears.
                await target.execute("DELETE FROM ts_table_export_receiver.records WHERE id=2")
                with patch.object(table_exports, "find_export_receipt", side_effect=AssertionError("terminal request probed receiver")):
                    self.assertEqual((await client.post(ENDPOINT + "/runs", json=request)).json(), duplicate.json())

                new = {**request, "requestId": str(uuid4())}
                original = table_exports.apply_export_rows

                async def lose_ack(*args, **kwargs):
                    self.assertEqual(kwargs, {"append": True})
                    await original(*args, **kwargs)
                    raise ReceiverFailure(503, "receiver_commit_unknown", "isolated fixture", "outcome_unknown")

                with patch.object(table_exports, "apply_export_rows", side_effect=lose_ack):
                    self.assertEqual((await client.post(ENDPOINT + "/runs", json=new)).status_code, 503)
                with patch.object(settings, "TABLE_EXPORT_APPEND_ENABLED", False), patch.object(
                    table_exports, "_source_rows", side_effect=AssertionError("unexpected source read while disabled"),
                ):
                    self.assertNotIn("appendAvailable", (await client.get(ENDPOINT + "/capabilities")).json())
                    recovered = await client.post(ENDPOINT + "/runs", json=new)
                    self.assertEqual(recovered.status_code, 200, recovered.text)
                    self.assertEqual((recovered.json()["rowsAffected"], recovered.json()["writeMode"]), (2, "append"))
                    self.assertEqual((await client.post(ENDPOINT + "/runs", json={**new, "requestId": str(uuid4())}))
                                     .status_code, 403)
                self.assertEqual((await target.fetch_one("SELECT count(*) AS n FROM ts_table_export_receiver.receipts"))["n"], 1)
                self.assertEqual((await client.get(ENDPOINT + "/runs")).json(), [recovered.json()])
                self.assertEqual((await client.get(ENDPOINT + "/lineage")).json()["latestObservation"]["writeMode"], "append")
                for relation in ("ontology_table_export_requests", "ontology_table_export_grants"):
                    with self.assertRaisesRegex(DBAPIError, "Export admission scope is immutable"):
                        await self.pg.execute(f"UPDATE {relation} SET write_mode='upsert'")
                switch = await client.put(ENDPOINT, json={
                    "name": "Explicit Upsert", "expectedUpdatedAt": version, "writeMode": "upsert",
                })
                self.assertEqual(switch.status_code, 200, switch.text)
                self.assertNotIn("writeMode", switch.json())
                self.assertEqual((await client.get(ENDPOINT + "/grant")).json()["status"], "stale")
                revoke = await client.put(ENDPOINT + "/grant", json={
                    **grant, "approved": False, "expectedGeneration": 1, "writeMode": "upsert",
                    "expectedPlanUpdatedAt": switch.json()["updatedAt"],
                })
                self.assertEqual(revoke.status_code, 200, revoke.text)
                history = (await client.get(ENDPOINT + "/grant/history")).json()
                self.assertEqual([(row["action"], row["writeMode"]) for row in history],
                                 [("revoked", "append"), ("approved", "append")])

        migration = import_module("app.web.alembic.versions.849ba892a412_ontology_table_export_append")
        def reject_downgrade(sync_session):
            with Operations.context(MigrationContext.configure(sync_session.connection())):
                migration.downgrade()

        with self.assertRaisesRegex(RuntimeError, "Cannot remove reviewed Append"):
            async with self.pg.session() as session:
                await session.run_sync(reject_downgrade)

    async def decision(self, client, version, *, approved=True, generation=None):
        return await client.put(ENDPOINT + "/grant", json={
            "approved": approved, "expectedPlanUpdatedAt": version, "expectedGeneration": generation,
            "marking": "sandbox-synthetic", "reason": "Verified synthetic receiver and projection",
        })

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
                self.assertEqual((await self.decision(client, version)).status_code, 200)

                async def run(key):
                    return await client.post(ENDPOINT + "/runs", json={
                        "requestId": str(key), "expectedUpdatedAt": version, "expectedGrantGeneration": 1,
                    })

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
            self.assertEqual((await self.decision(client, review["updatedAt"])).status_code, 200)
            with (patch("app.web.services.table_exports.find_export_receipt", side_effect=rogue),
                  patch("app.web.services.table_exports._source_rows") as source):
                response = await client.post(ENDPOINT + "/runs", json={
                    "requestId": str(uuid4()), "expectedUpdatedAt": review["updatedAt"], "expectedGrantGeneration": 1,
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
            self.assertEqual((await client.get(path)).json()["declaration"]["state"], "blocked")
            self.assertEqual((await self.decision(client, version)).status_code, 200)
            self.assertEqual((await client.get(path)).json()["declaration"]["state"], "enabled")
            with (
                patch("app.web.services.table_exports.find_export_receipt", return_value=None) as lookup,
                patch("app.web.services.table_exports.apply_export_rows", side_effect=receive) as target,
            ):
                async def export():
                    response = await client.post(ENDPOINT + "/runs", json={
                        "requestId": str(uuid4()), "expectedUpdatedAt": version, "expectedGrantGeneration": 1,
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
                self.assertEqual(history["receipts"], [])
                self.assertEqual(history["declaration"]["state"], "blocked")
                retained = await self.pg.fetch_one("SELECT recorded_at FROM ontology_table_export_receipts WHERE id=:id",
                                                   {"id": visible["id"]})
                self.assertEqual(retained["recorded_at"], recorded)
                with patch.object(settings, "TABLE_EXPORT_RECEIVER_MARKER", uuid4()):
                    stale = (await client.get(path)).json()
                    self.assertEqual(stale["declaration"]["state"], "stale")
                    rebound = await client.put(ENDPOINT, json={"name": "Different binding",
                                                              "expectedUpdatedAt": renamed["updatedAt"]})
                    self.assertEqual(rebound.status_code, 200, rebound.text)
                    different = (await client.get(path)).json()
                self.assertEqual(different["receipts"], [])
                self.assertFalse(different["observed"])
                self.assertEqual((lookup.await_count, target.await_count), (2, 2))  # Evidence never opens receiver.

    async def test_revocation_serializes_with_admitted_run_and_fences_future_contact(self):
        admitted = asyncio.Event()
        release = asyncio.Event()
        version: str
        key = str(uuid4())

        async def receive(config, request_id, plan_version, digest, rows):
            admitted.set()
            await release.wait()
            return ReceiverReceipt(request_id, plan_version, digest, len(rows), datetime.now(timezone.utc))

        async with api(True, self.provider) as client:
            review = await client.put(ENDPOINT, json={"name": "Synthetic pilot", "expectedUpdatedAt": None})
            version = review.json()["updatedAt"]

            async def run(generation, request_id=key):
                return await client.post(ENDPOINT + "/runs", json={
                    "requestId": request_id, "expectedUpdatedAt": version, "expectedGrantGeneration": generation,
                })

            with (
                patch("app.web.services.table_exports.find_export_receipt", return_value=None) as lookup,
                patch("app.web.services.table_exports.apply_export_rows", side_effect=receive) as receiver,
                patch("app.web.services.table_exports.read_table_export_source",
                      return_value=[(1, "synthetic source")]) as source,
            ):
                self.assertEqual((await run(1)).json()["detail"]["code"], "grant_required")
                self.assertEqual((lookup.await_count, source.await_count, receiver.await_count), (0, 0, 0))
                self.assertEqual((await self.decision(client, version)).json()["generation"], 1)
                task = asyncio.create_task(run(1))
                await asyncio.wait_for(admitted.wait(), timeout=5)
                try:
                    attempted = await self.decision(client, version, approved=False, generation=1)
                    self.assertEqual(attempted.status_code, 409, attempted.text)
                    self.assertEqual(attempted.json()["detail"]["code"], "target_busy")
                finally:
                    release.set()
                settled = await asyncio.wait_for(task, timeout=5)
                self.assertEqual(settled.status_code, 200, settled.text)
                self.assertEqual(settled.json()["grantGeneration"], 1)

                with patch.object(settings, "TABLE_EXPORT_RECEIVER_MARKER", uuid4()):
                    grant = (await client.get(ENDPOINT + "/grant")).json()
                    self.assertEqual(grant["status"], "stale")
                    self.assertFalse(grant["available"])
                    self.assertEqual((await run(1)).json()["detail"]["code"], "grant_required")
                    revoked = await self.decision(client, version, approved=False, generation=1)
                self.assertEqual(revoked.json()["status"], "revoked")
                self.assertEqual(revoked.json()["generation"], 2)
                self.assertEqual((await run(1)).json()["detail"]["code"], "grant_required")
                self.assertEqual((await client.get(ENDPOINT + "/runs")).json(), [])
                self.assertEqual((await client.get(ENDPOINT + "/attempts")).json(), [])
                self.assertEqual((await client.get(ENDPOINT + "/lineage")).json()["receipts"], [])
                self.assertIsNotNone(await self.pg.fetch_one(
                    "SELECT id FROM ontology_table_export_receipts WHERE request_id=:id", {"id": key},
                ))

                legacy = str(uuid4())
                await self.pg.execute(
                    "INSERT INTO ontology_table_export_requests(request_id, plan_updated_at, binding_digest) "
                    "SELECT CAST(:id AS uuid), updated_at, binding_digest FROM ontology_table_export_plan", {"id": legacy},
                )
                self.assertEqual((await self.decision(client, version, generation=2)).json()["generation"], 3)
                self.assertEqual((await run(1)).json()["detail"]["code"], "grant_changed")
                self.assertEqual((await run(3)).json()["detail"]["code"], "request_grant_changed")
                self.assertEqual((await run(3, legacy)).json()["detail"]["code"], "request_grant_changed")
                self.assertEqual((lookup.await_count, source.await_count, receiver.await_count), (1, 1, 1))
                audit = (await client.get(ENDPOINT + "/grant/history")).json()
                self.assertEqual([(item["generation"], item["action"]) for item in audit],
                                 [(3, "approved"), (2, "revoked"), (1, "approved")])
                self.assertNotIn("scope_digest", str(audit))
                self.assertNotIn(key, str(audit))
