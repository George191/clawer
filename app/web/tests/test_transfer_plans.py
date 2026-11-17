"""Transfer draft validation, authorization and optional isolated PostgreSQL checks."""

import asyncio
from importlib import import_module
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
import httpx
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.storage.postgres_client import PostgresClient
from app.web.api.dependencies.common import get_current_user
from app.web.api.dependencies.session import get_db
from app.web.api.models.transfer_plans import TransferPlanPayload
from app.web.api.routes.etl import router
from app.web.core.config import settings
from app.web.services.asset_freshness import evaluate_due_asset_freshness
from app.web.services.etl_layers import EtlLayers
from app.web.services.transfer_schedules import collect_due_transfer_jobs
from app.web.services.transfer_upstream import collect_due_upstream_transfer_jobs, execute_upstream_transfer_job_in_session


PAYLOAD = {
    "name": "Financial Fact flow",
    "sourceSchema": "ts_rds",
    "sourceTable": "rds_financial_fact",
    "targetSchema": "ts_ods",
    "targetTable": "ods_financial_fact",
    "writeMode": "append",
    "trigger": "manual",
}
TRANSFERS_URL = os.environ.get("ONTOLOGY_TRANSFERS_TEST_DATABASE_URL")
TEST_DB = "ontology_transfers_regression"
RUNS = "/etl/transfer-plans"
TEST_ACTOR = uuid4()


def api(is_admin, session_provider=lambda: None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(is_superuser=is_admin, id=TEST_ACTOR)
    app.dependency_overrides[get_db] = session_provider
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class TransferPlanTests(unittest.IsolatedAsyncioTestCase):
    def test_payload_rules(self):
        self.assertIsNone(TransferPlanPayload.model_validate(PAYLOAD).scheduleTime)
        self.assertEqual(TransferPlanPayload.model_validate(
            {**PAYLOAD, "trigger": "scheduled", "scheduleTime": "09:30"}).scheduleTime, "09:30")
        for invalid in (
            {"sourceSchema": "public"}, {"targetTable": "a.b"}, {"name": "  "},
            {"writeMode": "replace"}, {"trigger": "streaming"}, {"trigger": "scheduled"},
            {"targetSchema": "ts_rds", "targetTable": "rds_financial_fact"}, {"scheduleTime": "09:30"},
            {"trigger": "scheduled", "scheduleTime": "24:00"}, {"status": "active"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                TransferPlanPayload.model_validate({**PAYLOAD, **invalid})

    async def test_non_admin_cannot_list_create_or_update(self):
        async with api(False) as client:
            for response in (
                await client.get("/etl/transfer-plans"),
                await client.post("/etl/transfer-plans", json=PAYLOAD),
                await client.post("/etl/transfer-plans/preflight", json=PAYLOAD),
                await client.put(f"/etl/transfer-plans/{uuid4()}", json=PAYLOAD),
            ):
                self.assertEqual(response.status_code, 403, response.text)

    async def test_sandbox_capability_is_admin_only_and_disabled_by_flag(self):
        with patch.object(settings, "TRANSFER_SANDBOX_ENABLED", False):
            async with api(True) as client:
                response = await client.get(f"{RUNS}/run-capabilities")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"enabled": False, "sourceSchema": "ts_transfer_sandbox",
                                                   "targetSchema": "ts_transfer_sandbox", "maxRows": 100,
                                                   "incrementalAppend": False, "scheduledUpsert": False,
                                                   "upstreamUpsert": False})
            async with api(False) as client:
                path = f"{RUNS}/{uuid4()}/runs"
                self.assertEqual((await client.get(f"{RUNS}/run-capabilities")).status_code, 403)
                self.assertEqual((await client.get("/etl/transfer-upstream-triggers")).status_code, 403)
                for suffix in ("", "/candidates"):
                    self.assertEqual((await client.get(
                        f"/etl/asset-freshness/ts_transfer_sandbox/to_rows{suffix}",
                    )).status_code, 403)
                self.assertEqual((await client.get(path)).status_code, 403)
                self.assertEqual((await client.post(path, json={
                    "requestId": str(uuid4()), "expectedUpdatedAt": datetime.now(timezone.utc).isoformat(),
                })).status_code, 403)
                self.assertEqual((await client.get("/etl/ts_transfer_sandbox/from_rows/transfer-runs")).status_code, 403)


@unittest.skipUnless(TRANSFERS_URL, "Requires an isolated ontology_transfers_regression PostgreSQL database")
class TransferPlanIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if make_url(TRANSFERS_URL).database != TEST_DB:
            raise RuntimeError(f"Transfer tests refuse any database other than {TEST_DB}")
        self.pg = PostgresClient(TRANSFERS_URL)
        await self.pg.connect(max_retries=1)
        self.addAsyncCleanup(self.pg.close)
        database = await self.pg.fetch_one("SELECT current_database() AS name")
        if database["name"] != TEST_DB:
            raise RuntimeError(f"Transfer tests are not connected to {TEST_DB}")
        self.migration = import_module("app.web.alembic.versions.2f6f09ba33a7_ontology_transfer_plans")
        self.run_migration = import_module("app.web.alembic.versions.61a4c8fed182_ontology_transfer_runs")
        self.incremental_migration = import_module("app.web.alembic.versions.b29e8b8d4521_ontology_incremental_transfer")
        self.schedule_migration = import_module("app.web.alembic.versions.e427be5f90e2_ontology_transfer_schedules")
        self.upstream_migration = import_module("app.web.alembic.versions.5bef50bc72c9_ontology_transfer_upstream")
        self.compound_migration = import_module("app.web.alembic.versions.b6c45a7e9130_ontology_compound_upstream")
        self.freshness_migration = import_module("app.web.alembic.versions.d91a24c73f60_ontology_asset_freshness")
        self.quality_migration = import_module("app.web.alembic.versions.e66ab7c415f2_ontology_asset_quality_metadata")
        self.incident_migration = import_module(
            "app.web.alembic.versions.f32194ec7a60_ontology_asset_quality_incidents",
        )
        self.timeline_migration = import_module("app.web.alembic.versions.db42c1589a0e_ontology_transfer_timeline")
        async with self.pg.session() as session:
            for sql in (
                "CREATE SCHEMA ts_rds", "CREATE SCHEMA ts_ods", "CREATE SCHEMA ts_meta",
                "CREATE TABLE ts_rds.rds_financial_fact (id int PRIMARY KEY)",
                "CREATE TABLE ts_ods.ods_financial_fact (id int PRIMARY KEY)",
                "CREATE TABLE ts_ods.no_pk (id int)", "CREATE TABLE ts_ods.hidden (id int PRIMARY KEY)",
                "CREATE TABLE ts_meta.reserved (id int PRIMARY KEY)",
                "CREATE TABLE public.ontology_objects (schema_name text, table_name text, is_active bool, visibility text)",
                "CREATE TABLE public.ontology_object_properties (id int)",
                "INSERT INTO public.ontology_objects VALUES ('ts_ods', 'hidden', true, 'hidden')",
                "INSERT INTO ts_rds.rds_financial_fact VALUES (1), (2)",
                "INSERT INTO ts_ods.ods_financial_fact VALUES (3)",
            ):
                await session.execute(text(sql))
            await self.migrate(session, self.migration.upgrade)
            await self.migrate(session, self.run_migration.upgrade)
            await self.migrate(session, self.incremental_migration.upgrade)
            await self.migrate(session, self.schedule_migration.upgrade)
            await self.migrate(session, self.upstream_migration.upgrade)
            await self.migrate(session, self.compound_migration.upgrade)
            await self.migrate(session, self.freshness_migration.upgrade)
            await self.migrate(session, self.quality_migration.upgrade)
            await self.migrate(session, self.incident_migration.upgrade)
            await self.migrate(session, self.timeline_migration.upgrade)
        self.addAsyncCleanup(self.drop_fixtures)
        self.layers = object.__new__(EtlLayers)
        self.layers._pg = self.pg

    async def migrate(self, session, migration):
        def run(sync_session):
            with Operations.context(MigrationContext.configure(sync_session.connection())):
                migration()
        await session.run_sync(run)

    async def drop_fixtures(self):
        async with self.pg.session() as session:
            await session.execute(text("DROP TABLE IF EXISTS public.ontology_asset_quality_incidents"))
            await session.execute(text("DROP TABLE IF EXISTS public.ontology_asset_quality_transitions, "
                                       "public.ontology_asset_quality_observations"))
            await session.execute(text("DROP TABLE IF EXISTS public.ontology_asset_freshness_transitions, "
                                       "public.ontology_asset_freshness_policies"))
            await session.execute(text("DROP TABLE IF EXISTS public.ontology_transfer_attempts"))
            await session.execute(text("DROP TABLE IF EXISTS public.ontology_transfer_schedule_jobs"))
            await session.execute(text("DROP TABLE IF EXISTS public.ontology_transfer_schedules"))
            await session.execute(text("DROP TABLE IF EXISTS public.ontology_transfer_upstream_job_events, "
                                       "public.ontology_transfer_upstream_parents"))
            await session.execute(text("DROP TABLE IF EXISTS public.ontology_transfer_upstream_jobs, "
                                       "public.ontology_transfer_upstream_triggers, public.ontology_transfer_commit_events"))
            await session.execute(text("DROP TABLE IF EXISTS public.ontology_transfer_checkpoints"))
            await session.execute(text("DROP TABLE IF EXISTS public.ontology_transfer_runs"))
            await session.execute(text("DROP TABLE IF EXISTS public.ontology_transfer_plans"))
            await session.execute(text("DROP TABLE public.ontology_objects, public.ontology_object_properties"))
            for schema in ("ts_rds", "ts_ods", "ts_meta"):
                await session.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            await session.execute(text("DROP SCHEMA IF EXISTS ts_transfer_sandbox CASCADE"))

    async def test_freshness_confirms_exact_physical_receipts_and_handles_stale_checks(self):
        async with self.pg.session() as session:
            for sql in (
                "CREATE SCHEMA ts_transfer_sandbox",
                "CREATE TABLE ts_transfer_sandbox.freshness_source (id int PRIMARY KEY, value text)",
                "CREATE TABLE ts_transfer_sandbox.freshness_target (id int PRIMARY KEY, value text)",
            ):
                await session.execute(text(sql))
        config = {**PAYLOAD, "sourceSchema": "ts_transfer_sandbox", "targetSchema": "ts_transfer_sandbox",
                  "sourceTable": "freshness_source", "targetTable": "freshness_target", "writeMode": "upsert"}
        route = "/etl/asset-freshness/ts_transfer_sandbox/freshness_target"

        async def check(now):
            async with self.pg.session() as session:
                return await evaluate_due_asset_freshness(session, now=now)

        def assert_state(value, state, reason=None):
            self.assertEqual((value["assessment"]["state"], value["assessment"]["reason"]), (state, reason))

        with (patch("app.web.services.transfer_plans.get_etl_layers", return_value=self.layers),
              patch("app.web.services.transfer_runs.get_etl_layers", return_value=self.layers),
              patch.object(settings, "TRANSFER_SANDBOX_ENABLED", True),
              patch.object(settings, "ENVIRONMENT", "local")):
            async with api(True, self.session_provider) as client:
                created = await client.post(RUNS, json=config)
                self.assertEqual(created.status_code, 201, created.text)
                plan = created.json()
                binding = {"planId": plan["id"], "expectedPlanUpdatedAt": plan["updatedAt"],
                           "expectedGeneration": None, "thresholdMinutes": 5, "enabled": True}
                with patch.object(settings, "TRANSFER_SANDBOX_FRESHNESS_ENABLED", False):
                    self.assertFalse((await client.get(route)).json()["available"])
                    self.assertEqual((await client.put(route, json=binding)).status_code, 403)
                    self.assertEqual((await check(datetime.now(timezone.utc)))["checked"], 0)
                with patch.object(settings, "TRANSFER_SANDBOX_FRESHNESS_ENABLED", True):
                    self.assertEqual((await client.get(route + "/candidates")).json()[0]["id"], plan["id"])
                    self.assertEqual((await client.get(route.replace("ts_transfer_sandbox", "ts_rds"))).status_code, 404)
                    response = await client.put(route, json=binding)
                    self.assertEqual(response.status_code, 200, response.text)
                    assert_state(response.json(), "not_checked")
                    self.assertEqual((await client.put(route, json=binding)).status_code, 409)
                    transfer_path = f"{RUNS}/{plan['id']}/runs"

                    async def run():
                        result = await client.post(transfer_path, json={
                            "requestId": str(uuid4()), "expectedUpdatedAt": plan["updatedAt"],
                        })
                        self.assertEqual(result.status_code, 200, result.text)
                        return result.json()

                    async with self.pg.session() as session:
                        await session.execute(text("""
                            INSERT INTO ontology_transfer_runs (
                                id, plan_id, request_id, initiated_by, plan_updated_at, source_schema, source_table,
                                target_schema, target_table, write_mode, rows_affected, recorded_at
                            ) VALUES (
                                :id, :plan, :request, :actor, :revision, 'ts_transfer_sandbox', 'freshness_source',
                                'ts_transfer_sandbox', 'freshness_target', 'upsert', 1, clock_timestamp()
                            )
                        """), {"id": uuid4(), "plan": plan["id"], "request": uuid4(), "actor": TEST_ACTOR,
                                "revision": datetime.fromisoformat(plan["updatedAt"])})
                    self.assertEqual((await run())["rowsAffected"], 0)
                    started = datetime.now(timezone.utc)
                    self.assertEqual((await check(started))["unknown"], 1)
                    assert_state((await client.get(route)).json(), "unknown", "no_verified_write")
                    await self.pg.execute("INSERT INTO ts_transfer_sandbox.freshness_source VALUES (1, 'first')")
                    positive = await run()
                    self.assertEqual(positive["rowsAffected"], 1)
                    tick = started + timedelta(minutes=1)
                    results = await asyncio.gather(check(tick), check(tick))
                    self.assertEqual(sum(item["healthy"] for item in results), 1)
                    with patch("app.web.services.asset_freshness._utc_now", return_value=tick):
                        healthy = (await client.get(route)).json()
                        assert_state(healthy, "healthy")
                        self.assertEqual(healthy["assessment"]["receiptId"], positive["id"])
                        self.assertEqual(len(healthy["history"]), 2)
                    # A recent sample near its threshold cannot stay green after the threshold itself expires.
                    near_threshold = datetime.fromisoformat(positive["recordedAt"]) + timedelta(minutes=4, seconds=50)
                    self.assertEqual((await check(near_threshold))["healthy"], 1)
                    validity = near_threshold + timedelta(seconds=10)
                    with patch("app.web.services.asset_freshness._utc_now", return_value=near_threshold):
                        valid = (await client.get(route)).json()
                        self.assertEqual(datetime.fromisoformat(valid["assessment"]["validUntil"]), validity)
                    with patch("app.web.services.asset_freshness._utc_now", return_value=validity + timedelta(seconds=1)):
                        stale = (await client.get(route)).json()
                        assert_state(stale, "unknown", "evaluation_stale")
                        self.assertIsNone(stale["assessment"]["receiptId"])
                        self.assertIsNotNone(stale["assessment"]["checkedAt"])
                    late = tick + timedelta(minutes=6)
                    self.assertEqual((await check(late))["overdue"], 1)
                    with patch("app.web.services.asset_freshness._utc_now", return_value=late):
                        result = (await client.get(route)).json()
                        assert_state(result, "overdue")
                        self.assertEqual(result["incidents"]["active"][0]["reason"], "write_overdue")
                    await self.pg.execute("TRUNCATE ts_transfer_sandbox.freshness_target")
                    with patch("app.web.services.asset_freshness._utc_now", return_value=late):
                        drift = (await client.get(route)).json()
                        assert_state(drift, "unknown", "target_changed")
                        self.assertIsNone(drift["assessment"]["receiptId"])
                        self.assertEqual(drift["incidents"]["active"][0]["status"], "open")
                    with patch.object(settings, "TRANSFER_SANDBOX_FRESHNESS_ENABLED", False):
                        paused = await client.put(route, json={**binding, "expectedGeneration": 1, "enabled": False})
                        self.assertEqual(paused.status_code, 200, paused.text)
                        assert_state(paused.json(), "paused")
                        self.assertFalse(paused.json()["incidents"]["active"])
                        self.assertEqual(paused.json()["incidents"]["history"][0]["closeReason"], "policy_paused")

    async def test_quality_checks_capture_reviewed_schema_and_measure_only_main_heap(self):
        async with self.pg.session() as session:
            for sql in (
                "CREATE SCHEMA ts_transfer_sandbox",
                "CREATE TABLE ts_transfer_sandbox.quality_source (id int PRIMARY KEY)",
                "CREATE TABLE ts_transfer_sandbox.quality_target (id int, tag varchar(10), "
                "description text DEFAULT 'test_default_never_exposed', PRIMARY KEY (tag, id))",
            ):
                await session.execute(text(sql))
        config = {**PAYLOAD, "sourceSchema": "ts_transfer_sandbox", "targetSchema": "ts_transfer_sandbox",
                  "sourceTable": "quality_source", "targetTable": "quality_target", "writeMode": "upsert"}
        route = "/etl/asset-freshness/ts_transfer_sandbox/quality_target"

        async def check(now):
            async with self.pg.session() as session:
                return await evaluate_due_asset_freshness(session, now=now)

        with (patch("app.web.services.transfer_plans.get_etl_layers", return_value=self.layers),
              patch.object(settings, "TRANSFER_SANDBOX_ENABLED", True),
              patch.object(settings, "TRANSFER_SANDBOX_FRESHNESS_ENABLED", True),
              patch.object(settings, "ENVIRONMENT", "local")):
            async with api(True, self.session_provider) as client:
                plan = (await client.post(RUNS, json=config)).json()
                binding = {"planId": plan["id"], "expectedPlanUpdatedAt": plan["updatedAt"],
                           "expectedGeneration": None, "thresholdMinutes": 5, "enabled": True,
                           "schemaCheckEnabled": True, "heapSize": {"maxBytes": "0"}}
                for bounds in ({"minBytes": "2", "maxBytes": "1"},
                               {"maxBytes": "9223372036854775808"}, {"maxBytes": 0}):
                    self.assertEqual((await client.put(route, json={**binding, "heapSize": bounds})).status_code, 422)
                saved = await client.put(route, json=binding)
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertIsNone(saved.json()["policy"]["schemaBaseline"])
                self.assertEqual(saved.json()["policy"]["heapSize"]["maxBytes"], "0")
                preview_route = route + "/schema-baseline/preview"
                baseline_route = route + "/schema-baseline"
                response = await client.post(preview_route, json={"expectedGeneration": 1})
                self.assertEqual(response.status_code, 200, response.text)
                preview = response.json()
                self.assertEqual([column["name"] for column in preview["columns"]], ["id", "tag", "description"])
                self.assertEqual([column["primaryKeyPosition"] for column in preview["columns"]], [2, 1, None])
                self.assertEqual(preview["columns"][1]["type"], "character varying(10)")
                self.assertNotIn("test_default_never_exposed", response.text)
                await self.pg.execute("ALTER TABLE ts_transfer_sandbox.quality_target ADD COLUMN extra int")
                rejected = await client.post(baseline_route, json={
                    "expectedGeneration": 1, "expectedFingerprint": preview["fingerprint"],
                })
                self.assertEqual(rejected.status_code, 409, rejected.text)
                self.assertIsNone((await client.get(route)).json()["policy"]["schemaBaseline"])
                preview = (await client.post(preview_route, json={"expectedGeneration": 1})).json()
                captured = await client.post(baseline_route, json={
                    "expectedGeneration": 1, "expectedFingerprint": preview["fingerprint"],
                })
                self.assertEqual(captured.status_code, 200, captured.text)
                self.assertEqual(captured.json()["policy"]["generation"], 2)
                self.assertEqual(captured.json()["policy"]["schemaBaseline"]["fingerprint"], preview["fingerprint"])
                tick = datetime.now(timezone.utc)
                self.assertEqual((await check(tick))["checked"], 1)
                with patch("app.web.services.asset_freshness._utc_now", return_value=tick):
                    states = (await client.get(route)).json()["checks"]
                    self.assertEqual((states["schema"]["state"], states["heapSize"]["heapBytes"]), ("healthy", "0"))
                await self.pg.execute("ALTER TABLE ts_transfer_sandbox.quality_target ALTER COLUMN extra SET NOT NULL")
                await self.pg.execute("INSERT INTO ts_transfer_sandbox.quality_target(id, tag, extra) VALUES (1, 'x', 1)")
                late = tick + timedelta(minutes=1)
                self.assertEqual((await check(late))["checked"], 1)
                size = await self.pg.fetch_one(
                    "SELECT pg_relation_size('ts_transfer_sandbox.quality_target', 'main') AS bytes",
                )
                with patch("app.web.services.asset_freshness._utc_now", return_value=late):
                    states = (await client.get(route)).json()["checks"]
                    self.assertEqual(states["schema"]["reason"], "schema_drift")
                    self.assertEqual(states["heapSize"]["reason"], "above_maximum")
                    self.assertEqual(states["heapSize"]["heapBytes"], str(size["bytes"]))
                    self.assertEqual(len(states["schema"]["history"]), 2)
                with patch("app.web.services.asset_freshness._utc_now", return_value=late + timedelta(minutes=4)):
                    states = (await client.get(route)).json()["checks"]
                    self.assertEqual(states["schema"]["reason"], "evaluation_stale")
                    self.assertIsNone(states["heapSize"]["heapBytes"])
                ordinary = {k: v for k, v in binding.items() if k not in ("schemaCheckEnabled", "heapSize")}
                saved = await client.put(route, json={**ordinary, "expectedGeneration": 2, "thresholdMinutes": 6})
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertEqual(saved.json()["policy"]["schemaBaseline"]["fingerprint"], preview["fingerprint"])
                self.assertEqual(saved.json()["policy"]["heapSize"]["maxBytes"], "0")
                self.assertEqual(saved.json()["checks"]["schema"]["state"], "not_checked")

    async def test_quality_incident_acknowledges_without_recovery_and_preserves_history(self):
        async with self.pg.session() as session:
            for sql in (
                "CREATE SCHEMA ts_transfer_sandbox",
                "CREATE TABLE ts_transfer_sandbox.incident_source (id int PRIMARY KEY, value text)",
                "CREATE TABLE ts_transfer_sandbox.incident_target (id int PRIMARY KEY, value text)",
            ):
                await session.execute(text(sql))
        config = {**PAYLOAD, "sourceSchema": "ts_transfer_sandbox", "targetSchema": "ts_transfer_sandbox",
                  "sourceTable": "incident_source", "targetTable": "incident_target", "writeMode": "upsert"}
        route = "/etl/asset-freshness/ts_transfer_sandbox/incident_target"

        async def check(now):
            async with self.pg.session() as session:
                return await evaluate_due_asset_freshness(session, now=now)

        with (patch("app.web.services.transfer_plans.get_etl_layers", return_value=self.layers),
              patch.object(settings, "TRANSFER_SANDBOX_ENABLED", True),
              patch.object(settings, "TRANSFER_SANDBOX_FRESHNESS_ENABLED", True),
              patch.object(settings, "ENVIRONMENT", "local")):
            async with api(True, self.session_provider) as client:
                plan = (await client.post(RUNS, json=config)).json()
                binding = {"planId": plan["id"], "expectedPlanUpdatedAt": plan["updatedAt"],
                           "expectedGeneration": None, "thresholdMinutes": 5, "enabled": True,
                           "schemaCheckEnabled": True, "heapSize": {"minBytes": "1"}}
                saved = await client.put(route, json=binding)
                self.assertEqual(saved.status_code, 200, saved.text)
                baseline_route = route + "/schema-baseline"
                preview = await client.post(baseline_route + "/preview", json={"expectedGeneration": 1})
                self.assertEqual(preview.status_code, 200, preview.text)
                captured = await client.post(baseline_route, json={
                    "expectedGeneration": 1, "expectedFingerprint": preview.json()["fingerprint"],
                })
                self.assertEqual(captured.status_code, 200, captured.text)
                start = datetime.now(timezone.utc)
                self.assertEqual((await check(start))["checked"], 1)
                opened = (await client.get(route)).json()["incidents"]
                self.assertFalse(opened["history"])
                self.assertEqual(len(opened["active"]), 1)
                heap = opened["active"][0]
                self.assertEqual((heap["kind"], heap["status"], heap["reason"]), ("heap_size", "open", "below_minimum"))
                acknowledgement = f"{route}/incidents/{heap['id']}/acknowledge"
                self.assertEqual((await client.post(acknowledgement, json={"expectedGeneration": 1})).status_code, 409)
                async with api(False, self.session_provider) as reader:
                    self.assertEqual((await reader.post(acknowledgement, json={"expectedGeneration": 2})).status_code, 403)
                first, replay = await asyncio.gather(*(client.post(acknowledgement, json={"expectedGeneration": 2})
                                                      for _ in range(2)))
                self.assertEqual(first.status_code, 200, first.text)
                self.assertEqual(first.json(), replay.json())
                self.assertEqual(first.json()["status"], "acknowledged")
                still_open = await check(start + timedelta(minutes=1))
                self.assertEqual(still_open["checked"], 1)
                ongoing = (await client.get(route)).json()["incidents"]["active"]
                self.assertEqual([(item["id"], item["status"]) for item in ongoing], [(heap["id"], "acknowledged")])
                await self.pg.execute("ALTER TABLE ts_transfer_sandbox.incident_target ALTER COLUMN value SET NOT NULL")
                await self.pg.execute("INSERT INTO ts_transfer_sandbox.incident_target VALUES (1, 'confirm heap')")
                await check(start + timedelta(minutes=2))
                states = (await client.get(route)).json()["incidents"]
                self.assertEqual([(i["id"], i["status"]) for i in states["history"]], [(heap["id"], "recovered")])
                again = await client.post(acknowledgement, json={"expectedGeneration": 2})
                self.assertEqual(again.status_code, 409, again.text)
                schema = states["active"][0]
                self.assertEqual((schema["kind"], schema["reason"]), ("schema", "schema_drift"))
                await self.pg.execute("ALTER TABLE ts_transfer_sandbox.incident_target ALTER COLUMN value DROP NOT NULL")
                await check(start + timedelta(minutes=3))
                resolved = (await client.get(route)).json()["incidents"]
                self.assertFalse(resolved["active"])
                self.assertEqual(resolved["history"][0]["id"], schema["id"])
                self.assertEqual(resolved["history"][0]["closeReason"], "healthy")
                await self.pg.execute("ALTER TABLE ts_transfer_sandbox.incident_target ALTER COLUMN value SET NOT NULL")
                await check(start + timedelta(minutes=4))
                new_schema = (await client.get(route)).json()["incidents"]["active"][0]
                self.assertNotEqual(new_schema["id"], schema["id"])
                await self.pg.execute("TRUNCATE ts_transfer_sandbox.incident_target")
                await check(start + timedelta(minutes=5))
                unknown = (await client.get(route)).json()["incidents"]["active"]
                self.assertEqual([i["id"] for i in unknown], [new_schema["id"]])
                paused = await client.put(route, json={**binding, "expectedGeneration": 2, "enabled": False})
                self.assertEqual(paused.status_code, 200, paused.text)
                incidents = paused.json()["incidents"]
                self.assertFalse(incidents["active"])
                ended = next(item for item in incidents["history"] if item["id"] == new_schema["id"])
                self.assertEqual((ended["status"], ended["closeReason"]), ("superseded", "policy_paused"))

    async def test_sandbox_data_and_receipt_commit_together_without_duplicate_replay(self):
        async with self.pg.session() as session:
            for sql in (
                "CREATE SCHEMA ts_transfer_sandbox",
                "CREATE TABLE ts_transfer_sandbox.from_rows (id int NOT NULL, value text)",
                "CREATE TABLE ts_transfer_sandbox.to_rows (id int PRIMARY KEY, value text)",
                "INSERT INTO ts_transfer_sandbox.from_rows VALUES (1, 'first'), (2, 'second')",
                "CREATE TABLE ts_transfer_sandbox.duplicate_rows (id int NOT NULL, value text)",
                "INSERT INTO ts_transfer_sandbox.duplicate_rows VALUES (3, 'one'), (3, 'two')",
                "CREATE TABLE ts_transfer_sandbox.to_upsert (id int PRIMARY KEY, value text)",
                "INSERT INTO ts_transfer_sandbox.to_upsert VALUES (3, 'original')",
                "CREATE TABLE ts_transfer_sandbox.too_many (id int NOT NULL, value text)",
                "INSERT INTO ts_transfer_sandbox.too_many SELECT n, 'large' FROM generate_series(1, 101) n",
            ):
                await session.execute(text(sql))
        config = {**PAYLOAD, "sourceSchema": "ts_transfer_sandbox", "targetSchema": "ts_transfer_sandbox",
                  "sourceTable": "from_rows", "targetTable": "to_rows"}
        with patch("app.web.services.transfer_plans.get_etl_layers", return_value=self.layers):
            async with api(True, self.session_provider) as client:
                created = await client.post(RUNS, json=config)
                self.assertEqual(created.status_code, 201, created.text)
                plan = created.json()
                path = f"{RUNS}/{plan['id']}/runs"
                payload = {"requestId": str(uuid4()), "expectedUpdatedAt": plan["updatedAt"]}
                with patch.object(settings, "TRANSFER_SANDBOX_ENABLED", False):
                    self.assertTrue((await client.post(path, json=payload)).is_error)
                self.assertEqual(await self.pg.fetch_all("SELECT * FROM ts_transfer_sandbox.to_rows"), [])
                with patch.object(settings, "TRANSFER_SANDBOX_ENABLED", True), patch.object(settings, "ENVIRONMENT", "local"):
                    stale = await client.post(path, json={**payload, "expectedUpdatedAt": "2000-01-01T00:00:00Z"})
                    self.assertEqual(stale.status_code, 409, stale.text)
                    run = await client.post(path, json=payload)
                    self.assertTrue(run.is_success, run.text)
                    receipt = run.json()
                    self.assertEqual(receipt["status"], "committed")
                    self.assertEqual(receipt["rowsAffected"], 2)
                    replay = await client.post(path, json=payload)
                    self.assertEqual(replay.json(), receipt)
                    duplicate = await client.post(path, json={**payload, "requestId": str(uuid4())})
                    self.assertEqual(duplicate.status_code, 409, duplicate.text)
                    self.assertEqual((await client.get(path)).json(), [receipt])
                    for table in ("from_rows", "to_rows"):
                        rows = await client.get(f"/etl/ts_transfer_sandbox/{table}/transfer-runs")
                        self.assertEqual(rows.json(), [receipt])
                    for src, mode in (("duplicate_rows", "upsert"), ("too_many", "append")):
                        unsafe = await client.post(RUNS, json={**config, "sourceTable": src, "targetTable": "to_upsert",
                                                               "writeMode": mode})
                        self.assertEqual(unsafe.status_code, 201, unsafe.text)
                        candidate = unsafe.json()
                        attempt = f"{RUNS}/{candidate['id']}/runs"
                        failure = await client.post(attempt, json={
                            "requestId": str(uuid4()), "expectedUpdatedAt": candidate["updatedAt"],
                        })
                        self.assertEqual(failure.status_code, 422, failure.text)
                        self.assertEqual((await client.get(attempt)).json(), [])
                    await self.pg.execute("INSERT INTO ts_transfer_sandbox.to_upsert VALUES (1, 'before upsert')")
                    upserted = await client.post(RUNS, json={**config, "targetTable": "to_upsert", "writeMode": "upsert"})
                    upsert_plan = upserted.json()
                    result = await client.post(f"{RUNS}/{upsert_plan['id']}/runs", json={
                        "requestId": str(uuid4()), "expectedUpdatedAt": upsert_plan["updatedAt"],
                    })
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertEqual(result.json()["rowsAffected"], 2)
        target = await self.pg.fetch_all("SELECT id, value FROM ts_transfer_sandbox.to_rows ORDER BY id")
        self.assertEqual([(row["id"], row["value"]) for row in target], [(1, "first"), (2, "second")])
        checked = await self.pg.fetch_all("SELECT id, value FROM ts_transfer_sandbox.to_upsert ORDER BY id")
        self.assertEqual([(row["id"], row["value"]) for row in checked], [(1, "first"), (2, "second"), (3, "original")])

    async def session_provider(self):
        async with self.pg.session() as session:
            yield session

    async def test_persists_filters_updates_and_never_changes_warehouse_rows(self):
        with patch("app.web.services.transfer_plans.get_etl_layers", return_value=self.layers):
            async with api(True, self.session_provider) as client:
                created = await client.post("/etl/transfer-plans", json=PAYLOAD)
                self.assertEqual(created.status_code, 201, created.text)
                saved = created.json()
                self.assertEqual(saved["status"], "draft")
                self.assertEqual(saved["name"], PAYLOAD["name"])
                self.assertIsNone(saved["scheduleTime"])
                for schema in ("ts_rds", "ts_ods"):
                    self.assertEqual((await client.get(f"/etl/transfer-plans?schema={schema}")).json(), [saved])
                self.assertEqual((await client.get("/etl/transfer-plans?schema=ts_other")).json(), [])
                identifier = saved["id"]
                new = {**PAYLOAD, "name": "Daily import", "trigger": "scheduled",
                       "scheduleTime": "04:45", "writeMode": "upsert"}
                changed = await client.put(f"/etl/transfer-plans/{identifier}", json=new)
                self.assertEqual(changed.status_code, 200, changed.text)
                self.assertEqual(changed.json()["id"], identifier)
                self.assertEqual(changed.json()["status"], "draft")
                self.assertEqual(changed.json()["scheduleTime"], "04:45")
                self.assertEqual((await client.get("/etl/transfer-plans")).json(), [changed.json()])
                self.assertEqual((await client.put(f"/etl/transfer-plans/{uuid4()}", json=new)).status_code, 404)
                for invalid, status in (
                    ({"targetTable": "missing"}, 404), ({"targetTable": "hidden"}, 404),
                    ({"targetTable": "no_pk", "writeMode": "upsert"}, 422),
                    ({"targetSchema": "ts_meta", "targetTable": "reserved"}, 404),
                ):
                    response = await client.post("/etl/transfer-plans", json={**PAYLOAD, **invalid})
                    self.assertEqual(response.status_code, status, response.text)
        source = await self.pg.fetch_all("SELECT id FROM ts_rds.rds_financial_fact ORDER BY id")
        target = await self.pg.fetch_all("SELECT id FROM ts_ods.ods_financial_fact ORDER BY id")
        self.assertEqual([row["id"] for row in source], [1, 2])
        self.assertEqual([row["id"] for row in target], [3])

    async def test_downgrade_only_removes_transfer_configuration_table(self):
        async with self.pg.session() as session:
            await self.migrate(session, self.timeline_migration.downgrade)
            await self.migrate(session, self.incident_migration.downgrade)
            await self.migrate(session, self.quality_migration.downgrade)
            await self.migrate(session, self.freshness_migration.downgrade)
            await self.migrate(session, self.compound_migration.downgrade)
            await self.migrate(session, self.upstream_migration.downgrade)
            await self.migrate(session, self.schedule_migration.downgrade)
            await self.migrate(session, self.incremental_migration.downgrade)
            await self.migrate(session, self.run_migration.downgrade)
            await self.migrate(session, self.migration.downgrade)
        rows = await self.pg.fetch_one("""
            SELECT to_regclass('public.ontology_transfer_plans') IS NULL AS removed,
                   to_regclass('public.ontology_objects') IS NOT NULL AS ontology_preserved,
                   (SELECT count(*) FROM ts_rds.rds_financial_fact) = 2 AS warehouse_preserved
        """)
        self.assertTrue(all(rows.values()))

    async def test_scheduled_utc_slot_is_durable_unique_and_separate_from_commits(self):
        await self.pg.execute("CREATE SCHEMA ts_transfer_sandbox")
        await self.pg.execute("CREATE TABLE ts_transfer_sandbox.schedule_src (id int PRIMARY KEY)")
        await self.pg.execute("CREATE TABLE ts_transfer_sandbox.schedule_dst (id int PRIMARY KEY)")
        await self.pg.execute("INSERT INTO ts_transfer_sandbox.schedule_src VALUES (1)")
        previous = datetime(2026, 11, 17, 3, 59, tzinfo=timezone.utc)
        due = previous + timedelta(minutes=1)
        payload = {**PAYLOAD, "sourceSchema": "ts_transfer_sandbox", "targetSchema": "ts_transfer_sandbox",
                   "sourceTable": "schedule_src", "targetTable": "schedule_dst", "writeMode": "upsert",
                   "trigger": "scheduled", "scheduleTime": "04:00"}
        with (
            patch("app.web.services.transfer_plans.get_etl_layers", return_value=self.layers),
            patch("app.web.services.transfer_schedules._utc_now", return_value=previous),
            patch.object(settings, "TRANSFER_SANDBOX_ENABLED", True),
            patch.object(settings, "TRANSFER_SANDBOX_SCHEDULER_ENABLED", True),
            patch.object(settings, "ENVIRONMENT", "local"),
        ):
            async with api(True, self.session_provider) as client:
                created = await client.post(RUNS, json=payload)
                self.assertEqual(created.status_code, 201, created.text)
                plan = created.json()
                cfg = f"{RUNS}/{plan['id']}/schedule"
                arm = await client.put(cfg, json={"expectedUpdatedAt": plan["updatedAt"], "enabled": True})
                self.assertEqual(arm.status_code, 200, arm.text)
                self.assertTrue(arm.json()["enabled"])
                async with self.pg.session() as session:
                    self.assertEqual(await collect_due_transfer_jobs(session, now=previous), [])
                    jobs = await collect_due_transfer_jobs(session, now=due)
                    self.assertEqual(len(jobs), 1)
                    self.assertEqual(await collect_due_transfer_jobs(session, now=due), [])
                history = (await client.get(cfg + "/jobs")).json()
                self.assertEqual([(row["id"], row["state"], row["receiptId"]) for row in history],
                                 [(str(jobs[0]), "pending", None)])
                self.assertEqual(await self.pg.fetch_all("SELECT * FROM ts_transfer_sandbox.schedule_dst"), [])
                paused = await client.put(cfg, json={"expectedUpdatedAt": plan["updatedAt"], "enabled": False})
                self.assertFalse(paused.json()["enabled"])
                async with self.pg.session() as session:
                    self.assertEqual(await collect_due_transfer_jobs(session, now=due + timedelta(days=1)), [])

    async def test_incremental_append_drains_in_order_and_a_failed_batch_does_not_advance(self):
        first = 9007199254740993
        async with self.pg.session() as session:
            for sql in (
                "CREATE SCHEMA ts_transfer_sandbox",
                "CREATE TABLE ts_transfer_sandbox.cursor_source (id bigint PRIMARY KEY, value text)",
                "CREATE TABLE ts_transfer_sandbox.cursor_target (id bigint PRIMARY KEY, value text)",
            ):
                await session.execute(text(sql))
            await session.execute(text("""INSERT INTO ts_transfer_sandbox.cursor_source
                SELECT CAST(:first AS bigint) + i, 'synthetic' FROM generate_series(0, 200) i"""), {"first": first})
        config = {**PAYLOAD, "sourceSchema": "ts_transfer_sandbox", "targetSchema": "ts_transfer_sandbox",
                  "sourceTable": "cursor_source", "targetTable": "cursor_target", "incrementalCursor": "id"}
        with (
            patch("app.web.services.transfer_plans.get_etl_layers", return_value=self.layers),
            patch.object(settings, "TRANSFER_SANDBOX_ENABLED", True),
            patch.object(settings, "ENVIRONMENT", "local"),
        ):
            async with api(True, self.session_provider) as client:
                created = await client.post(RUNS, json=config)
                self.assertEqual(created.status_code, 201, created.text)
                plan = created.json()
                path = f"{RUNS}/{plan['id']}/runs"
                original = {"requestId": str(uuid4()), "expectedUpdatedAt": plan["updatedAt"]}

                async def run(payload):
                    response = await client.post(path, json=payload)
                    self.assertTrue(response.is_success, response.text)
                    return response.json()

                initial = await run(original)
                self.assertEqual((initial["rowsAffected"], initial["cursorThrough"], initial["hasMore"]), (100, str(first + 99), True))
                self.assertEqual(await run(original), initial)
                bad_key = first + 100
                await self.pg.execute("INSERT INTO ts_transfer_sandbox.cursor_target VALUES (:id, 'collision')", {"id": bad_key})
                second_request = {**original, "requestId": str(uuid4())}
                rejected = await client.post(path, json=second_request)
                self.assertEqual(rejected.status_code, 422, rejected.text)
                self.assertEqual((await client.get(path)).json(), [initial])
                await self.pg.execute("DELETE FROM ts_transfer_sandbox.cursor_target WHERE id = :id", {"id": bad_key})
                for count, boundary, more, payload in (
                    (100, first + 199, True, second_request),
                    (1, first + 200, False, {**original, "requestId": str(uuid4())}),
                    (0, first + 200, False, {**original, "requestId": str(uuid4())}),
                ):
                    receipt = await run(payload)
                    self.assertEqual((receipt["rowsAffected"], receipt["cursorThrough"], receipt["hasMore"]),
                                     (count, str(boundary), more))
                attempts = await client.get(f"{RUNS}/{plan['id']}/attempts")
                self.assertEqual(attempts.status_code, 200, attempts.text)
                self.assertTrue({"committed", "replayed", "rejected"} <= {item["status"] for item in attempts.json()})
        target = await self.pg.fetch_all("SELECT id FROM ts_transfer_sandbox.cursor_target ORDER BY id")
        self.assertEqual([row["id"] for row in target], list(range(first, first + 201)))

    async def test_preflight_uses_strict_catalog_metadata_only(self):
        async with self.pg.session() as session:
            for sql in (
                "ALTER TABLE ts_ods.ods_financial_fact ADD COLUMN discarded text",
                "ALTER TABLE ts_ods.ods_financial_fact DROP COLUMN discarded",
                "CREATE TABLE ts_rds.people (id bigint NOT NULL, name varchar(80), amount numeric(12,2), doubled numeric)",
                """CREATE TABLE ts_ods.people (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                   name varchar(80), amount numeric(12,2), doubled numeric GENERATED ALWAYS AS (amount * 2) STORED)""",
                "CREATE TABLE ts_rds.shape (id integer NOT NULL, name varchar(80), amount numeric(12,2), extra text)",
                """CREATE TABLE ts_ods.shape (id integer PRIMARY KEY DEFERRABLE INITIALLY IMMEDIATE,
                   amount numeric(12,3), name varchar(81) NOT NULL, other text)""",
            ):
                await session.execute(text(sql))
        endpoint = "/etl/transfer-plans/preflight"
        with patch("app.web.services.transfer_plans.get_etl_layers", return_value=self.layers):
            async with api(True, self.session_provider) as client:
                response = await client.post(endpoint, json={**PAYLOAD, "writeMode": "upsert"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"status": "compatible", "issues": [], "checkedColumns": 1})
                response = await client.post(endpoint, json={**PAYLOAD, "sourceTable": "people", "targetTable": "people"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "blocked")
                self.assertEqual(response.json()["checkedColumns"], 4)
                self.assertEqual({(item["code"], item.get("column")) for item in response.json()["issues"]},
                                 {("target_identity_always", "id"), ("target_generated", "doubled")})
                schema_mismatch = {**PAYLOAD, "sourceTable": "shape", "targetTable": "shape", "writeMode": "upsert"}
                response = await client.post(endpoint, json=schema_mismatch)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "blocked")
                self.assertEqual(response.json()["checkedColumns"], 3)
                self.assertEqual({(item["code"], item.get("column")) for item in response.json()["issues"]}, {
                    ("target_primary_key_unusable", None), ("column_type_mismatch", "name"),
                    ("column_type_mismatch", "amount"), ("column_nullability_mismatch", "name"),
                    ("source_column_missing", "other"), ("target_column_missing", "extra"),
                })
                for target_schema, target in (("ts_ods", "hidden"), ("ts_meta", "reserved"), ("ts_ods", "missing")):
                    response = await client.post(endpoint, json={**PAYLOAD, "targetSchema": target_schema,
                                                                 "targetTable": target})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["checkedColumns"], 0)
                    self.assertEqual([item["code"] for item in response.json()["issues"]], ["target_unavailable"])
                response = await client.post(endpoint, json={**PAYLOAD, "sourceTable": "missing", "targetTable": "missing"})
                self.assertEqual([item["code"] for item in response.json()["issues"]],
                                 ["source_unavailable", "target_unavailable"])
                self.assertEqual((await client.post(endpoint, json={**PAYLOAD, "targetTable": "bad.name"})).status_code, 422)
                self.assertEqual((await client.get("/etl/transfer-plans")).json(), [])
        target = await self.pg.fetch_all("SELECT id FROM ts_ods.ods_financial_fact ORDER BY id")
        self.assertEqual([row["id"] for row in target], [3])

    async def test_upstream_commit_events_and_compound_bindings_execute_only_reviewed_sets(self):
        async with self.pg.session() as session:
            for sql in (
                "CREATE TYPE public.statustypes AS ENUM ('ENABLE','DISABLE')",
                'CREATE TABLE public."user" (id uuid PRIMARY KEY, status public.statustypes, is_superuser bool)',
                "CREATE SCHEMA ts_transfer_sandbox",
                "CREATE TABLE ts_transfer_sandbox.a (id integer PRIMARY KEY, value text)",
                "CREATE TABLE ts_transfer_sandbox.b (id integer PRIMARY KEY, value text)",
                "CREATE TABLE ts_transfer_sandbox.c (id integer PRIMARY KEY, value text)",
                "INSERT INTO ts_transfer_sandbox.a VALUES (1,'before arming')",
            ):
                await session.execute(text(sql))
            await session.execute(text('INSERT INTO public."user" VALUES (:id, \'ENABLE\', true)'), {"id": TEST_ACTOR})
        self.addAsyncCleanup(self.pg.execute, "DROP TYPE public.statustypes")
        self.addAsyncCleanup(self.pg.execute, 'DROP TABLE public."user"')

        async def collect(now=None):
            async with self.pg.session() as session:
                return await collect_due_upstream_transfer_jobs(session, now=now or datetime.now(timezone.utc))

        async def execute(job, now=None):
            async with self.pg.session() as session:
                return await execute_upstream_transfer_job_in_session(session, job, now=now)

        async with api(True, self.session_provider) as client:
            with (
                patch("app.web.services.transfer_plans.get_etl_layers", return_value=self.layers),
                patch.object(settings, "ENVIRONMENT", "local"),
                patch.object(settings, "TRANSFER_SANDBOX_ENABLED", True),
                patch.object(settings, "TRANSFER_SANDBOX_UPSTREAM_ENABLED", True),
            ):
                base = {**PAYLOAD, "sourceSchema": "ts_transfer_sandbox", "targetSchema": "ts_transfer_sandbox", "writeMode": "upsert"}

                async def create(source, target, trigger):
                    response = await client.post(RUNS, json={**base, "sourceTable": source, "targetTable": target,
                                                             "trigger": trigger})
                    self.assertEqual(response.status_code, 201, response.text)
                    return response.json()

                async def run(plan, key):
                    return await client.post(f"{RUNS}/{plan['id']}/runs", json={
                        "requestId": str(key), "expectedUpdatedAt": plan["updatedAt"],
                    })

                parent = await create("a", "b", "manual")
                child = await create("b", "c", "upstream")
                path = f"{RUNS}/{child['id']}/upstream-trigger"
                first = await run(parent, uuid4())
                self.assertEqual(first.status_code, 200, first.text)
                self.assertEqual([row["id"] for row in (await client.get(path + "/candidates")).json()], [parent["id"]])
                desired = {"enabled": True, "operator": "OR",
                           "parents": [{"planId": parent["id"], "expectedUpdatedAt": parent["updatedAt"]}],
                           "expectedUpdatedAt": child["updatedAt"], "expectedGeneration": None}
                armed = await client.put(path, json=desired)
                self.assertEqual(armed.status_code, 200, armed.text)
                self.assertTrue(armed.json()["enabled"])
                self.assertEqual(await collect(), [])  # The first positive event predates arming.
                await self.pg.execute("DELETE FROM ts_transfer_sandbox.a")
                self.assertEqual((await run(parent, uuid4())).json()["rowsAffected"], 0)
                self.assertEqual(await collect(), [])
                await self.pg.execute("INSERT INTO ts_transfer_sandbox.a VALUES(1,'actual event')")
                key = uuid4()
                after_arm = await run(parent, key)
                self.assertEqual(after_arm.status_code, 200, after_arm.text)
                self.assertEqual((await run(parent, key)).json(), after_arm.json())
                job, = await collect()
                self.assertEqual(await collect(), [])
                self.assertEqual((await run(child, job)).status_code, 422)  # No manual use of reserved worker ID.
                self.assertEqual(await execute(job), "committed")
                self.assertEqual(await execute(job), "committed")
                jobs = (await client.get(path + "/jobs")).json()
                self.assertEqual(jobs[0]["events"][0]["parentReceiptId"], after_arm.json()["id"])
                self.assertNotEqual(jobs[0]["receiptId"], jobs[0]["events"][0]["parentReceiptId"])
                self.assertEqual(len((await client.get(f"{RUNS}/{child['id']}/runs")).json()), 1)
                self.assertEqual((await self.pg.fetch_one("SELECT count(*) AS count FROM ontology_transfer_commit_events"))["count"], 2)
                self.assertEqual((await self.pg.fetch_one("SELECT value FROM ts_transfer_sandbox.c"))["value"], "actual event")
                await self.pg.execute("UPDATE ts_transfer_sandbox.a SET value='paused'")
                self.assertEqual((await run(parent, uuid4())).status_code, 200)
                queued, = await collect()
                pause = await client.put(path, json={**desired, "enabled": False, "parents": [], "expectedGeneration": 1})
                self.assertEqual(pause.status_code, 200, pause.text)
                self.assertEqual(await execute(queued), "rejected")
                self.assertEqual((await self.pg.fetch_one("SELECT value FROM ts_transfer_sandbox.c"))["value"], "actual event")
                rearmed = await client.put(path, json={**desired, "expectedGeneration": 2})
                self.assertEqual(rearmed.status_code, 200, rearmed.text)
                self.assertEqual(await collect(), [])
                self.assertEqual((await run(parent, uuid4())).status_code, 200)
                rewritten, = await collect()
                await self.pg.execute("TRUNCATE ts_transfer_sandbox.c")
                self.assertEqual(await execute(rewritten), "rejected")
                self.assertEqual(await self.pg.fetch_all("SELECT * FROM ts_transfer_sandbox.c"), [])

                # Rearming explicitly reviews the changed physical destination.
                reviewed = await client.put(path, json={**desired, "expectedGeneration": 3})
                self.assertEqual(reviewed.status_code, 200, reviewed.text)
                self.assertEqual((await run(parent, uuid4())).status_code, 200)
                self.assertEqual(await collect(datetime.now(timezone.utc) + timedelta(minutes=6)), [])
                self.assertEqual((await client.get(path + "/jobs")).json()[0]["state"], "expired")
                self.assertEqual(await self.pg.fetch_all("SELECT * FROM ts_transfer_sandbox.c"), [])

                # A genuine AND set retains an earlier unmatched event across
                # the five-minute horizon and emits exactly one child job.
                await self.pg.execute("CREATE TABLE ts_transfer_sandbox.d (id integer PRIMARY KEY, value text)")
                await self.pg.execute("INSERT INTO ts_transfer_sandbox.d VALUES (2,'second producer')")
                second = await create("d", "b", "manual")
                compound = {**desired, "operator": "AND", "expectedGeneration": 4,
                            "parents": [*desired["parents"], {"planId": second["id"], "expectedUpdatedAt": second["updatedAt"]}]}
                duplicate = await client.put(path, json={**compound, "parents": compound["parents"] + desired["parents"]})
                self.assertEqual(duplicate.status_code, 422, duplicate.text)
                armed = await client.put(path, json=compound)
                self.assertEqual(armed.status_code, 200, armed.text)
                self.assertEqual(armed.json()["operator"], "AND")
                self.assertEqual(len(armed.json()["parents"]), 2)
                first = await run(parent, uuid4())
                self.assertEqual(first.status_code, 200, first.text)
                async with self.pg.session() as session:
                    await session.execute(text(
                        "UPDATE ontology_transfer_commit_events SET recorded_at = recorded_at - interval '6 minutes' "
                        "WHERE parent_receipt_id = :receipt"
                    ), {"receipt": first.json()["id"]})
                self.assertEqual(await collect(), [])
                second_receipt = await run(second, uuid4())
                self.assertEqual(second_receipt.status_code, 200, second_receipt.text)
                left, right = await asyncio.gather(collect(), collect())
                job, = left + right
                self.assertEqual(await execute(job), "committed")
                self.assertEqual(await execute(job), "committed")
                self.assertEqual(await collect(), [])
                jobs = (await client.get(path + "/jobs")).json()
                self.assertEqual(jobs[0]["operator"], "AND")
                self.assertEqual({event["parentReceiptId"] for event in jobs[0]["events"]},
                                 {first.json()["id"], second_receipt.json()["id"]})
                self.assertIsNotNone(jobs[0]["receiptId"])
                first_extra = await run(parent, uuid4())
                self.assertEqual(first_extra.status_code, 200, first_extra.text)
                self.assertEqual(await collect(), [])  # Prior second event was already consumed.

                # Changing operator starts a new reviewed generation. OR does
                # not replay the prior unpaired event, and emits one per input.
                or_change = {**compound, "operator": "OR", "expectedGeneration": 5}
                changed = await client.put(path, json=or_change)
                self.assertEqual(changed.status_code, 200, changed.text)
                self.assertEqual(await collect(), [])
                a, d = await run(parent, uuid4()), await run(second, uuid4())
                self.assertEqual((a.status_code, d.status_code), (200, 200))
                or_jobs = await collect()
                self.assertEqual(len(or_jobs), 2)
                self.assertEqual([await execute(job) for job in or_jobs], ["committed", "committed"])
                jobs = (await client.get(path + "/jobs")).json()
                self.assertTrue(all(job["operator"] == "OR" and len(job["events"]) == 1 for job in jobs[:2]))
                self.assertEqual({job["events"][0]["parentReceiptId"] for job in jobs[:2]}, {a.json()["id"], d.json()["id"]})
                self.assertEqual(await collect(), [])


    async def test_exact_destination_timeline_pins_attempts_and_preserves_receipt_identity(self):
        await self.pg.execute("CREATE SCHEMA ts_transfer_sandbox")
        for table in ("timeline_source", "timeline_target", "timeline_other"):
            await self.pg.execute(f"CREATE TABLE ts_transfer_sandbox.{table} (id integer PRIMARY KEY)")
        await self.pg.execute("INSERT INTO ts_transfer_sandbox.timeline_source VALUES (1)")
        route = "/etl/ts_transfer_sandbox/timeline_target/transfer-timeline"
        async with api(True, self.session_provider) as client:
            with (
                patch("app.web.services.transfer_plans.get_etl_layers", return_value=self.layers),
                patch.object(settings, "ENVIRONMENT", "local"),
                patch.object(settings, "TRANSFER_SANDBOX_ENABLED", True),
            ):
                proposed = {**PAYLOAD, "sourceSchema": "ts_transfer_sandbox", "sourceTable": "timeline_source",
                            "targetSchema": "ts_transfer_sandbox", "targetTable": "timeline_target",
                            "writeMode": "upsert", "trigger": "manual"}
                resp = await client.post(RUNS, json=proposed)
                self.assertEqual(resp.status_code, 201, resp.text)
                plan = resp.json()
                action = f"{RUNS}/{plan['id']}/runs"
                request = {"requestId": str(uuid4()), "expectedUpdatedAt": plan["updatedAt"]}
                sent = await client.post(action, json=request)
                self.assertEqual(sent.status_code, 200, sent.text)
                receipt = sent.json()
                self.assertEqual((await client.post(action, json=request)).json(), receipt)
                stale = await client.post(action, json={**request, "requestId": str(uuid4()),
                                                        "expectedUpdatedAt": "2020-01-01T00:00:00Z"})
                self.assertEqual(stale.status_code, 409)

                await self.pg.execute("INSERT INTO ts_transfer_sandbox.timeline_source SELECT generate_series(2,101)")
                oversized = await client.post(action, json={**request, "requestId": str(uuid4())})
                self.assertEqual(oversized.status_code, 422, oversized.text)

                encountered = []
                cursor = None
                while True:
                    response = await client.get(route, params={"limit": 1, **({"cursor": cursor} if cursor else {})})
                    self.assertEqual(response.status_code, 200, response.text)
                    body = response.json()
                    self.assertEqual(body["latestCurrentReceipt"], receipt)
                    encountered.extend(body["entries"])
                    cursor = body["nextCursor"]
                    if cursor is None:
                        break
                    wrong = await client.get("/etl/ts_transfer_sandbox/timeline_other/transfer-timeline",
                                             params={"cursor": cursor})
                    self.assertEqual(wrong.status_code, 422)
                self.assertEqual([entry["status"] for entry in encountered],
                                 ["rejected", "replayed", "committed", "committed"])
                self.assertEqual([entry["kind"] for entry in encountered], ["attempt", "attempt", "receipt", "attempt"])
                self.assertTrue(all(entry["physicalScope"] == "current" for entry in encountered))
                self.assertTrue(all(entry["trigger"] == "manual" for entry in encountered))
                self.assertEqual(encountered[2]["occurredAt"], receipt["recordedAt"])
                self.assertIsNone(encountered[0]["receiptId"])
                detail = (await client.get(f"{route}/attempt/{encountered[0]['id']}")).json()
                self.assertIsNone(detail["job"])
                self.assertGreaterEqual(detail["finishedAt"], detail["occurredAt"])
                self.assertEqual((await client.get(
                    f"/etl/ts_transfer_sandbox/timeline_source/transfer-timeline/receipt/{receipt['id']}",
                )).status_code, 404)
                self.assertEqual((await client.get("/etl/ts_ods/ods_financial_fact/transfer-timeline")).status_code, 404)

                # Kind is a distinct tie-breaker even if a legacy importer
                # gave an attempt and receipt identical identities/timestamps.
                async with self.pg.session() as session:
                    await session.execute(text(
                        "UPDATE ontology_transfer_attempts SET id = :receipt, started_at = :stamp WHERE id = :attempt",
                    ), {"receipt": receipt["id"], "stamp": datetime.fromisoformat(receipt["recordedAt"]),
                        "attempt": encountered[3]["id"]})
                first_page = (await client.get(route, params={"limit": 3})).json()
                last_page = (await client.get(route, params={"cursor": first_page["nextCursor"]})).json()
                self.assertEqual(first_page["entries"][-1]["kind"], "receipt")
                self.assertEqual([(row["kind"], row["id"]) for row in last_page["entries"]],
                                 [("attempt", receipt["id"])])

                # An imported impossible state does not become successful
                # provenance in the destination view without an actual receipt.
                async with self.pg.session() as session:
                    await session.execute(text(
                        "UPDATE ontology_transfer_attempts SET receipt_id = NULL WHERE id = :attempt",
                    ), {"attempt": receipt["id"]})
                damaged = (await client.get(f"{route}/attempt/{receipt['id']}")).json()
                self.assertEqual(damaged["status"], "outcome_unknown")
                self.assertIsNone(damaged["receiptId"])
                manual_without_proof = (await client.get(f"{route}/receipt/{receipt['id']}")).json()
                self.assertIsNone(manual_without_proof["trigger"])

                changed = await client.put(f"{RUNS}/{plan['id']}", json={**proposed, "targetTable": "timeline_other"})
                self.assertEqual(changed.status_code, 200, changed.text)
                other = (await client.get("/etl/ts_transfer_sandbox/timeline_other/transfer-timeline")).json()
                self.assertEqual(other["entries"], [])
                await self.pg.execute("TRUNCATE ts_transfer_sandbox.timeline_target")
                historical = (await client.get(route)).json()
                self.assertIsNone(historical["latestCurrentReceipt"])
                self.assertTrue(all(item["physicalScope"] == "historical" for item in historical["entries"]))
        async with api(False, self.session_provider) as reader:
            self.assertEqual((await reader.get(route)).status_code, 403)
