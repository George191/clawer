"""Local batch-sync contracts and isolated target transaction/concurrency tests."""

import asyncio
from datetime import datetime, timedelta, timezone
from importlib import import_module
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
import httpx
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.storage.postgres_client import PostgresClient
from app.web.api.dependencies.common import get_current_user
from app.web.api.dependencies.session import get_db
from app.web.api.models.batch_syncs import BatchSyncPayload
from app.web.api.routes.etl import router
from app.web.core.config import settings
from app.web.services.batch_sync_schedules import collect_due_batch_sync_jobs, execute_batch_sync_schedule_job
from tools.ontology_data_connections import migrate


URL = os.environ.get("ONTOLOGY_BATCH_SYNC_TEST_URL")
IDENTITY_ORIGINS = [os.environ.get("ONTOLOGY_BATCH_SOURCE_TEST_ORIGIN_" + suffix) for suffix in ("A", "B")]
DATABASE = "ontology_batch_sync_regression"
ACTOR = uuid4()
RUNS = "/etl/batch-syncs"
PAYLOAD = {
    "name": "Isolated Accounts",
    "connectionId": str(uuid4()),
    "expectedSourceUpdatedAt": datetime(2026, 11, 17, tzinfo=timezone.utc).isoformat(),
    "sourceSchema": "Source Catalog",
    "sourceTable": "Account Data",
    "sourceKeyColumn": "Source Identifier",
    "targetTable": "accounts",
    "mapping": [{"sourceColumn": "Source Identifier", "targetColumn": "id"},
                {"sourceColumn": "Display Name", "targetColumn": "caption"}],
}
PROJECTION = [
    {"name": "Source Identifier", "typeOid": 23, "typmod": -1, "nullable": False, "primaryKey": True},
    {"name": "Display Name", "typeOid": 25, "typmod": -1, "nullable": False, "primaryKey": False},
]


def api(is_admin, provider=lambda: None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(is_superuser=is_admin, id=ACTOR)
    app.dependency_overrides[get_db] = provider
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class BatchSyncContracts(unittest.IsolatedAsyncioTestCase):
    def test_keys_unique_columns_and_reserved_target_schema(self):
        BatchSyncPayload.model_validate(PAYLOAD)
        for alteration in (
            {"sourceSchema": "ts_batch_sync_sandbox"}, {"sourceKeyColumn": "Unmapped"},
            {"targetTable": "accounts; unexpectedly unsafe"},
            {"mapping": [PAYLOAD["mapping"][0], PAYLOAD["mapping"][0]]},
        ):
            with self.subTest(alteration=alteration), self.assertRaises(ValidationError):
                BatchSyncPayload.model_validate({**PAYLOAD, **alteration})

    async def test_capability_default_off_and_admin_gate(self):
        with patch.object(settings, "BATCH_SYNC_SANDBOX_ENABLED", False):
            async with api(True) as client:
                response = await client.get(f"{RUNS}/capabilities")
                self.assertEqual(response.json(), {"enabled": False, "targetSchema": "ts_batch_sync_sandbox",
                                                   "maxRows": 100, "maxBytes": 1048576, "scheduledBatchSync": False})
                self.assertEqual((await client.post(f"{RUNS}/{uuid4()}/runs", json={
                    "requestId": str(uuid4()), "expectedUpdatedAt": PAYLOAD["expectedSourceUpdatedAt"],
                })).status_code, 403)
            async with api(False) as client:
                self.assertEqual((await client.get(RUNS)).status_code, 403)
                self.assertEqual((await client.get(f"{RUNS}/capabilities")).status_code, 403)
                self.assertEqual((await client.get(f"{RUNS}/{uuid4()}/attempts")).status_code, 403)
                self.assertEqual((await client.get("/etl/batch-sync-schedules")).status_code, 403)
                self.assertEqual((await client.get(f"{RUNS}/{uuid4()}/schedule")).status_code, 403)


@unittest.skipUnless(URL, "Requires explicitly isolated ontology_batch_sync_regression PostgreSQL")
class BatchSyncIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if make_url(URL).database != DATABASE:
            raise RuntimeError("Batch-sync fixture refuses other databases")
        self.pg = PostgresClient(URL)
        await self.pg.connect(max_retries=1)
        self.addAsyncCleanup(self.pg.close)
        if (await self.pg.fetch_one("SELECT current_database() AS name"))["name"] != DATABASE:
            raise RuntimeError("Connected to a non-fixture database")
        self.migration = import_module("app.web.alembic.versions.3c0db1c2a19e_ontology_batch_syncs")
        self.attempts_migration = import_module("app.web.alembic.versions.70ac419e8121_ontology_batch_sync_attempts")
        self.schedules_migration = import_module("app.web.alembic.versions.a83a40b7dc19_ontology_batch_sync_schedules")
        identity_migration = import_module("app.web.alembic.versions.0e653c2f6a91_ontology_batch_source_identity")
        incremental_migration = import_module("app.web.alembic.versions.b8e42d47136f_ontology_batch_sync_incremental")
        self.range_migration = import_module("app.web.alembic.versions.b86d2b1d4e98_ontology_batch_snapshot_range")
        lineage_migration = import_module("app.web.alembic.versions.d2fb47b91a6e_ontology_committed_batch_timeline")
        await migrate(self.pg, apply=True)
        async with self.pg.session() as session:
            def upgrade(sync_session):
                with Operations.context(MigrationContext.configure(sync_session.connection())):
                    self.migration.upgrade()
                    self.attempts_migration.upgrade()
                    self.schedules_migration.upgrade()
                    identity_migration.upgrade()
                    incremental_migration.upgrade()
                    lineage_migration.upgrade()
                    self.range_migration.upgrade()

            await session.run_sync(upgrade)
            await session.execute(text("CREATE SCHEMA ts_batch_sync_sandbox"))
            await session.execute(text("CREATE TABLE ts_batch_sync_sandbox.accounts (id integer PRIMARY KEY, caption text NOT NULL)"))
            await session.execute(text("INSERT INTO ts_batch_sync_sandbox.accounts VALUES(1,'old'),(77,'untouched')"))
            await session.execute(text("""
                INSERT INTO public.ontology_data_connections
                (id,name,engine,config,credentials_ciphertext,created_by,tested_at,updated_at)
                VALUES (:id,'fake local source','postgresql','{"host":"127.0.0.1"}','never read by this fixture','test',now(),:version)
            """), {"id": UUID(PAYLOAD["connectionId"]), "version": datetime.fromisoformat(PAYLOAD["expectedSourceUpdatedAt"])})
        self.addAsyncCleanup(self.drop_fixture)

    async def drop_fixture(self):
        async with self.pg.session() as session:
            for command in (
                "DROP TABLE public.ontology_batch_sync_attempts, public.ontology_batch_sync_requests, "
                "public.ontology_batch_sync_schedule_jobs, public.ontology_batch_sync_schedules",
                "DROP TABLE public.ontology_batch_sync_receipts, public.ontology_batch_sync_plans",
                "DROP TABLE public.ontology_data_connections, public.ontology_batch_source_identities, "
                "public.ontology_batch_sync_incremental_watermark",
                "DROP SCHEMA ts_batch_sync_sandbox CASCADE",
            ):
                await session.execute(text(command))

    async def provider(self):
        async with self.pg.session() as session:
            yield session

    async def test_same_target_lock_before_source_read_and_atomic_receipt(self):
        async with api(True, self.provider) as client:
            response = await client.post(RUNS, json=PAYLOAD)
            self.assertEqual(response.status_code, 201, response.text)
            plan = response.json()
            response = await client.post(RUNS, json={**PAYLOAD, "name": "Other draft, same physical target"})
            self.assertEqual(response.status_code, 201, response.text)
            rival = response.json()
            self.assertEqual(len((await client.get(RUNS, params={"connectionId": PAYLOAD["connectionId"]})).json()), 2)

            requested = asyncio.Event()
            snapshot_allowed = asyncio.Event()

            async def reader(*args, **kwargs):
                requested.set()
                await snapshot_allowed.wait()
                return [
                    {"Source Identifier": 1, "Display Name": "changed"},
                    {"Source Identifier": 2, "Display Name": "inserted"},
                ], PROJECTION

            def submit(plan, request_id=None):
                return client.post(f"{RUNS}/{plan['id']}/runs", json={
                    "requestId": request_id or str(uuid4()), "expectedUpdatedAt": plan["updatedAt"],
                })

            with (
                patch.object(settings, "ENVIRONMENT", "local"),
                patch.object(settings, "BATCH_SYNC_SANDBOX_ENABLED", True),
                patch("app.web.services.batch_syncs.read_batch_sync_snapshot", side_effect=reader) as observed,
            ):
                request_id = str(uuid4())
                first = asyncio.create_task(submit(plan, request_id))
                try:
                    await asyncio.wait_for(requested.wait(), timeout=5)
                    started = (await client.get(f"{RUNS}/{plan['id']}/attempts")).json()
                    self.assertEqual([(item["status"], item["receiptId"]) for item in started], [("started", None)])
                    blocked = await submit(rival)
                    self.assertEqual(blocked.status_code, 409, blocked.text)
                    self.assertEqual(blocked.json()["detail"]["code"], "target_busy")
                    rival_history = (await client.get(f"{RUNS}/{rival['id']}/attempts")).json()
                    self.assertEqual([(i["status"], i["code"]) for i in rival_history], [("retryable", "target_busy")])
                    self.assertEqual(observed.await_count, 1)
                finally:
                    snapshot_allowed.set()
                response = await first
                self.assertEqual(response.status_code, 200, response.text)
                receipt = response.json()
                self.assertEqual(receipt["rowsAffected"], 2)
                rows = await self.pg.fetch_all("SELECT id,caption FROM ts_batch_sync_sandbox.accounts ORDER BY id")
                self.assertEqual([(row["id"], row["caption"]) for row in rows],
                                 [(1, "changed"), (2, "inserted"), (77, "untouched")])
                self.assertEqual((await submit(plan, request_id)).json(), receipt)
                history = (await client.get(f"{RUNS}/{plan['id']}/attempts")).json()
                self.assertEqual([(i["status"], i["receiptId"]) for i in history],
                                 [("replayed", receipt["id"]), ("committed", receipt["id"])])
                self.assertEqual(observed.await_count, 1)
                self.assertEqual((await client.get(f"{RUNS}/{plan['id']}/runs")).json(), [receipt])
                self.assertEqual((await client.get(f"{RUNS}/{rival['id']}/runs")).json(), [])

                # A connection edit blocks new requests but not the replay of an
                # already-committed request; neither case reopens the source.
                async with self.pg.session() as session:
                    await session.execute(text("UPDATE public.ontology_data_connections SET updated_at=clock_timestamp()"))
                stale_source = await submit(plan)
                self.assertEqual(stale_source.status_code, 409, stale_source.text)
                self.assertEqual(stale_source.json()["detail"]["code"], "source_revision_changed")
                self.assertEqual((await submit(plan, request_id)).json(), receipt)
                self.assertEqual(observed.await_count, 1)

                # A rewrite preserves the logical name and OID but not the
                # approved physical relation; reject before the source exists.
                async with self.pg.session() as session:
                    await session.execute(text("TRUNCATE ts_batch_sync_sandbox.accounts"))
                    await session.execute(text("INSERT INTO ts_batch_sync_sandbox.accounts VALUES(77,'still independent')"))
                rewritten = await submit(plan)
                self.assertEqual(rewritten.status_code, 409, rewritten.text)
                self.assertEqual(rewritten.json()["detail"]["code"], "target_rewritten")
                self.assertEqual(observed.await_count, 1)

    async def test_known_rejection_binds_request_retry_and_plan_revision(self):
        async with api(True, self.provider) as client:
            response = await client.post(RUNS, json=PAYLOAD)
            self.assertEqual(response.status_code, 201, response.text)
            plan = response.json()
            path = f"{RUNS}/{plan['id']}"
            request = {"requestId": str(uuid4()), "expectedUpdatedAt": plan["updatedAt"]}
            with (
                patch.object(settings, "ENVIRONMENT", "local"),
                patch.object(settings, "BATCH_SYNC_SANDBOX_ENABLED", True),
                patch("app.web.services.batch_syncs.read_batch_sync_snapshot",
                      side_effect=[HTTPException(422, "8 KiB fixture; do not leak driver private text"),
                                   ([{"Source Identifier": 1, "Display Name": "ok"}], PROJECTION)]) as reader,
            ):
                reply = await client.post(path + "/runs", json=request)
                self.assertEqual(reply.status_code, 422, reply.text)
                self.assertEqual(reply.json()["detail"]["code"], "source_limit")
                history = (await client.get(path + "/attempts")).json()
                self.assertEqual((history[0]["status"], history[0]["receiptId"]), ("rejected", None))
                self.assertNotIn("driver private text", str(history))
                self.assertEqual((await client.get(path + "/runs")).json(), [])
                retried = await client.post(path + "/runs", json=request)
                self.assertEqual(retried.status_code, 200, retried.text)
                history = (await client.get(path + "/attempts")).json()
                self.assertEqual([(item["status"], item["requestId"], item["planUpdatedAt"]) for item in history], [
                    ("committed", request["requestId"], plan["updatedAt"]),
                    ("rejected", request["requestId"], plan["updatedAt"]),
                ])
                amended = await client.put(path, json={**PAYLOAD, "name": "Revised definition"})
                self.assertEqual(amended.status_code, 200, amended.text)
                rejected = await client.post(path + "/runs", json={**request, "expectedUpdatedAt": amended.json()["updatedAt"]})
                self.assertEqual(rejected.status_code, 409, rejected.text)
                self.assertEqual(rejected.json()["detail"]["code"], "request_revision_conflict")
                self.assertEqual(reader.await_count, 2)

    async def test_commit_ack_loss_never_overwrites_committed_attempt_and_replays(self):
        async def lost_ack_provider():
            async with self.pg.session() as session:
                original_commit = session.commit
                commits = 0

                async def commit():
                    nonlocal commits
                    commits += 1
                    await original_commit()
                    if commits == 2:
                        # PostgreSQL accepted the real target/receipt/attempt
                        # commit, but simulate the client's lost acknowledgment.
                        raise DBAPIError("COMMIT", None, OSError("isolated acknowledgement fault"))

                with patch.object(session, "commit", commit):
                    yield session

        async with api(True, self.provider) as client:
            saved = await client.post(RUNS, json=PAYLOAD)
            self.assertEqual(saved.status_code, 201, saved.text)
            plan = saved.json()
            path = f"{RUNS}/{plan['id']}"
            request = {"requestId": str(uuid4()), "expectedUpdatedAt": plan["updatedAt"]}
            with (
                patch.object(settings, "ENVIRONMENT", "local"),
                patch.object(settings, "BATCH_SYNC_SANDBOX_ENABLED", True),
                patch("app.web.services.batch_syncs.read_batch_sync_snapshot",
                      return_value=([{"Source Identifier": 1, "Display Name": "committed"}], PROJECTION)) as reader,
            ):
                async with api(True, lost_ack_provider) as lossy:
                    reply = await lossy.post(path + "/runs", json=request)
                self.assertEqual(reply.status_code, 503, reply.text)
                self.assertEqual(reply.json()["detail"]["code"], "outcome_unknown")
                self.assertNotIn("acknowledgement fault", reply.text)
                receipts = (await client.get(path + "/runs")).json()
                self.assertEqual(len(receipts), 1)
                history = (await client.get(path + "/attempts")).json()
                self.assertEqual([(item["status"], item["receiptId"]) for item in history], [("committed", receipts[0]["id"])])
                self.assertEqual((await client.post(path + "/runs", json=request)).json(), receipts[0])
                self.assertEqual(reader.await_count, 1)

    async def test_utc_jobs_commit_once_and_respect_generation_source_scope_and_actor(self):
        await self.pg.execute("CREATE TYPE public.statustypes AS ENUM ('ENABLE','DISABLE')")
        await self.pg.execute('CREATE TABLE public."user" (id uuid primary key, status public.statustypes, is_superuser boolean)')
        await self.pg.execute('INSERT INTO public."user" VALUES (:id, \'ENABLE\', true)', {"id": ACTOR})
        self.addAsyncCleanup(self.pg.execute, "DROP TYPE public.statustypes")
        self.addAsyncCleanup(self.pg.execute, 'DROP TABLE public."user"')
        slot = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)

        async def due_jobs(now):
            async with self.pg.session() as session:
                return await collect_due_batch_sync_jobs(session, now=now)

        async def execute(job, now):
            async with self.pg.session() as session:
                return await execute_batch_sync_schedule_job(session, job, now=now)

        async with api(True, self.provider) as client:
            saved = await client.post(RUNS, json=PAYLOAD)
            self.assertEqual(saved.status_code, 201, saved.text)
            plan = saved.json()
            path = f"{RUNS}/{plan['id']}"
            desired = {"plannedTimeUtc": "04:00", "expectedUpdatedAt": plan["updatedAt"], "enabled": True,
                       "expectedGeneration": None}
            with (
                patch.object(settings, "ENVIRONMENT", "local"),
                patch.object(settings, "BATCH_SYNC_SANDBOX_ENABLED", True),
                patch.object(settings, "BATCH_SYNC_SANDBOX_SCHEDULER_ENABLED", True),
                patch("app.web.services.batch_sync_schedules._utc_now", return_value=slot-timedelta(minutes=1)),
                patch("app.web.services.batch_sync_schedules.local_batch_sync_scope", return_value="test-namespace-a"),
                patch("app.web.services.batch_syncs.local_batch_sync_scope", return_value="test-namespace-a"),
                patch("app.web.services.batch_syncs.read_batch_sync_snapshot",
                      return_value=([{"Source Identifier": 1, "Display Name": "scheduled once"}], PROJECTION)) as reader,
            ):
                armed = await client.put(path + "/schedule", json=desired)
                self.assertEqual(armed.status_code, 200, armed.text)
                self.assertTrue(armed.json()["enabled"] and armed.json()["configuredEnabled"])
                self.assertEqual((await client.get("/etl/batch-sync-schedules")).json()[0]["connectionId"], PAYLOAD["connectionId"])
                self.assertEqual(await due_jobs(slot-timedelta(seconds=1)), [])
                first, = await due_jobs(slot)
                self.assertEqual(await due_jobs(slot), [])
                self.assertEqual(await execute(first, slot-timedelta(seconds=1)), "pending")
                manual = await client.post(path + "/runs", json={
                    "requestId": str(first), "expectedUpdatedAt": plan["updatedAt"],
                })
                self.assertEqual(manual.json()["detail"]["code"], "scheduled_request_reserved")
                self.assertEqual(reader.await_count, 0)
                self.assertEqual(await execute(first, slot), "committed")
                self.assertEqual(await execute(first, slot), "committed")
                receipts = (await client.get(path + "/runs")).json()
                self.assertEqual(len(receipts), 1)
                jobs = (await client.get(path + "/schedule/jobs")).json()
                self.assertEqual(jobs[0]["receiptId"], receipts[0]["id"])
                attempts = (await client.get(path + "/attempts")).json()
                self.assertEqual([(i["status"], i["scheduledJobId"]) for i in attempts],
                                 [("committed", str(first)), ("retryable", str(first))])
                day2 = slot + timedelta(days=1)
                second, = await due_jobs(day2)
                with patch("app.web.services.batch_syncs.local_batch_sync_scope", return_value="test-namespace-b"):
                    self.assertEqual(await execute(second, day2), "rejected")
                day3 = slot + timedelta(days=2)
                third, = await due_jobs(day3)
                await self.pg.execute('UPDATE public."user" SET is_superuser=false')
                self.assertEqual(await execute(third, day3), "rejected")
                await self.pg.execute('UPDATE public."user" SET is_superuser=true')
                day4 = slot + timedelta(days=3)
                fourth, = await due_jobs(day4)
                await self.pg.execute("UPDATE public.ontology_data_connections SET updated_at=clock_timestamp()")
                stale = (await client.get(path + "/schedule")).json()
                self.assertFalse(stale["enabled"])
                self.assertTrue(stale["configuredEnabled"])
                self.assertEqual(await execute(fourth, day4), "rejected")
                paused = await client.put(path + "/schedule", json={**desired, "enabled": False, "expectedGeneration": 1})
                self.assertEqual(paused.status_code, 200, paused.text)
                self.assertFalse(paused.json()["configuredEnabled"])
                self.assertEqual(reader.await_count, 1)

    async def test_paused_source_reconciles_only_existing_receipt_and_does_not_dispatch_queued_work(self):
        from app.web.services.data_connections import DataConnections

        service = object.__new__(DataConnections)
        service._pg = self.pg
        await self.pg.execute("CREATE TYPE public.statustypes AS ENUM ('ENABLE','DISABLE')")
        await self.pg.execute('CREATE TABLE public."user" (id uuid primary key, status public.statustypes, is_superuser boolean)')
        await self.pg.execute("""INSERT INTO public."user" VALUES (:id, 'ENABLE', true)""", {"id": ACTOR})
        self.addAsyncCleanup(self.pg.execute, "DROP TYPE public.statustypes")
        self.addAsyncCleanup(self.pg.execute, 'DROP TABLE public."user"')
        slot = datetime(2026, 11, 17, 3, 0, tzinfo=timezone.utc)
        connection_path = "/etl/data-connections/" + PAYLOAD["connectionId"]

        async def jobs(now):
            async with self.pg.session() as session:
                return await collect_due_batch_sync_jobs(session, now=now)

        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "ONTOLOGY_POSTGRES_SOURCE_PAUSE_ENABLED", True),
            patch.object(settings, "BATCH_SYNC_SANDBOX_ENABLED", True),
            patch.object(settings, "BATCH_SYNC_SANDBOX_SCHEDULER_ENABLED", True),
            patch("app.web.api.routes.etl.get_data_connections", return_value=service),
            patch("app.web.services.batch_sync_schedules._utc_now", return_value=slot - timedelta(minutes=1)),
            patch("app.web.services.batch_sync_schedules.local_batch_sync_scope", return_value="test-namespace-a"),
            patch("app.web.services.batch_syncs.local_batch_sync_scope", return_value="test-namespace-a"),
            patch("app.web.services.batch_syncs.read_batch_sync_snapshot",
                  return_value=([{"Source Identifier": 1, "Display Name": "reviewed once"}], PROJECTION)) as read,
        ):
            async with api(True, self.provider) as client:
                plan = (await client.post(RUNS, json=PAYLOAD)).json()
                route = f"{RUNS}/{plan['id']}"
                request = {"requestId": str(uuid4()), "expectedUpdatedAt": plan["updatedAt"]}
                receipt = await client.post(route + "/runs", json=request)
                self.assertEqual(receipt.status_code, 200, receipt.text)
                self.assertEqual(read.await_count, 1)
                arm = {"plannedTimeUtc": "03:00", "expectedUpdatedAt": plan["updatedAt"],
                       "enabled": True, "expectedGeneration": None}
                self.assertTrue((await client.put(route + "/schedule", json=arm)).json()["enabled"])
                queued, = await jobs(slot)

                pause = await client.put(connection_path + "/pause", json={
                    "paused": True, "expectedUpdatedAt": PAYLOAD["expectedSourceUpdatedAt"],
                })
                self.assertEqual(pause.status_code, 200, pause.text)
                status = (await client.get(route + "/schedule")).json()
                self.assertTrue(status["sourcePaused"] and status["configuredEnabled"])
                self.assertFalse(status["enabled"])
                self.assertEqual((await client.get(connection_path + "/batch-source-identity")).json()["state"], "paused")
                self.assertEqual(await jobs(slot + timedelta(minutes=1)), [])
                with patch.object(settings, "ONTOLOGY_POSTGRES_SOURCE_PAUSE_ENABLED", False):
                    later = await client.post(route + "/runs", json={**request, "requestId": str(uuid4())})
                    self.assertEqual(later.json()["detail"]["code"], "source_paused")
                    self.assertEqual((await client.post(route + "/runs", json=request)).json(), receipt.json())
                    rejected_arm = await client.put(route + "/schedule", json={**arm, "expectedGeneration": 1})
                    self.assertEqual(rejected_arm.json()["detail"]["code"], "source_paused")
                    async with self.pg.session() as session:
                        self.assertEqual(await execute_batch_sync_schedule_job(session, queued, now=slot), "rejected")
                self.assertEqual(read.await_count, 1)
                resumed = await client.put(connection_path + "/pause", json={
                    "paused": False, "expectedUpdatedAt": pause.json()["updatedAt"],
                })
                self.assertEqual(resumed.status_code, 200, resumed.text)
                self.assertFalse((await client.get(route + "/schedule")).json()["enabled"])
                stale = await client.post(route + "/runs", json={**request, "requestId": str(uuid4())})
                self.assertEqual(stale.json()["detail"]["code"], "source_revision_changed")
                self.assertEqual(await jobs(slot + timedelta(days=1)), [])
                self.assertEqual(read.await_count, 1)

    @unittest.skipUnless(all(IDENTITY_ORIGINS), "requires isolated A/B PostgreSQL source fixtures")
    async def test_exact_source_identity_is_sticky_and_blocks_wrong_origin_before_select(self):
        import asyncpg

        markers = [uuid4(), uuid4()]
        origins = [make_url(url) for url in IDENTITY_ORIGINS]
        role = "batch_identity_fixture_reader"
        for index, origin in enumerate(origins):
            if (origin.host not in {"127.0.0.1", "::1"}
                or origin.database != ("ontology_batch_origin_a" if index == 0 else "ontology_batch_origin_b")
                or origin.query):
                self.fail("Refusing any non-fixture source database")
        opened = []
        for origin, marker in zip(origins, markers, strict=True):
            admin = await asyncpg.connect(host=origin.host, port=origin.port, user=origin.username,
                                          password=origin.password, database=origin.database)
            self.addAsyncCleanup(admin.close)
            opened.append(admin)
            await admin.execute("""
                DO $$BEGIN
                    CREATE ROLE batch_identity_fixture_reader LOGIN;
                EXCEPTION WHEN duplicate_object THEN NULL;
                END$$;
                CREATE SCHEMA batch_sync_identity;
                CREATE TABLE batch_sync_identity.source_identity(marker uuid PRIMARY KEY);
                GRANT USAGE ON SCHEMA batch_sync_identity TO batch_identity_fixture_reader;
                GRANT SELECT ON batch_sync_identity.source_identity TO batch_identity_fixture_reader;
                CREATE SCHEMA "Source Catalog";
                CREATE TABLE "Source Catalog"."Account Data"("Source Identifier" integer PRIMARY KEY,"Display Name" text NOT NULL);
                GRANT USAGE ON SCHEMA "Source Catalog" TO batch_identity_fixture_reader;
                GRANT SELECT ON "Source Catalog"."Account Data" TO batch_identity_fixture_reader;
            """)
            self.addAsyncCleanup(admin.execute, 'DROP SCHEMA batch_sync_identity, "Source Catalog" CASCADE')
            await admin.execute("INSERT INTO batch_sync_identity.source_identity VALUES($1)", marker)
            await admin.execute('INSERT INTO "Source Catalog"."Account Data" VALUES(1,$1)', f"origin {len(opened)}")

        current = 0
        observed_sql = []

        class Capture:
            def __init__(self, original):
                self.original = original

            def __getattr__(self, attr):
                fn = getattr(self.original, attr)
                if attr not in ("fetch", "fetchval", "fetchrow", "execute"):
                    return fn

                async def record(sql, *params, **kwargs):
                    observed_sql.append((sql, params, current))
                    return await fn(sql, *params, **kwargs)
                return record

        async def connect(**kwargs):
            # Simulate a wrong same-shape service at the exact saved settings without changing a revision.
            origin = origins[current]
            kwargs.update(host=origin.host, port=origin.port, database=origin.database)
            return Capture(await asyncpg.connect(**kwargs))

        revision = datetime.fromisoformat(PAYLOAD["expectedSourceUpdatedAt"])
        stored = {
            "name": "Isolated Source", "engine": "postgresql", "updated_at": revision,
            "config": {"host": origins[0].host, "port": origins[0].port, "database": origins[0].database,
                       "username": role, "auth": "password", "tls": False},
        }
        connections = SimpleNamespace(_stored=AsyncMock(return_value=stored),
                                      _credentials=AsyncMock(return_value={"password": "isolated-test-value"}))
        review_url = f"/etl/data-connections/{PAYLOAD['connectionId']}/batch-source-identity"
        verify = {"expectedSourceUpdatedAt": PAYLOAD["expectedSourceUpdatedAt"]}
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "BATCH_SYNC_SANDBOX_ENABLED", True),
            patch.object(settings, "BATCH_SYNC_SOURCE_IDENTITY_MARKER", markers[0]),
            patch("app.web.services.batch_source_identity.get_data_connections", return_value=connections),
            patch("app.web.services.batch_syncs.get_data_connections", return_value=connections),
            patch("app.web.services.postgres_sync_source._driver", return_value=SimpleNamespace(connect=connect)),
        ):
            async with api(True, self.provider) as client:
                legacy = (await client.post(RUNS, json=PAYLOAD)).json()
                with patch.object(settings, "BATCH_SYNC_SOURCE_IDENTITY_ENABLED", True):
                    self.assertEqual((await client.get(review_url)).json()["state"], "unverified")
                    self.assertEqual((await client.post(RUNS, json=PAYLOAD)).status_code, 409)
                    checked = await client.post(review_url, json=verify)
                    self.assertEqual(checked.status_code, 200, checked.text)
                    self.assertEqual(checked.json()["state"], "verified")
                    self.assertNotIn(str(markers[0]), checked.text)
                    self.assertFalse(any('"Account Data"' in sql for sql, _, _ in observed_sql))
                    self.assertFalse(any(markers[0] in params for _, params, _ in observed_sql))
                    plan_resp = await client.post(RUNS, json=PAYLOAD)
                    self.assertEqual(plan_resp.status_code, 201, plan_resp.text)
                    plan = plan_resp.json()
                    self.assertTrue(plan["sourceIdentityReviewed"])
                    self.assertFalse(any("WITH s AS MATERIALIZED" in sql for sql, _, _ in observed_sql))
                    body = {"requestId": str(uuid4()), "expectedUpdatedAt": plan["updatedAt"]}
                    path = f"{RUNS}/{plan['id']}/runs"

                    current = 1
                    observed_sql.clear()
                    rejected = await client.post(path, json=body)
                    self.assertEqual(rejected.status_code, 409, rejected.text)
                    self.assertEqual(rejected.json()["detail"]["code"], "source_identity_mismatch")
                    self.assertFalse(any('"Account Data"' in sql for sql, _, _ in observed_sql))
                    self.assertEqual((await client.get(path)).json(), [])
                    self.assertEqual((await self.pg.fetch_one(
                        "SELECT caption FROM ts_batch_sync_sandbox.accounts WHERE id=1",
                    ))["caption"], "old")

                    current = 0
                    applied = await client.post(path, json=body)
                    self.assertEqual(applied.status_code, 200, applied.text)
                    self.assertTrue(applied.json()["sourceIdentityVerified"])
                    await opened[0].execute('TRUNCATE TABLE "Source Catalog"."Account Data"')
                    observed_sql.clear()
                    changed = await client.post(path, json={**body, "requestId": str(uuid4())})
                    self.assertEqual(changed.status_code, 409, changed.text)
                    self.assertEqual(changed.json()["detail"]["code"], "source_identity_mismatch")
                    self.assertFalse(any("WITH s AS MATERIALIZED" in sql for sql, _, _ in observed_sql))
                    observed_sql.clear()

                    current = 1
                    self.assertEqual((await client.post(review_url, json=verify)).status_code, 409)
                    self.assertEqual((await client.get(review_url)).json()["state"], "stale")
                self.assertEqual((await client.get(review_url)).json()["state"], "blocked")
                self.assertEqual((await client.post(RUNS, json=PAYLOAD)).status_code, 409)
                rejected = await client.post(f"{RUNS}/{legacy['id']}/runs", json={
                    "requestId": str(uuid4()), "expectedUpdatedAt": legacy["updatedAt"],
                })
                self.assertEqual(rejected.status_code, 409, rejected.text)
                self.assertEqual((await client.post(path, json=body)).json(), applied.json())
                self.assertEqual((await self.pg.fetch_one(
                    "SELECT caption FROM ts_batch_sync_sandbox.accounts WHERE id=1",
                ))["caption"], "origin 1")

    @unittest.skipUnless(IDENTITY_ORIGINS[0], "Requires an isolated loopback PostgreSQL source")
    async def test_exact_range_snapshot_preserves_outside_keys_and_replays_empty_after_disable(self):
        import asyncpg

        origin = make_url(IDENTITY_ORIGINS[0])
        if origin.host != "127.0.0.1" or origin.database != "ontology_batch_origin_a" or origin.query:
            self.fail("Refusing any non-fixture source database")
        admin = await asyncpg.connect(host=origin.host, port=origin.port, user=origin.username,
                                      password=origin.password, database=origin.database)
        self.addAsyncCleanup(admin.close)
        def migrate_range(sync, action):
            with Operations.context(MigrationContext.configure(sync.connection())):
                action()

        async with self.pg.session() as session:
            await session.run_sync(migrate_range, self.range_migration.downgrade)
            await session.run_sync(migrate_range, self.range_migration.upgrade)
        marker = uuid4()
        await admin.execute("""
            DO $$BEGIN CREATE ROLE batch_identity_fixture_reader LOGIN;
            EXCEPTION WHEN duplicate_object THEN NULL; END$$;
            CREATE SCHEMA batch_sync_identity;
            CREATE TABLE batch_sync_identity.source_identity(marker uuid PRIMARY KEY);
            CREATE SCHEMA batch_sync_range_fixture;
            CREATE TABLE batch_sync_range_fixture.records(id bigint PRIMARY KEY, value text NOT NULL);
            GRANT USAGE ON SCHEMA batch_sync_identity, batch_sync_range_fixture TO batch_identity_fixture_reader;
            GRANT SELECT ON batch_sync_identity.source_identity TO batch_identity_fixture_reader;
            GRANT SELECT, MAINTAIN ON batch_sync_range_fixture.records TO batch_identity_fixture_reader;
            INSERT INTO batch_sync_range_fixture.records SELECT k,'batch' FROM generate_series(1,10000) k;
            INSERT INTO batch_sync_range_fixture.records VALUES (-9223372036854775808,'first'),(-2,'second');
            ANALYZE batch_sync_range_fixture.records;
        """)
        self.addAsyncCleanup(admin.execute, "DROP SCHEMA batch_sync_identity, batch_sync_range_fixture CASCADE")
        await admin.execute("INSERT INTO batch_sync_identity.source_identity VALUES($1)", marker)
        await self.pg.execute("""
            CREATE TABLE ts_batch_sync_sandbox.range_fixture(id bigint PRIMARY KEY,value text NOT NULL);
        """)
        await self.pg.execute("INSERT INTO ts_batch_sync_sandbox.range_fixture VALUES(77,'unused')")
        revision = datetime.fromisoformat(PAYLOAD["expectedSourceUpdatedAt"])
        config = {"host": origin.host, "port": origin.port, "database": origin.database,
                  "username": "batch_identity_fixture_reader", "auth": "password", "tls": False}
        connections = SimpleNamespace(_stored=AsyncMock(return_value={
            "name": "Range source", "engine": "postgresql", "updated_at": revision, "config": config,
        }), _credentials=AsyncMock(return_value={"password": "isolated-test-value"}))
        definition = {**PAYLOAD, "sourceSchema": "batch_sync_range_fixture", "sourceTable": "records",
                      "sourceKeyColumn": "id", "targetTable": "range_fixture",
                      "mapping": [{"sourceColumn": name, "targetColumn": name} for name in ("id", "value")],
                      "keyRange": {"min": "-9223372036854775808", "max": "-1"}}
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "BATCH_SYNC_SANDBOX_ENABLED", True),
            patch.object(settings, "BATCH_SYNC_SOURCE_IDENTITY_ENABLED", True),
            patch.object(settings, "BATCH_SYNC_RANGE_SNAPSHOT_ENABLED", True),
            patch.object(settings, "BATCH_SYNC_SOURCE_IDENTITY_MARKER", marker),
            patch("app.web.services.batch_source_identity.get_data_connections", return_value=connections),
            patch("app.web.services.batch_syncs.get_data_connections", return_value=connections),
        ):
            async with api(True, self.provider) as client:
                verify = await client.post(f"/etl/data-connections/{PAYLOAD['connectionId']}/batch-source-identity",
                                          json={"expectedSourceUpdatedAt": PAYLOAD["expectedSourceUpdatedAt"]})
                self.assertEqual(verify.status_code, 200, verify.text)
                created = await client.post(RUNS, json=definition)
                self.assertEqual(created.status_code, 201, created.text)
                plan = created.json()
                self.assertEqual(plan["keyRange"], definition["keyRange"])
                run = f"{RUNS}/{plan['id']}/runs"
                request = {"requestId": str(uuid4()), "expectedUpdatedAt": plan["updatedAt"]}
                actual = await client.post(run, json=request)
                self.assertEqual(actual.status_code, 200, actual.text)
                self.assertEqual((actual.json()["rowsAffected"], actual.json()["keyRange"]), (2, definition["keyRange"]))
                rows = await self.pg.fetch_all("SELECT id,value FROM ts_batch_sync_sandbox.range_fixture ORDER BY id")
                self.assertEqual([tuple(row.values()) for row in rows], [
                    (-9223372036854775808, "first"), (-2, "second"), (77, "unused"),
                ])
                with patch.object(settings, "BATCH_SYNC_RANGE_SNAPSHOT_ENABLED", False), patch(
                    "app.web.services.batch_syncs.read_range_snapshot", side_effect=AssertionError("disabled read"),
                ) as read:
                    replay = await client.post(run, json=request)
                    self.assertEqual(replay.json(), actual.json())
                    self.assertEqual((await client.post(run, json={**request, "requestId": str(uuid4())})).status_code, 403)
                    read.assert_not_called()

                blocked_scope = await client.put(f"{RUNS}/{plan['id']}", json=PAYLOAD)
                self.assertEqual(blocked_scope.status_code, 409, blocked_scope.text)
                excess = await client.post(RUNS, json={**definition, "keyRange": {"min": "1", "max": "101"}})
                self.assertEqual(excess.status_code, 201, excess.text)
                excess_plan = excess.json()
                oversized = await client.post(f"{RUNS}/{excess_plan['id']}/runs", json={
                    "requestId": str(uuid4()), "expectedUpdatedAt": excess_plan["updatedAt"],
                })
                self.assertEqual((oversized.status_code, oversized.json()["detail"]["code"]), (422, "source_limit"))
                self.assertEqual(await self.pg.fetch_all("SELECT id,value FROM ts_batch_sync_sandbox.range_fixture ORDER BY id"), rows)

                empty_bound = {"min": "-100", "max": "-50"}
                modified = await client.put(f"{RUNS}/{plan['id']}", json={**definition, "keyRange": empty_bound})
                self.assertEqual(modified.status_code, 200, modified.text)
                empty = await client.post(run, json={"requestId": str(uuid4()), "expectedUpdatedAt": modified.json()["updatedAt"]})
                self.assertEqual(empty.status_code, 200, empty.text)
                self.assertEqual((empty.json()["status"], empty.json()["keyRange"], empty.json()["rowsAffected"]),
                                 ("no_new_data", empty_bound, 0))
                await admin.execute("REINDEX INDEX batch_sync_range_fixture.records_pkey")
                drift = await client.post(run, json={"requestId": str(uuid4()), "expectedUpdatedAt": modified.json()["updatedAt"]})
                self.assertEqual((drift.status_code, drift.json()["detail"]["code"]), (409, "source_identity_mismatch"))

        with self.assertRaisesRegex(RuntimeError, "Cannot remove reviewed range snapshots"):
            async with self.pg.session() as session:
                await session.run_sync(migrate_range, self.range_migration.downgrade)

    @unittest.skipUnless(IDENTITY_ORIGINS[0], "requires the isolated committed PostgreSQL source")
    async def test_incremental_real_publications_shared_cursor_atomic_failure_and_schedule_no_data(self):
        import asyncpg

        source = make_url(IDENTITY_ORIGINS[0])
        if source.host not in {"127.0.0.1", "::1"} or source.database != "ontology_batch_origin_a" or source.query:
            self.fail("Refusing any non-fixture source database")
        admin = await asyncpg.connect(host=source.host, port=source.port, user=source.username,
                                      password=source.password, database=source.database)
        self.addAsyncCleanup(admin.close)
        await admin.execute("""
            DO $$BEGIN CREATE ROLE batch_identity_fixture_reader LOGIN;
            EXCEPTION WHEN duplicate_object THEN NULL; END$$;
            CREATE SCHEMA batch_sync_identity;
            CREATE TABLE batch_sync_identity.source_identity(marker uuid PRIMARY KEY);
            GRANT USAGE ON SCHEMA batch_sync_identity TO batch_identity_fixture_reader;
            GRANT SELECT ON batch_sync_identity.source_identity TO batch_identity_fixture_reader;
        """)
        self.addAsyncCleanup(admin.execute, "DROP SCHEMA batch_sync_identity CASCADE")
        await admin.execute(Path("tools/fixtures/committed_batch_source.sql").read_text())
        self.addAsyncCleanup(admin.execute, "DROP SCHEMA batch_sync_stream CASCADE")
        await admin.execute("""
            GRANT USAGE ON SCHEMA batch_sync_stream TO batch_identity_fixture_reader;
            GRANT SELECT ON ALL TABLES IN SCHEMA batch_sync_stream TO batch_identity_fixture_reader;
        """)
        marker = uuid4()
        await admin.execute("INSERT INTO batch_sync_identity.source_identity VALUES($1)", marker)
        await self.pg.execute("""
            CREATE TABLE ts_batch_sync_sandbox.committed_batch_fixture(id bigint PRIMARY KEY, value text NOT NULL)
        """)
        await self.pg.execute("CREATE TYPE public.statustypes AS ENUM ('ENABLE','DISABLE')")
        await self.pg.execute('CREATE TABLE public."user" (id uuid primary key, status public.statustypes, is_superuser boolean)')
        await self.pg.execute('INSERT INTO public."user" VALUES (:id, \'ENABLE\', true)', {"id": ACTOR})
        self.addAsyncCleanup(self.pg.execute, "DROP TYPE public.statustypes")
        self.addAsyncCleanup(self.pg.execute, 'DROP TABLE public."user"')

        revision = datetime.fromisoformat(PAYLOAD["expectedSourceUpdatedAt"])
        config = {"host": source.host, "port": source.port, "database": source.database,
                  "username": "batch_identity_fixture_reader", "auth": "password", "tls": False}
        connections = SimpleNamespace(_stored=AsyncMock(return_value={
            "name": "Committed source", "engine": "postgresql", "updated_at": revision, "config": config,
        }), _credentials=AsyncMock(return_value={"password": "isolated-test-value"}))
        incremental = {**PAYLOAD, "mode": "incremental", "sourceSchema": "batch_sync_stream", "sourceTable": "batches",
                       "sourceKeyColumn": "id", "targetTable": "committed_batch_fixture",
                       "mapping": [{"sourceColumn": name, "targetColumn": name} for name in ("id", "value")]}
        review_url = f"/etl/data-connections/{PAYLOAD['connectionId']}/batch-source-identity"
        timeline = "/etl/ts_batch_sync_sandbox/committed_batch_fixture/batch-lineage-timeline"
        cursor_sql = "SELECT applied_through AS through FROM ontology_batch_sync_incremental_watermark"
        slot = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)

        async def request(client, plan, request_id=None):
            body = {"requestId": str(request_id or uuid4()), "expectedUpdatedAt": plan["updatedAt"]}
            return await client.post(f"{RUNS}/{plan['id']}/runs", json=body)

        async def publish(value):
            return await admin.fetchval("SELECT batch_sync_stream.publish($1::jsonb)", value)

        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "BATCH_SYNC_SANDBOX_ENABLED", True),
            patch.object(settings, "BATCH_SYNC_SOURCE_IDENTITY_ENABLED", True),
            patch.object(settings, "BATCH_SYNC_INCREMENTAL_ENABLED", True),
            patch.object(settings, "BATCH_SYNC_SOURCE_IDENTITY_MARKER", marker),
            patch.object(settings, "BATCH_SYNC_SANDBOX_SCHEDULER_ENABLED", True),
            patch("app.web.services.batch_sync_schedules._utc_now", return_value=slot-timedelta(minutes=1)),
            patch("app.web.services.batch_sync_schedules.local_batch_sync_scope", return_value="incremental-test-scope"),
            patch("app.web.services.batch_syncs.local_batch_sync_scope", return_value="incremental-test-scope"),
            patch("app.web.services.batch_source_identity.get_data_connections", return_value=connections),
            patch("app.web.services.batch_syncs.get_data_connections", return_value=connections),
        ):
            async with api(True, self.provider) as client:
                self.assertEqual((await client.get(timeline)).json()["lineage"]["state"], "unreviewed")
                async with api(False, self.provider) as reader:
                    self.assertEqual((await reader.get(timeline)).status_code, 403)
                with patch.object(settings, "BATCH_SYNC_INCREMENTAL_ENABLED", False):
                    self.assertEqual((await client.get(timeline)).status_code, 404)
                verify = await client.post(review_url, json={"expectedSourceUpdatedAt": PAYLOAD["expectedSourceUpdatedAt"]})
                self.assertEqual(verify.status_code, 200, verify.text)
                self.assertEqual((await client.post(RUNS, json={**incremental, "mode": "snapshot"})).status_code, 422)
                one = await client.post(RUNS, json=incremental)
                self.assertEqual(one.status_code, 201, one.text)
                first = one.json()
                two = await client.post(RUNS, json={**incremental, "name": "Same fixed binding"})
                self.assertEqual(two.status_code, 201, two.text)
                second = two.json()
                self.assertEqual(first["appliedThrough"], "0")
                self.assertEqual((await client.get(timeline)).json()["lineage"]["state"], "reviewed")
                empty_id = uuid4()
                empty = await request(client, first, empty_id)
                self.assertEqual(empty.status_code, 200, empty.text)
                self.assertEqual({key: empty.json()[key] for key in (
                    "status", "fromExclusive", "throughInclusive", "sourceHighWater", "eventsRead", "rowsAffected",
                )}, {"status": "no_new_data", "fromExclusive": "0", "throughInclusive": "0", "sourceHighWater": "0",
                     "eventsRead": 0, "rowsAffected": 0})

                self.assertEqual(await publish('[{"id":"9223372036854775807","value":"first"}]'), 1)
                self.assertEqual(await publish('[{"id":"9223372036854775807","value":"second"}]'), 2)
                positive = await request(client, second)
                self.assertEqual(positive.status_code, 200, positive.text)
                self.assertEqual((positive.json()["throughInclusive"], positive.json()["sourceHighWater"]), ("1", "2"))
                self.assertRegex(positive.json()["manifestHash"], r"^[0-9a-f]{64}$")
                self.assertEqual((await request(client, first, empty_id)).json(), empty.json())
                self.assertEqual((await self.pg.fetch_one(cursor_sql))["through"], 1)

                await self.pg.execute("""
                    CREATE FUNCTION ts_batch_sync_sandbox.reject_incremental_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN RAISE EXCEPTION 'synthetic fault' USING ERRCODE='23514'; END; $$
                """)
                await self.pg.execute("""
                    CREATE TRIGGER reject_incremental_receipt BEFORE INSERT ON ontology_batch_sync_receipts
                        FOR EACH ROW EXECUTE FUNCTION ts_batch_sync_sandbox.reject_incremental_receipt();
                """)
                rejected_id = uuid4()
                failed = await request(client, first, rejected_id)
                self.assertEqual(failed.status_code, 422, failed.text)
                self.assertEqual((await self.pg.fetch_one(cursor_sql))["through"], 1)
                self.assertEqual((await self.pg.fetch_one(
                    "SELECT value FROM ts_batch_sync_sandbox.committed_batch_fixture",
                ))["value"], "first")
                await self.pg.execute("DROP TRIGGER reject_incremental_receipt ON ontology_batch_sync_receipts")
                applied = await request(client, first, rejected_id)
                self.assertEqual(applied.status_code, 200, applied.text)
                self.assertEqual((applied.json()["fromExclusive"], applied.json()["throughInclusive"]), ("1", "2"))
                self.assertEqual((await self.pg.fetch_one(cursor_sql))["through"], 2)

                firstpath = f"{RUNS}/{first['id']}"
                armed = await client.put(firstpath + "/schedule", json={
                    "enabled": True, "expectedGeneration": None, "expectedUpdatedAt": first["updatedAt"],
                    "plannedTimeUtc": "04:00",
                })
                self.assertEqual(armed.status_code, 200, armed.text)
                async with self.pg.session() as session:
                    job, = await collect_due_batch_sync_jobs(session, now=slot)
                async with self.pg.session() as session:
                    status = await execute_batch_sync_schedule_job(session, job, now=slot)
                self.assertEqual(status, "no_new_data")
                info, = (await client.get(firstpath + "/schedule/jobs")).json()
                self.assertEqual(info["state"], "no_new_data")
                self.assertIsNotNone(info["receiptId"])
                self.assertEqual((await self.pg.fetch_one(cursor_sql))["through"], 2)
                attempts = (await client.get(firstpath + "/attempts")).json()
                self.assertIn(("no_new_data", info["receiptId"]), {(row["status"], row["receiptId"]) for row in attempts})
                self.assertEqual({item["appliedThrough"] for item in (await client.get(RUNS)).json()}, {"2"})

                observed = await client.get(timeline)
                self.assertEqual(observed.status_code, 200, observed.text)
                edge = observed.json()["lineage"]
                self.assertEqual((edge["state"], edge["appliedThrough"], edge["latestPositiveReceiptId"]),
                                 ("observed", "2", applied.json()["id"]))
                self.assertEqual((await client.get(timeline, params={"from": "2026-01-01T00:00:00Z"})).status_code, 422)
                self.assertEqual((await client.get(timeline, params={
                    "from": "2026-01-01T00:00:00Z", "to": "2026-02-05T00:00:00Z",
                })).status_code, 422)

                scheduled_detail = await client.get(timeline + "/receipt/" + info["receiptId"])
                self.assertEqual(scheduled_detail.status_code, 200, scheduled_detail.text)
                scheduled = scheduled_detail.json()
                self.assertEqual((scheduled["trigger"], scheduled["status"], scheduled["job"]["state"]),
                                 ("daily_utc", "no_new_data", "no_new_data"))
                self.assertEqual(scheduled["rowsAffected"], 0)
                for forbidden in ("planId", "requestId", "connectionId", "manifestHash", "sourceIdentityVerified"):
                    self.assertNotIn(forbidden, scheduled)

                # Simulate legacy successful attempt and force a cross-table timestamp/UUID tie.
                tie = UUID(applied.json()["id"])
                await self.pg.execute("""
                    UPDATE ontology_batch_sync_attempts SET id=:id, incremental_scope=NULL, started_at=(
                        SELECT recorded_at FROM ontology_batch_sync_receipts WHERE id=:id
                    ) WHERE receipt_id=:id AND status='committed'
                """, {"id": tie})
                cursor = None
                seen = []
                for page_number in range(20):
                    params = {"limit": 1}
                    if cursor:
                        params["cursor"] = cursor
                    page = await client.get(timeline, params=params)
                    self.assertEqual(page.status_code, 200, page.text)
                    entry, = page.json()["entries"]
                    seen.append((entry["kind"], entry["id"]))
                    cursor = page.json()["nextCursor"]
                    if cursor is None:
                        break
                else:
                    self.fail("Timeline did not terminate under bounded keyset paging")
                paired = [kind for kind, identity in seen if identity == str(tie)]
                self.assertEqual(paired, ["receipt", "attempt"])
                self.assertEqual(len(seen), len(set(seen)))

                # Commit a real later batch after timeline watermark read; one response must not mix generations.
                from app.web.services import batch_lineage

                captured, release = asyncio.Event(), asyncio.Event()
                original_watermark = batch_lineage.watermark

                async def hold_watermark(session):
                    result = await original_watermark(session)
                    captured.set()
                    await release.wait()
                    return result

                with patch.object(batch_lineage, "watermark", hold_watermark):
                    pending = asyncio.create_task(client.get(timeline))
                    try:
                        await asyncio.wait_for(captured.wait(), 5)
                        self.assertEqual(await publish('[{"id":1,"value":"concurrent publication"}]'), 3)
                        third = await request(client, first)
                        self.assertEqual(third.status_code, 200, third.text)
                    finally:
                        release.set()
                    concurrent_page = await asyncio.wait_for(pending, 5)
                self.assertEqual(concurrent_page.status_code, 200, concurrent_page.text)
                self.assertEqual(concurrent_page.json()["lineage"]["appliedThrough"], "2")
                self.assertNotIn(third.json()["id"], {item["id"] for item in concurrent_page.json()["entries"]})
                self.assertEqual((await client.get(timeline)).json()["lineage"]["appliedThrough"], "3")

                # Local archive is independent of the mutable saved connection and of the source being healthy.
                with patch.object(settings, "BATCH_SYNC_SOURCE_IDENTITY_MARKER", uuid4()):
                    self.assertEqual((await client.get(timeline)).json()["lineage"]["state"], "observed")
                await self.pg.execute("TRUNCATE ts_batch_sync_sandbox.committed_batch_fixture")
                failed_after_rewrite = await request(client, first)
                self.assertEqual(failed_after_rewrite.status_code, 409, failed_after_rewrite.text)
                archived = await client.get(timeline)
                self.assertEqual(archived.status_code, 200, archived.text)
                self.assertEqual(archived.json()["lineage"]["state"], "archived")
                for field in ("appliedThrough", "latestPositiveReceiptId", "latestPositiveRecordedAt"):
                    self.assertIsNone(archived.json()["lineage"][field])
                self.assertTrue(all(item["physicalScope"] == "archived" for item in archived.json()["entries"]))
                failure, = [item for item in archived.json()["entries"] if item["code"] == "target_rewritten"]
                self.assertIsNone(failure["receiptId"])
                self.assertIsNone(failure["rowsAffected"])
                history = await client.get(timeline + "/attempt/" + str(tie))
                self.assertEqual(history.status_code, 200, history.text)
                self.assertEqual(history.json()["receiptId"], str(tie))
                await self.pg.execute("DROP TABLE ts_batch_sync_sandbox.committed_batch_fixture")
                absent = await client.get(timeline)
                self.assertEqual(absent.status_code, 200, absent.text)
                self.assertEqual(absent.json()["lineage"]["state"], "archived")
