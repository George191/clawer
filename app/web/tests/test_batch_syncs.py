"""Local batch-sync contracts and isolated target transaction/concurrency tests."""

import asyncio
from datetime import datetime, timedelta, timezone
from importlib import import_module
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
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
        await migrate(self.pg, apply=True)
        async with self.pg.session() as session:
            def upgrade(sync_session):
                with Operations.context(MigrationContext.configure(sync_session.connection())):
                    self.migration.upgrade()
                    self.attempts_migration.upgrade()
                    self.schedules_migration.upgrade()

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
                "DROP TABLE public.ontology_data_connections",
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
