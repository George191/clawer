"""Synthetic CDC fixture playback; an explicit isolated PostgreSQL URL enables writes."""

import asyncio
import json
from importlib import import_module
import os
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.postgres_client import PostgresClient
from app.web.api.models.cdc_sandbox import CdcSandboxApply, CdcSandboxReview
from app.web.api.models.pipeline_sandbox import PipelineSandboxConfig, PipelineSandboxSave
from app.web.api.models.pipeline_sandbox_outputs import PipelineSandboxCommit
from app.web.core.config import settings
from app.web.core.config.sandbox_handoff import HandoffCopyGrant, HandoffSinkGrant
from app.web.services.etl_layers import EtlLayers
from app.web.tests.test_transfer_plans import TEST_ACTOR, api

_URL = os.getenv("ONTOLOGY_CDC_SANDBOX_TEST_URL")
_DB = "ontology_cdc_regression"
_ROUTE = "/etl/cdc-sandbox"
_BUILDER = "/etl/pipeline-sandbox"
_OUTPUTS = _BUILDER + "/outputs"
_MARKER = uuid4()
_REVIEW = {"expectedGeneration": None}


def payload(after: int, request_id=None) -> dict:
    return {"requestId": str(request_id or uuid4()), "expectedGeneration": 1, "expectedAppliedThrough": str(after)}


class CdcSandboxContract(unittest.IsolatedAsyncioTestCase):
    def test_fixed_request_scope_and_cursor(self):
        CdcSandboxReview.model_validate(_REVIEW)
        CdcSandboxApply.model_validate(payload(9223372036854775807))
        for invalid in (
            {"expectedGeneration": 2}, {"expectedAppliedThrough": "00"}, {"expectedAppliedThrough": "-1"},
            {"expectedAppliedThrough": "9223372036854775808"}, {"expectedAppliedThrough": 0},
            {"source": "arbitrary"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                CdcSandboxApply.model_validate({**payload(0), **invalid})
        with self.assertRaises(ValidationError):
            CdcSandboxReview.model_validate({**_REVIEW, "target": "arbitrary"})

    async def test_nonadmin_and_default_off_have_no_fixture_dependency(self):
        with patch.object(settings, "CDC_SANDBOX_ENABLED", False):
            async with api(True) as client:
                self.assertFalse((await client.get(_ROUTE)).json()["available"])
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 403)
                self.assertEqual((await client.post(_ROUTE + "/runs", json=payload(0))).status_code, 403)
        async with api(False) as client:
            self.assertEqual((await client.get(_ROUTE)).status_code, 403)
            self.assertEqual((await client.get(_ROUTE + "/runs")).status_code, 403)
            self.assertEqual((await client.get(_ROUTE + "/versions")).status_code, 403)
            self.assertEqual((await client.get(_ROUTE + f"/versions/{uuid4()}")).status_code, 403)
            self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 403)
            self.assertEqual((await client.post(_ROUTE + "/runs", json=payload(0))).status_code, 403)


    def test_builder_rejects_arbitrary_operations_and_noncanonical_bigints(self):
        good = {"versionId": str(uuid4()), "filters": [], "projection": ["id", "value"]}
        PipelineSandboxSave.model_validate({"expectedGeneration": None, "config": good})
        for change in (
            {"sql": "SELECT arbitrary"}, {"schema": "public"}, {"graph": []},
            {"projection": ["id", "id"]}, {"projection": []}, {"projection": ["id", "*"]},
            {"filters": [{"field": "id", "operator": "eq", "value": "9223372036854775808"}]},
            {"filters": [{"field": "id", "operator": "eq", "value": "-0"}]},
            {"filters": [{"field": "id", "operator": "lte", "value": 1}]},
            {"filters": [{"field": "id", "operator": "prefix", "value": "1"}]},
            {"filters": [{"field": "value", "operator": "gte", "value": "a"}]},
            {"filters": [{"field": "value", "operator": "prefix", "value": "a" * 129}]},
            {"filters": [{"field": "value", "operator": "eq", "value": "bad\x00text"}]},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                PipelineSandboxConfig.model_validate({**good, **change})

    async def test_builder_is_default_off_and_reader_cannot_access(self):
        config = {"versionId": str(uuid4()), "filters": [], "projection": ["id"]}
        with patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_ENABLED", False):
            async with api(True) as client:
                self.assertEqual((await client.get(_BUILDER)).json(), {"available": False, "latestGeneration": None})
                self.assertEqual((await client.post(_BUILDER+"/preview", json=config)).status_code, 403)
                self.assertEqual((await client.get(_BUILDER+"/revisions")).status_code, 403)
        async with api(False) as client:
            self.assertEqual((await client.get(_BUILDER)).status_code, 403)
            self.assertEqual((await client.post(_BUILDER+"/preview", json=config)).status_code, 403)
            self.assertEqual((await client.post(
                _BUILDER+"/revisions", json={"expectedGeneration": None, "config": config},
            )).status_code, 403)


    async def test_builder_output_opt_in_and_strict_commit_payload(self):
        request = {"requestId": str(uuid4()), "expectedGeneration": 1, "expectedConfigHash": "a" * 64}
        PipelineSandboxCommit.model_validate(request)
        for invalid in ({"target": "arbitrary"}, {"expectedGeneration": "1"}, {"expectedConfigHash": "wrong"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                PipelineSandboxCommit.model_validate({**request, **invalid})
        with patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_OUTPUT_ENABLED", False):
            async with api(True) as client:
                self.assertEqual((await client.get(_OUTPUTS)).json(), {"available": False, "outputs": []})
                self.assertEqual((await client.post(_OUTPUTS, json=request)).status_code, 403)
        async with api(False) as client:
            self.assertEqual((await client.get(_OUTPUTS)).status_code, 403)
            self.assertEqual((await client.get(_OUTPUTS + f"/{uuid4()}")).status_code, 403)
            self.assertEqual((await client.post(_OUTPUTS, json=request)).status_code, 403)


@unittest.skipUnless(_URL, "Requires an isolated ontology_cdc_regression PostgreSQL database")
class CdcSandboxIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        url = make_url(_URL)
        if url.database != _DB or url.host != "127.0.0.1":
            raise RuntimeError("CDC tests refuse any destination outside the named loopback fixture")
        self.pg = PostgresClient(_URL)
        await self.pg.connect(max_retries=1)
        self.addAsyncCleanup(self.pg.close)
        self.assertEqual((await self.pg.fetch_one("SELECT current_database() AS name"))["name"], _DB)
        migrations = [import_module("app.web.alembic.versions." + name) for name in (
            "984b2ef31a6c_ontology_cdc_sandbox", "31d9685bf24a_ontology_cdc_sandbox_versions",
            "f5b873c94d20_ontology_pipeline_sandbox", "b46891e47c52_ontology_pipeline_sandbox_outputs",
            "8d659760c2b4_ontology_synthetic_markings", "51b18bf851dd_ontology_pipeline_match",
            "f27ad8ce609b_ontology_pipeline_restore_provenance",
        )]

        def upgrade(sync):
            with Operations.context(MigrationContext.configure(sync.connection())):
                for migration in migrations:
                    migration.upgrade()

        async with self.pg.session() as session:
            await session.run_sync(upgrade)
            for query in (
                "CREATE SCHEMA ts_cdc_sandbox",
                "CREATE TABLE ts_cdc_sandbox.stream_head (marker uuid PRIMARY KEY, ordinal bigint NOT NULL)",
                "CREATE TABLE ts_cdc_sandbox.event_log (ordinal bigint PRIMARY KEY, entity_id bigint NOT NULL, "
                "value text NOT NULL)",
                "CREATE TABLE ts_cdc_sandbox.target_rows (id bigint PRIMARY KEY, value text NOT NULL)",
                "CREATE TABLE ts_cdc_sandbox.source_rows (id bigint PRIMARY KEY, value text NOT NULL)",
            ):
                await session.execute(text(query))
            await session.execute(text("INSERT INTO ts_cdc_sandbox.stream_head VALUES (:marker, 0)"), {"marker": _MARKER})
        self.addAsyncCleanup(self.drop_fixture)

    async def sessions(self):
        async with self.pg.session() as session:
            yield session

    async def drop_fixture(self):
        await self.pg.execute("DROP TABLE ontology_pipeline_sandbox_output_rows, ontology_pipeline_sandbox_outputs")
        await self.pg.execute("DROP TABLE ontology_pipeline_sandbox_revisions")
        await self.pg.execute("DROP FUNCTION IF EXISTS app.ontology_pipeline_keep_restore_provenance()")
        await self.pg.execute("DROP TABLE ontology_cdc_sandbox_version_rows, ontology_cdc_sandbox_versions")
        await self.pg.execute("DROP TABLE ontology_cdc_sandbox_receipts, ontology_cdc_sandbox_binding")
        await self.pg.execute("DROP SCHEMA ts_cdc_sandbox CASCADE")

    async def produce(self, session, entity: int, value: str) -> int:
        ordinal = await session.scalar(text(
            "UPDATE ts_cdc_sandbox.stream_head SET ordinal = ordinal + 1 WHERE marker = :marker RETURNING ordinal",
        ), {"marker": _MARKER})
        args = {"ordinal": ordinal, "id": entity, "value": value}
        await session.execute(text("""
            INSERT INTO ts_cdc_sandbox.source_rows (id, value) VALUES (:id, :value)
            ON CONFLICT (id) DO UPDATE SET value = EXCLUDED.value
        """), args)
        await session.execute(text("INSERT INTO ts_cdc_sandbox.event_log VALUES (:ordinal, :id, :value)"), args)
        return ordinal

    async def test_committed_prefix_single_transaction_and_exact_replay_without_source(self):
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
        ):
            async with api(True, self.sessions) as client:
                self.assertEqual((await client.get(_ROUTE)).json()["state"], "unreviewed")
                self.assertEqual((await client.post(_ROUTE + "/runs", json=payload(0))).status_code, 409)
                review = await client.put(_ROUTE, json=_REVIEW)
                self.assertEqual(review.status_code, 200, review.text)
                self.assertEqual(review.json()["appliedThrough"], "0")
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 409)
                self.assertEqual((await client.post(_ROUTE + "/runs", json=payload(0))).status_code, 409)

                async with self.pg.session() as session:
                    self.assertEqual(await self.produce(session, 1, "old"), 1)
                    self.assertEqual(await self.produce(session, 2, "other"), 2)
                    self.assertEqual(await self.produce(session, 1, "final"), 3)
                    before_commit = (await client.get(_ROUTE)).json()
                    self.assertEqual(before_commit["sourceHighWater"], "0")
                    self.assertEqual((await client.post(_ROUTE + "/runs", json=payload(0))).status_code, 409)
                status = (await client.get(_ROUTE)).json()
                self.assertEqual((status["sourceHighWater"], status["appliedThrough"]), ("3", "0"))

                request, competitor = payload(0), payload(0)
                first, second = await asyncio.gather(client.post(_ROUTE + "/runs", json=request),
                                                     client.post(_ROUTE + "/runs", json=competitor))
                self.assertEqual(sorted([first.status_code, second.status_code]), [200, 409],
                                 (first.text, second.text))
                won_payload, receipt = (request, first.json()) if first.status_code == 200 else (competitor, second.json())
                self.assertEqual((receipt["fromExclusive"], receipt["appliedThrough"], receipt["rowsAffected"]),
                                 ("0", "3", 2))
                target = await self.pg.fetch_all("SELECT id, value FROM ts_cdc_sandbox.target_rows ORDER BY id")
                self.assertEqual([(row["id"], row["value"]) for row in target], [(1, "final"), (2, "other")])
                with patch("app.web.services.cdc_sandbox._lock_fixture",
                           side_effect=AssertionError("Receipt replay touched source")):
                    self.assertEqual((await client.post(_ROUTE + "/runs", json=won_payload)).json(), receipt)
                    changed = await client.post(_ROUTE + "/runs", json={**won_payload, "expectedAppliedThrough": "3"})
                    self.assertEqual(changed.status_code, 409)
                self.assertEqual((await client.get(_ROUTE + "/runs")).json(), [receipt])
                await self.pg.execute("TRUNCATE ts_cdc_sandbox.target_rows")
                self.assertEqual((await client.get(_ROUTE)).json()["state"], "blocked")
                self.assertEqual((await client.post(_ROUTE + "/runs", json=payload(3))).status_code, 409)
                self.assertEqual((await client.post(_ROUTE + "/runs", json=won_payload)).json(), receipt)

    async def test_missing_ordinal_and_budget_never_advance_past_unapplied_data(self):
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
        ):
            async with api(True, self.sessions) as client:
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 200)
                async with self.pg.session() as session:
                    # Deliberately corrupt the outbox to prove the consumer
                    # refuses to advance; the regular producer never skips a value.
                    await session.execute(text("INSERT INTO ts_cdc_sandbox.event_log VALUES (2,2,'later')"))
                    await session.execute(text("UPDATE ts_cdc_sandbox.stream_head SET ordinal=2"))
                missing = await client.post(_ROUTE + "/runs", json=payload(0))
                self.assertEqual(missing.status_code, 409, missing.text)
                self.assertEqual((await client.get(_ROUTE)).json()["appliedThrough"], "0")
                async with self.pg.session() as session:
                    await session.execute(text(
                        "INSERT INTO ts_cdc_sandbox.event_log VALUES (1,1,repeat('a',40000))",
                    ))
                    await session.execute(text("UPDATE ts_cdc_sandbox.event_log SET value = repeat('b',40000) WHERE ordinal=2"))
                    await session.execute(text("UPDATE ts_cdc_sandbox.stream_head SET ordinal=2"))
                first = await client.post(_ROUTE + "/runs", json=payload(0))
                self.assertEqual(first.status_code, 200, first.text)
                self.assertEqual(first.json()["appliedThrough"], "1")
                async with self.pg.session() as session:
                    self.assertEqual(await self.produce(session, 3, "c" * 65536), 3)
                too_large = await client.post(_ROUTE + "/runs", json=payload(1))
                self.assertEqual(too_large.status_code, 422, too_large.text)
                self.assertEqual((await client.get(_ROUTE)).json()["appliedThrough"], "1")
                self.assertEqual((await self.pg.fetch_one("SELECT count(*) AS n FROM ts_cdc_sandbox.target_rows"))["n"], 1)


    async def test_sink_checkpoint_and_receipt_rollback_together_and_lost_ack_replays(self):
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
        ):
            async with api(True, self.sessions) as client:
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 200)
                async with self.pg.session() as session:
                    self.assertEqual(await self.produce(session, 1, "final"), 1)
                    await session.execute(text("""
                        CREATE FUNCTION ts_cdc_sandbox.fail_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
                        BEGIN RAISE EXCEPTION 'fixture refused receipt'; END;
                        $$
                    """))
                    await session.execute(text("""
                        CREATE TRIGGER fail_receipt BEFORE INSERT ON ontology_cdc_sandbox_receipts
                        FOR EACH ROW EXECUTE FUNCTION ts_cdc_sandbox.fail_receipt()
                    """))
                request = payload(0)
                failed = await client.post(_ROUTE + "/runs", json=request)
                self.assertEqual(failed.status_code, 503, failed.text)
                self.assertEqual((await client.get(_ROUTE)).json()["appliedThrough"], "0")
                self.assertEqual(await self.pg.fetch_all("SELECT * FROM ts_cdc_sandbox.target_rows"), [])
                self.assertEqual((await client.get(_ROUTE + "/runs")).json(), [])
                await self.pg.execute("DROP TRIGGER fail_receipt ON ontology_cdc_sandbox_receipts")

                commit = AsyncSession.commit
                lost = False

                async def lose_ack(session):
                    nonlocal lost
                    await commit(session)
                    if not lost:
                        lost = True
                        raise RuntimeError("fixture dropped successful commit acknowledgement")

                with patch.object(AsyncSession, "commit", lose_ack):
                    unknown = await client.post(_ROUTE + "/runs", json=request)
                self.assertEqual(unknown.status_code, 503, unknown.text)
                self.assertEqual((await client.get(_ROUTE)).json()["appliedThrough"], "1")
                self.assertEqual((await self.pg.fetch_one("SELECT value FROM ts_cdc_sandbox.target_rows"))["value"], "final")
                previous, = (await client.get(_ROUTE + "/runs")).json()
                self.assertEqual((await client.post(_ROUTE + "/runs", json=request)).json(), previous)


    async def test_versions_preserve_real_snapshot_history_and_diff_not_live_rows(self):
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_VERSIONS_ENABLED", False),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
        ):
            async with api(True, self.sessions) as client:
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 200)
                async with self.pg.session() as session:
                    self.assertEqual(await self.produce(session, 1, "before capture"), 1)
                legacy_request = payload(0)
                legacy_response = await client.post(_ROUTE + "/runs", json=legacy_request)
                self.assertEqual(legacy_response.status_code, 200, legacy_response.text)
                legacy_id = legacy_response.json()["id"]
                versions = _ROUTE + "/versions"
                self.assertEqual((await client.get(versions)).json(), {"available": False, "versions": []})
                self.assertEqual((await client.get(f"{versions}/{legacy_id}")).status_code, 403)

                with patch.object(settings, "CDC_SANDBOX_VERSIONS_ENABLED", True):
                    self.assertEqual((await client.get(versions)).json(), {"available": True, "versions": []})
                    self.assertEqual((await client.post(_ROUTE + "/runs", json=legacy_request)).json(),
                                     legacy_response.json())
                    self.assertEqual((await client.get(f"{versions}/{legacy_id}")).status_code, 404)

                    async with self.pg.session() as session:
                        self.assertEqual(await self.produce(session, 1, "first captured"), 2)
                        self.assertEqual(await self.produce(session, 9223372036854775807, "to remove"), 3)
                    first_request = payload(1)
                    first = await client.post(_ROUTE + "/runs", json=first_request)
                    self.assertEqual(first.status_code, 200, first.text)
                    old_receipt = first.json()
                    old = (await client.get(f"{versions}/{old_receipt['id']}")).json()
                    self.assertEqual(old["receiptId"], old_receipt["id"])
                    self.assertEqual(old["recordedAt"], old_receipt["recordedAt"])
                    self.assertEqual(old["rows"], [{"id": "1", "value": "first captured"},
                                                   {"id": "9223372036854775807", "value": "to remove"}])

                    # Only the isolated fixture simulates an unrelated removal;
                    # the CDC feature continues to support after-image UPSERT only.
                    await self.pg.execute("DELETE FROM ts_cdc_sandbox.target_rows WHERE id = 9223372036854775807")
                    async with self.pg.session() as session:
                        self.assertEqual(await self.produce(session, 1, "updated"), 4)
                        self.assertEqual(await self.produce(session, -9223372036854775808, "inserted"), 5)
                    second = await client.post(_ROUTE + "/runs", json=payload(3))
                    self.assertEqual(second.status_code, 200, second.text)
                    current = second.json()
                    params = {"fromVersionId": old_receipt["id"], "toVersionId": current["id"]}
                    diff = await client.get(versions + "/compare", params=params)
                    self.assertEqual(diff.status_code, 200, diff.text)
                    self.assertEqual(diff.json()["changes"], [
                        {"id": "-9223372036854775808", "kind": "inserted", "before": None, "after": "inserted"},
                        {"id": "1", "kind": "updated", "before": "first captured", "after": "updated"},
                        {"id": "9223372036854775807", "kind": "deleted", "before": "to remove", "after": None},
                    ])
                    self.assertEqual((await client.post(_ROUTE + "/runs", json=first_request)).json(), old_receipt)
                    self.assertEqual(len((await client.get(versions)).json()["versions"]), 2)

                    await self.pg.execute("TRUNCATE ts_cdc_sandbox.target_rows")
                    self.assertEqual((await client.get(_ROUTE)).json()["state"], "blocked")
                    self.assertEqual((await client.get(f"{versions}/{old_receipt['id']}")).json(), old)
                    self.assertEqual((await client.get(versions + "/compare", params=params)).json(), diff.json())
                    with patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", uuid4()):
                        self.assertEqual((await client.get(versions)).json(), {"available": False, "versions": []})
                        self.assertEqual((await client.get(f"{versions}/{old_receipt['id']}")).status_code, 403)
                        self.assertEqual((await client.get(versions + "/compare", params=params)).status_code, 403)

    async def test_snapshot_over_budget_or_metadata_failure_rolls_back_all_apply_effects(self):
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_VERSIONS_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
        ):
            async with api(True, self.sessions) as client:
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 200)
                async with self.pg.session() as session:
                    await self.produce(session, 101, "would be new")
                    await session.execute(text(
                        "INSERT INTO ts_cdc_sandbox.target_rows SELECT x, 'existing' FROM generate_series(1,100) x",
                    ))
                request = payload(0)
                full = await client.post(_ROUTE + "/runs", json=request)
                self.assertEqual(full.status_code, 422, full.text)

                await self.pg.execute("DELETE FROM ts_cdc_sandbox.target_rows WHERE id > 1")
                await self.pg.execute("UPDATE ts_cdc_sandbox.target_rows SET value = repeat('x',65536) WHERE id = 1")
                too_large = await client.post(_ROUTE + "/runs", json=request)
                self.assertEqual(too_large.status_code, 422, too_large.text)

                async with self.pg.session() as session:
                    await session.execute(text("UPDATE ts_cdc_sandbox.target_rows SET value = 'existing' WHERE id = 1"))
                    await session.execute(text("""
                        CREATE FUNCTION ts_cdc_sandbox.fail_version() RETURNS trigger LANGUAGE plpgsql AS $$
                        BEGIN RAISE EXCEPTION 'fixture refused version'; END;
                        $$
                    """))
                    await session.execute(text("""
                        CREATE TRIGGER fail_version BEFORE INSERT ON ontology_cdc_sandbox_version_rows
                        FOR EACH ROW EXECUTE FUNCTION ts_cdc_sandbox.fail_version()
                    """))
                refused = await client.post(_ROUTE + "/runs", json=request)
                self.assertEqual(refused.status_code, 503, refused.text)
                self.assertEqual((await client.get(_ROUTE)).json()["appliedThrough"], "0")
                self.assertEqual((await client.get(_ROUTE + "/runs")).json(), [])
                self.assertEqual((await client.get(_ROUTE + "/versions")).json()["versions"], [])
                self.assertIsNone(await self.pg.fetch_one("SELECT id FROM ts_cdc_sandbox.target_rows WHERE id=101"))

                await self.pg.execute("DROP TRIGGER fail_version ON ontology_cdc_sandbox_version_rows")
                delivered = await client.post(_ROUTE + "/runs", json=request)
                self.assertEqual(delivered.status_code, 200, delivered.text)
                self.assertEqual((await client.get(_ROUTE + "/versions")).json()["versions"][0]["id"],
                                 delivered.json()["id"])


    async def test_builder_projects_stored_int64_rows_and_optimistically_appends_config_only(self):
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_VERSIONS_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
        ):
            async with api(True, self.sessions) as client:
                self.assertEqual((await client.get(_BUILDER)).json(), {"available": True, "latestGeneration": None})
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 200)
                async with self.pg.session() as session:
                    await self.produce(session, 9223372036854775807, "alpha")
                    await self.produce(session, 9223372036854775806, "alphabet")
                    await self.produce(session, -9223372036854775808, "alpha")
                    await self.produce(session, 0, "beta")
                data = await client.post(_ROUTE + "/runs", json=payload(0))
                self.assertEqual(data.status_code, 200, data.text)
                receipt = data.json()
                config = {
                    "versionId": receipt["id"],
                    "filters": [
                        {"field": "value", "operator": "prefix", "value": "alpha"},
                        {"field": "id", "operator": "gte", "value": "9223372036854775806"},
                    ],
                    "projection": ["value", "id"],
                }
                preview = await client.post(_BUILDER + "/preview", json=config)
                self.assertEqual(preview.status_code, 200, preview.text)
                before = preview.json()
                self.assertEqual(before["inputRows"], 4)
                self.assertEqual(before["matchedRows"], 2)
                self.assertEqual(before["columns"], ["value", "id"])
                self.assertEqual(before["rows"], [
                    ["alphabet", "9223372036854775806"], ["alpha", "9223372036854775807"],
                ])
                reversed_filters = {**config, "filters": list(reversed(config["filters"]))}
                self.assertEqual((await client.post(_BUILDER+"/preview", json=reversed_filters)).json(), before)
                unknown = await client.post(_BUILDER + "/preview", json={**config, "versionId": str(uuid4())})
                self.assertEqual(unknown.status_code, 404)
                self.assertEqual((await client.get(_BUILDER + "/revisions")).json(), [])

                payloads = [{"expectedGeneration": None, "config": candidate} for candidate in (config, reversed_filters)]
                left, right = await asyncio.gather(*[client.post(_BUILDER+"/revisions", json=item) for item in payloads])
                self.assertEqual(sorted([left.status_code, right.status_code]), [201, 409], (left.text, right.text))
                first = (left if left.status_code == 201 else right).json()
                self.assertEqual(first["generation"], 1)
                self.assertEqual(first["configHash"], before["configHash"])
                self.assertEqual((await client.get(_BUILDER + "/revisions/1")).json(), first)

                second_config = {**config, "projection": ["id"]}
                second = await client.post(_BUILDER + "/revisions",
                                           json={"expectedGeneration": 1, "config": second_config})
                self.assertEqual(second.status_code, 201, second.text)
                self.assertEqual([rev["generation"] for rev in (await client.get(_BUILDER + "/revisions")).json()], [2, 1])
                self.assertNotEqual(second.json()["configHash"], before["configHash"])
                self.assertEqual((await client.get(_BUILDER + "/revisions/1")).json(), first)
                self.assertEqual((await client.get(_ROUTE + "/runs")).json(), [receipt])
                self.assertEqual((await client.get(_ROUTE)).json()["appliedThrough"], "4")
                self.assertEqual((await self.pg.fetch_one("SELECT count(*) AS n FROM ts_cdc_sandbox.target_rows"))["n"], 4)

                await self.pg.execute("TRUNCATE ts_cdc_sandbox.target_rows")
                self.assertEqual((await client.get(_ROUTE)).json()["state"], "blocked")
                self.assertEqual((await client.post(_BUILDER+"/preview", json=config)).json(), before)
                with patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", uuid4()):
                    self.assertFalse((await client.get(_BUILDER)).json()["available"])
                    self.assertEqual((await client.get(_BUILDER+"/revisions/1")).status_code, 403)
                    self.assertEqual((await client.post(_BUILDER+"/preview", json=config)).status_code, 403)


    async def test_builder_output_commits_exact_latest_review_and_preserves_sealed_old_and_empty_results(self):
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_VERSIONS_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_OUTPUT_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
        ):
            async with api(True, self.sessions) as client:
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 200)
                async with self.pg.session() as session:
                    await self.produce(session, 9223372036854775807, "last")
                    await self.produce(session, -9223372036854775808, "first")
                captured = await client.post(_ROUTE+"/runs", json=payload(0))
                self.assertEqual(captured.status_code, 200, captured.text)
                config = {"versionId": captured.json()["id"], "filters": [], "projection": ["value", "id"]}
                draft = await client.post(_BUILDER+"/revisions", json={"expectedGeneration": None, "config": config})
                self.assertEqual(draft.status_code, 201, draft.text)
                request = {"requestId": str(uuid4()), "expectedGeneration": 1,
                           "expectedConfigHash": draft.json()["configHash"]}
                bad = await client.post(_OUTPUTS, json={**request, "expectedConfigHash": "a"*64})
                self.assertEqual(bad.status_code, 409)

                left, right = await asyncio.gather(client.post(_OUTPUTS, json=request), client.post(_OUTPUTS, json=request))
                self.assertEqual((left.status_code, right.status_code), (201, 201), (left.text, right.text))
                self.assertEqual(left.json(), right.json())
                receipt = left.json()
                self.assertEqual((receipt["inputRows"], receipt["outputRows"], receipt["versionId"]), (2, 2, config["versionId"]))
                self.assertNotEqual(receipt["id"], receipt["versionId"])
                detail = (await client.get(f"{_OUTPUTS}/{receipt['id']}")).json()
                self.assertEqual(detail["rows"], [["first", "-9223372036854775808"], ["last", "9223372036854775807"]])
                self.assertEqual((await client.get(_OUTPUTS)).json()["outputs"], [receipt])

                # Once a new reviewed generation exists, an original receipt
                # reconciles but a new request carrying stale review cannot run.
                empty_config = {**config, "filters": [{"field": "value", "operator": "eq", "value": "not found"}]}
                draft_two = await client.post(_BUILDER+"/revisions",
                                              json={"expectedGeneration": 1, "config": empty_config})
                self.assertEqual(draft_two.status_code, 201, draft_two.text)
                with patch("app.web.services.pipeline_sandbox_outputs._read_records",
                           side_effect=AssertionError("Replay touched the archived input")):
                    self.assertEqual((await client.post(_OUTPUTS, json=request)).json(), receipt)
                    stale = await client.post(_OUTPUTS, json={**request, "requestId": str(uuid4())})
                    self.assertEqual(stale.status_code, 409)
                changed_request = await client.post(_OUTPUTS, json={**request, "expectedGeneration": 2})
                self.assertEqual(changed_request.status_code, 409)
                empty = await client.post(_OUTPUTS, json={"requestId": str(uuid4()), "expectedGeneration": 2,
                                                         "expectedConfigHash": draft_two.json()["configHash"]})
                self.assertEqual(empty.status_code, 201, empty.text)
                self.assertEqual(empty.json()["outputRows"], 0)
                self.assertEqual((await client.get(f"{_OUTPUTS}/{empty.json()['id']}")).json()["rows"], [])
                self.assertEqual((await client.get(_ROUTE+"/runs")).json(), [captured.json()])
                self.assertEqual((await self.pg.fetch_one("SELECT count(*) AS n FROM ts_cdc_sandbox.target_rows"))["n"], 2)

                await self.pg.execute("TRUNCATE ts_cdc_sandbox.target_rows")
                self.assertEqual((await client.get(f"{_OUTPUTS}/{receipt['id']}")).json(), detail)
                with patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", uuid4()):
                    self.assertFalse((await client.get(_OUTPUTS)).json()["available"])
                    self.assertEqual((await client.get(f"{_OUTPUTS}/{receipt['id']}")).status_code, 403)

    async def test_governed_handoff_qualification_requires_both_grants_and_never_reads_output_rows(self):
        base, restricted = "sandbox-synthetic", "sandbox-restricted"
        sink_generation = uuid4()
        grant = HandoffSinkGrant(scope="synthetic-review-sink", generation=sink_generation, streamMarker=_MARKER,
                                 clearance=base)
        route = _BUILDER + "/handoff"
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_VERSIONS_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_OUTPUT_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
            patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS", {TEST_ACTOR: [base, restricted]}),
            patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_SINK_GENERATION", sink_generation),
            patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_SINK_CLEARANCE", base),
        ):
            async with api(True, self.sessions) as client:
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 200)
                async with self.pg.session() as session:
                    await self.produce(session, 1, "captured")
                captured = await client.post(_ROUTE + "/runs", json=payload(0))
                self.assertEqual(captured.status_code, 200, captured.text)

                async def output(generation, **options):
                    saved = await client.post(_BUILDER + "/revisions", json={"expectedGeneration": generation, "config": {
                        "versionId": captured.json()["id"], "projection": ["id"], "filters": [], **options,
                    }})
                    self.assertEqual(saved.status_code, 201, saved.text)
                    revised = saved.json()
                    result = await client.post(_OUTPUTS, json={
                        "requestId": str(uuid4()), "expectedGeneration": revised["generation"],
                        "expectedConfigHash": revised["configHash"],
                    })
                    self.assertEqual(result.status_code, 201, result.text)
                    return result.json()

                base_output = await output(None)
                restricted_output = await output(1, additionalMarking=restricted, filters=[
                    {"field": "id", "operator": "eq", "value": "2"},
                ])
                self.assertEqual(restricted_output["outputRows"], 0)

                def review(output, **changes):
                    return {"outputId": output["id"], "expectedConfigHash": output["configHash"],
                            "expectedResultHash": output["resultHash"], "expectedSinkGeneration": str(sink_generation),
                            **changes}

                async def qualify(output=base_output, **changes):
                    return await client.post(route + "/eligibility", json=review(output, **changes))

                with (
                    patch("app.web.services.pipeline_sandbox_outputs._stored_rows",
                          side_effect=AssertionError("handoff read stored output rows")) as output_read,
                    patch("app.web.services.pipeline_sandbox_outputs._read_records",
                          side_effect=AssertionError("handoff read CDC rows")) as cdc_read,
                    patch("app.web.services.pipeline_sandbox_outputs.read_match_ids",
                          side_effect=AssertionError("handoff read CDC IDs")) as id_read,
                ):
                    gap = await qualify()
                    self.assertEqual(gap.status_code, 404)
                    self.assertEqual((await client.get(route)).json(), {"available": False})
                    with patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_SINK_GRANTS", {TEST_ACTOR: grant}):
                        ready = await client.get(route)
                        self.assertEqual(ready.json()["sink"]["id"], "synthetic-review-sink")
                        self.assertEqual(ready.headers["cache-control"], "no-store")
                        same = await qualify()
                        self.assertEqual(same.status_code, 200, same.text)
                        self.assertEqual(same.json()["reason"], "eligible_for_review")
                        self.assertTrue(same.json()["eligible"])
                        self.assertEqual(same.json()["markings"], [base])
                        not_clear = await qualify(restricted_output)
                        self.assertEqual(not_clear.status_code, 200, not_clear.text)
                        self.assertFalse(not_clear.json()["eligible"])
                        self.assertEqual(not_clear.json()["reason"], "clearance_mismatch")
                        self.assertEqual(not_clear.json()["markings"], [base, restricted])
                        self.assertEqual((await qualify(expectedConfigHash="f" * 64)).status_code, 409)
                        self.assertEqual((await qualify(expectedResultHash="e" * 64)).status_code, 409)
                        self.assertEqual((await qualify(destination="ts_anywhere")).status_code, 422)

                        with patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS", {TEST_ACTOR: [base]}):
                            hidden = await qualify(restricted_output, expectedConfigHash="c" * 64)
                            missing = await qualify(outputId=str(uuid4()))
                            self.assertEqual((hidden.status_code, hidden.json()), (404, missing.json()))
                        with patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_SINK_CLEARANCE", restricted):
                            self.assertEqual((await qualify()).json(), gap.json())
                        self.assertEqual((await qualify(expectedSinkGeneration=str(uuid4()))).json(), gap.json())
                        with patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_ENABLED", False):
                            self.assertEqual((await qualify()).json(), gap.json())
                        with patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_ENABLED", False):
                            self.assertEqual((await qualify()).json(), gap.json())
                    for reader in (output_read, cdc_read, id_read):
                        reader.assert_not_called()
                await self.assert_private_handoff_copy(client, grant, review(base_output), review(restricted_output), route)

        async with api(False, self.sessions) as reader:
            self.assertEqual((await reader.get(route)).status_code, 403)

    async def assert_private_handoff_copy(self, client, grant, payload, empty_payload, route):
        from app.web.services.pipeline_handoff_copy import _physical

        marker = uuid4()
        migration = import_module("app.web.alembic.versions.6094e3ea0b7d_ontology_private_handoff_copy")

        def migrate(sync, operation):
            with Operations.context(MigrationContext.configure(sync.connection())):
                operation()

        self.addAsyncCleanup(self.pg.execute, "DROP SCHEMA IF EXISTS ontology_handoff_private CASCADE")
        async with self.pg.session() as session:
            for operation in (migration.upgrade, migration.downgrade, migration.upgrade):
                await session.run_sync(migrate, operation)
            await session.execute(text("SET LOCAL search_path=pg_catalog,public,pg_temp"))
            await session.execute(text("INSERT INTO ontology_handoff_private.sink_state VALUES(true,:marker)"), {"marker": marker})
            physical = await _physical(session)
            self.assertIsNotNone(physical)
        approval = HandoffCopyGrant(**grant.model_dump(), databaseMarker=marker, physicalDigest=physical)
        body = {**payload, "requestId": str(uuid4())}
        with patch.multiple(settings, ONTOLOGY_SANDBOX_HANDOFF_SINK_GRANTS={TEST_ACTOR: grant},
                            ONTOLOGY_SANDBOX_HANDOFF_COPY_ENABLED=True,
                            ONTOLOGY_SANDBOX_HANDOFF_COPY_DATABASE_MARKER=marker,
                            ONTOLOGY_SANDBOX_HANDOFF_COPY_PHYSICAL_DIGEST=physical):
            self.assertEqual((await client.post(route + "/copies", json=body)).status_code, 404)
            with patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_COPY_GRANTS", {TEST_ACTOR: approval}):
                cap = await client.get(route)
                self.assertTrue(cap.json().get("copyAvailable"), cap.text)
                execute = AsyncSession.execute

                async def fail_rows(session, query, params=None, **kwargs):
                    if "INSERT INTO ontology_handoff_private.copy_rows" in str(query):
                        params = [{**row, "position": 101} for row in params]
                    return await execute(session, query, params, **kwargs)

                with patch.object(AsyncSession, "execute", fail_rows):
                    result = await client.post(route + "/copies", json=body)
                    self.assertEqual(result.status_code, 503, result.text)
                self.assertEqual((await self.pg.fetch_one("SELECT count(*) AS n FROM ontology_handoff_private.copies"))["n"], 0)
                original = await client.post(route + "/copies", json=body)
                self.assertEqual(original.status_code, 201, original.text)
                receipt = original.json()
                self.assertEqual((receipt["rowsCopied"], receipt["markings"]), (1, ["sandbox-synthetic"]))
                self.assertNotIn("rows", receipt)
                with patch("app.web.services.pipeline_handoff_copy._stored_rows", side_effect=AssertionError(
                    "reconciliation touched source output rows",
                )) as source:
                    replay = await client.post(route + "/copies", json=body)
                    self.assertEqual(replay.json(), receipt)
                    duplicate = await client.post(route + "/copies", json={**body, "requestId": str(uuid4())})
                    self.assertEqual(duplicate.status_code, 409, duplicate.text)
                    await self.pg.execute("UPDATE ontology_handoff_private.copy_rows SET row_values=ARRAY['2']")
                    tampered = await client.post(route + "/copies", json=body)
                    self.assertEqual(tampered.status_code, 409, tampered.text)
                    await self.pg.execute("DELETE FROM ontology_handoff_private.copy_rows")
                    self.assertEqual((await client.post(route + "/copies", json=body)).status_code, 409)
                    source.assert_not_called()

                # Even after replica contents were removed in this isolated fixture,
                # the recipient can verify the historical receipt, not replica integrity.
                await self.assert_recipient_handoff_proof(route, receipt, approval)
                await self.assert_sender_handoff_proof(client, route, body, receipt, approval)

                restarted = grant.model_copy(update={"generation": uuid4(), "clearance": "sandbox-restricted"})
                approved = HandoffCopyGrant(**restarted.model_dump(), databaseMarker=marker, physicalDigest=physical)
                zero = {**empty_payload, "requestId": str(uuid4()), "expectedSinkGeneration": str(restarted.generation)}
                original_commit = AsyncSession.commit
                lost = False

                async def lose_ack(session):
                    nonlocal lost
                    await original_commit(session)
                    if not lost:
                        lost = True
                        raise TimeoutError("simulated post-commit loss")

                with patch.multiple(settings, ONTOLOGY_SANDBOX_HANDOFF_SINK_GRANTS={TEST_ACTOR: restarted},
                                    ONTOLOGY_SANDBOX_HANDOFF_SINK_CLEARANCE=restarted.clearance,
                                    ONTOLOGY_SANDBOX_HANDOFF_SINK_GENERATION=restarted.generation,
                                    ONTOLOGY_SANDBOX_HANDOFF_COPY_GRANTS={TEST_ACTOR: approved}):
                    self.assertEqual((await client.post(route + "/copies", json=body)).status_code, 404)
                    with patch.object(AsyncSession, "commit", lose_ack):
                        uncertain = await client.post(route + "/copies", json=zero)
                        self.assertEqual(uncertain.status_code, 503, uncertain.text)
                    with patch("app.web.services.pipeline_handoff_copy._stored_rows", side_effect=AssertionError(
                        "lost-ack recovery reread sealed source",
                    )) as source:
                        settled = await client.post(route + "/copies", json=zero)
                        self.assertEqual(settled.status_code, 201, settled.text)
                        self.assertEqual((settled.json()["rowsCopied"], settled.json()["markings"]),
                                         (0, ["sandbox-synthetic", "sandbox-restricted"]))
                        source.assert_not_called()
                    await self.assert_recipient_handoff_proof(route, settled.json(), approved)
        with self.assertRaisesRegex(RuntimeError, "Cannot remove provisioned synthetic handoff"):
            async with self.pg.session() as session:
                await session.run_sync(migrate, migration.downgrade)

    async def assert_sender_handoff_proof(self, client, route, original_body, original_receipt, approval):
        endpoint = route + "/sender-proof"
        execute = AsyncSession.execute
        lookups = []

        async def no_payload(session, query, params=None, **kwargs):
            sql = " ".join(str(query).lower().split())
            self.assertNotIn("from only ontology_handoff_private.copy_rows", sql)
            self.assertNotIn("ontology_pipeline_sandbox_output_rows", sql)
            self.assertNotIn("ontology_cdc_sandbox_version_rows", sql)
            self.assertFalse(sql.startswith(("insert ", "update ", "delete ")))
            if "from only ontology_handoff_private.copies" in sql:
                self.assertIn("and actor_id=:actor", sql)
                lookups.append(sql)
            return await execute(session, query, params, **kwargs)

        with (patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_COPY_ENABLED", False),
              patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_PROOF_GRANTS", {}),
              patch.object(AsyncSession, "execute", no_payload)):
            off = await client.post(endpoint, json=original_body)
            self.assertEqual(off.status_code, 404, off.text)
            self.assertEqual(off.headers["cache-control"], "private, no-store")
            self.assertEqual(lookups, [])
            with patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_SENDER_PROOF_ENABLED", True):
                cap = await client.get(route)
                self.assertTrue(cap.json().get("senderProofAvailable"), cap.text)
                self.assertNotIn("copyAvailable", cap.json())
                self.assertEqual(lookups, [])
                found = await client.post(endpoint, json=original_body)
                self.assertEqual(found.status_code, 200, found.text)
                self.assertEqual(found.json(), original_receipt)
                self.assertEqual(found.headers["cache-control"], "private, no-store")
                self.assertEqual(len(lookups), 1)
                for edits in (
                    {"expectedConfigHash": "a" * 64}, {"expectedResultHash": "e" * 64},
                    {"expectedSinkGeneration": str(uuid4())}, {"outputId": str(uuid4())},
                    {"extra": "ignore"}, {"requestId": original_body["requestId"].upper()},
                ):
                    denied = await client.post(endpoint, json={**original_body, **edits})
                    self.assertEqual((denied.status_code, denied.json()), (404, off.json()))
                    self.assertEqual(denied.headers["cache-control"], "private, no-store")
                self.assertEqual(len(lookups), 1)
                self.assertEqual((await client.post(endpoint + "?scope=any", json=original_body)).json(), off.json())
                duplicate = json.dumps(original_body)[:-1] + ',"requestId":' + json.dumps(original_body["requestId"]) + '}'
                self.assertEqual((await client.post(endpoint, content=duplicate,
                                                   headers={"Content-Type": "application/json"})).json(), off.json())
                missing = await client.post(endpoint, json={**original_body, "requestId": str(uuid4())})
                self.assertEqual((missing.status_code, missing.json()), (404, off.json()))
                self.assertEqual(len(lookups), 2)
                with patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_COPY_GRANTS", {}):
                    self.assertNotIn("senderProofAvailable", (await client.get(route)).json())
                    self.assertEqual((await client.post(endpoint, json=original_body)).json(), off.json())
                with patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_COPY_PHYSICAL_DIGEST", "4" * 64):
                    self.assertEqual((await client.post(endpoint, json=original_body)).json(), off.json())
                self.assertEqual(len(lookups), 2)
                async with api(False, self.sessions) as reader:
                    self.assertEqual((await reader.post(endpoint, json=original_body)).status_code, 403)
                async with self.pg.session() as holder:
                    # A busy private heap must not become a false statement that nothing was recorded.
                    await holder.execute(text("LOCK TABLE ontology_handoff_private.copies IN ACCESS EXCLUSIVE MODE"))
                    busy = await client.post(endpoint, json=original_body)
                    self.assertEqual((busy.status_code, busy.json()), (404, off.json()))

    async def assert_recipient_handoff_proof(self, route, historical, enrollment):
        recipient = uuid4()
        endpoint = route + "/recipient-proof"
        expected = {"receiptId": historical["id"], "expectedSinkGeneration": str(enrollment.generation)}
        attrs = {
            "ONTOLOGY_SANDBOX_HANDOFF_COPY_ENABLED": False,
            "ONTOLOGY_SANDBOX_HANDOFF_COPY_GRANTS": {},
            "ONTOLOGY_SANDBOX_HANDOFF_SINK_GRANTS": {},
            "ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS": {},
            "ONTOLOGY_SANDBOX_PIPELINE_ENABLED": False,
            "ONTOLOGY_SANDBOX_PIPELINE_OUTPUT_ENABLED": False,
            "CDC_SANDBOX_ENABLED": False,
            "CDC_SANDBOX_VERSIONS_ENABLED": False,
            "CDC_SANDBOX_STREAM_MARKER": None,
            "ONTOLOGY_SANDBOX_HANDOFF_PROOF_ENABLED": True,
            "ONTOLOGY_SANDBOX_HANDOFF_PROOF_STREAM_MARKER": enrollment.streamMarker,
            "ONTOLOGY_SANDBOX_HANDOFF_PROOF_GRANTS": {recipient: enrollment},
        }
        receipt_selects = []
        execute = AsyncSession.execute

        async def no_payload(session, query, params=None, **kwargs):
            sql = " ".join(str(query).lower().split())
            if sql.startswith("select ") and "from only ontology_handoff_private.copies" in sql:
                receipt_selects.append(sql)
            self.assertNotIn("from only ontology_handoff_private.copy_rows", sql)
            self.assertNotIn("ontology_cdc_sandbox_version_rows", sql)
            self.assertNotIn("ontology_pipeline_sandbox_output_rows", sql)
            return await execute(session, query, params, **kwargs)

        with (patch.multiple(settings, **attrs),
              patch("app.web.tests.test_transfer_plans.TEST_ACTOR", recipient),
              patch.object(AsyncSession, "execute", no_payload)):
            async with api(True, self.sessions) as client:
                self.assertEqual((await client.get(route)).json(), {"available": False})
                capability = await client.get(endpoint)
                self.assertEqual(capability.status_code, 200, capability.text)
                self.assertEqual(capability.json(), {"available": True, "sink": historical["sink"]})
                self.assertEqual(capability.headers["cache-control"], "private, no-store")
                self.assertEqual(receipt_selects, [], "capability enumerated receipt IDs")
                for wrong in ({**expected, "requestId": historical["requestId"]}, {"receiptId": historical["id"]}):
                    self.assertEqual((await client.post(endpoint, json=wrong)).status_code, 422)
                self.assertEqual((await client.get(endpoint + "?history=true")).status_code, 422)
                self.assertEqual((await client.post(endpoint + "?id=anything", json=expected)).status_code, 422)
                found = await client.post(endpoint, json=expected)
                self.assertEqual(found.status_code, 200, found.text)
                self.assertEqual(found.headers["cache-control"], "private, no-store")
                result = found.json()
                self.assertEqual(result, {**{key: historical[key] for key in ("id", "sink", "markings", "recordedAt")},
                                          "rowsRecorded": historical["rowsCopied"]})
                self.assertEqual(len(receipt_selects), 1)
                self.assertIn("and marking_level <= :clearance", receipt_selects[0])
                self.assertTrue(all(name not in receipt_selects[0] for name in ("actor_id", "output_id", "request_id", "result_hash")))
                missing = await client.post(endpoint, json={**expected, "receiptId": str(uuid4())})
                self.assertEqual(missing.status_code, 404)
                count = len(receipt_selects)
                self.assertEqual((await client.post(endpoint, json={**expected, "expectedSinkGeneration": str(uuid4())})).json(),
                                 missing.json())
                with patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_PROOF_GRANTS", {}):
                    self.assertEqual((await client.get(endpoint)).json(), {"available": False})
                    self.assertEqual((await client.post(endpoint, json=expected)).json(), missing.json())
                self.assertEqual(len(receipt_selects), count)
                lower = enrollment.model_copy(update={
                    "clearance": "sandbox-synthetic" if enrollment.clearance == "sandbox-restricted" else "sandbox-restricted",
                })
                with patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_PROOF_GRANTS", {recipient: lower}):
                    self.assertEqual((await client.post(endpoint, json=expected)).json(), missing.json())
                    self.assertEqual((await client.get(endpoint)).json().get("available"),
                                     enrollment.clearance == "sandbox-restricted")
                alternate = enrollment.model_copy(update={"streamMarker": uuid4()})
                with patch.multiple(settings, ONTOLOGY_SANDBOX_HANDOFF_PROOF_STREAM_MARKER=alternate.streamMarker,
                                    ONTOLOGY_SANDBOX_HANDOFF_PROOF_GRANTS={recipient: alternate}):
                    self.assertEqual((await client.post(endpoint, json=expected)).json(), missing.json())
                with patch.object(settings, "ONTOLOGY_SANDBOX_HANDOFF_PROOF_ENABLED", False):
                    self.assertEqual((await client.post(endpoint, json=expected)).json(), missing.json())
            async with api(False, self.sessions) as reader:
                self.assertEqual((await reader.get(endpoint)).status_code, 403)
                self.assertEqual((await reader.post(endpoint, json=expected)).status_code, 403)

    async def test_compare_sealed_outputs_checks_ancestry_before_rows_and_counts_bounded_changes(self):
        base, restricted = "sandbox-synthetic", "sandbox-restricted"
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_VERSIONS_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_OUTPUT_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
            patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS", {TEST_ACTOR: [base, restricted]}),
        ):
            async with api(True, self.sessions) as client:
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 200)
                async with self.pg.session() as session:
                    for key in (-9223372036854775808, -2, 0, *range(1, 28)):
                        await self.produce(session, key, "before")

                async def capture(previous):
                    result = await client.post(_ROUTE + "/runs", json=payload(previous))
                    self.assertEqual(result.status_code, 200, result.text)
                    return result.json()["id"]

                async def output(version, generation, *, projection, filters=(), marking=None):
                    config = dict(versionId=version, projection=projection, filters=list(filters))
                    if marking:
                        config["additionalMarking"] = marking
                    saved = await client.post(_BUILDER + "/revisions", json={
                        "expectedGeneration": generation, "config": config,
                    })
                    self.assertEqual(saved.status_code, 201, saved.text)
                    result = await client.post(_OUTPUTS, json={
                        "requestId": str(uuid4()), "expectedGeneration": saved.json()["generation"],
                        "expectedConfigHash": saved.json()["configHash"],
                    })
                    self.assertEqual(result.status_code, 201, result.text)
                    return result.json()["id"]

                first = await capture(0)
                left = await output(first, None, projection=["value", "id"])
                async with self.pg.session() as session:
                    for key in (*range(1, 28), 9223372036854775807):
                        await self.produce(session, key, "after")
                second = await capture(30)
                right = await output(second, 1, projection=["value", "id"],
                                     filters=[{"field": "id", "operator": "gte", "value": "0"}], marking=restricted)
                value_only = await output(second, 2, projection=["value"])
                path = _OUTPUTS + "/compare"
                args = {"fromOutputId": left, "toOutputId": right}

                with patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS", {TEST_ACTOR: [base]}), patch(
                    "app.web.services.pipeline_sandbox_outputs._stored_rows",
                    side_effect=AssertionError("read rows before both operands were authorized"),
                ) as read:
                    denied = await client.post(path, json=args)
                    absent = await client.post(path, json={**args, "toOutputId": str(uuid4())})
                    self.assertEqual((denied.status_code, denied.json()), (404, absent.json()))
                    read.assert_not_called()

                self.assertEqual((await client.post(path, json={**args, "toOutputId": left})).status_code, 422)
                self.assertEqual((await client.post(path, json={**args, "ignoreOrder": True})).status_code, 422)
                with patch("app.web.services.pipeline_sandbox_outputs._stored_rows", side_effect=AssertionError(
                    "incompatible output shouldn't load rows",
                )) as read:
                    rejected = await client.post(path, json={**args, "toOutputId": value_only})
                    self.assertEqual(rejected.status_code, 409, rejected.text)
                    read.assert_not_called()

                await self.pg.execute("TRUNCATE ts_cdc_sandbox.target_rows")
                # The exact old physical capture remains historical, never reevaluated on the replacement.
                with patch("app.web.services.pipeline_sandbox_outputs._read_records",
                           side_effect=AssertionError("compared live or captured CDC rows")) as read:
                    comparison = await client.post(path, json=args)
                    self.assertEqual(comparison.status_code, 200, comparison.text)
                    self.assertEqual(comparison.headers["Cache-Control"], "no-store")
                    read.assert_not_called()
                result = comparison.json()
                self.assertEqual([result[key] for key in ("added", "removed", "updated", "unchanged")], [1, 2, 27, 1])
                self.assertEqual(result["markings"], [base, restricted])
                self.assertTrue(result["truncated"])
                self.assertEqual(len(result["changes"]), 25)
                self.assertEqual(result["changes"][0], {"id": "-9223372036854775808", "kind": "removed",
                                                       "before": ["before", "-9223372036854775808"], "after": None})
                self.assertEqual(result["changes"][2], {"id": "1", "kind": "updated",
                                                       "before": ["before", "1"], "after": ["after", "1"]})
                await self.pg.execute("""
                    UPDATE ontology_pipeline_sandbox_output_rows SET row_values=ARRAY['tampered',row_values[2]]
                    WHERE output_id=:id AND position=1
                """, {"id": UUID(left)})
                self.assertEqual((await client.post(path, json=args)).status_code, 409)

    async def test_record_history_reproves_all_selected_outputs_before_loading_sealed_rows(self):
        base, restricted = "sandbox-synthetic", "sandbox-restricted"
        record = "-9223372036854775808"
        path = _OUTPUTS + "/record-history"
        config_shape = {"projection": ["value", "id"],
                        "filters": [{"field": "value", "operator": "prefix", "value": "keep"}]}
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_VERSIONS_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_OUTPUT_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
            patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS", {TEST_ACTOR: [base, restricted]}),
        ):
            async with api(True, self.sessions) as client:
                response = await client.put(_ROUTE, json=_REVIEW)
                self.assertEqual(response.status_code, 200, response.text)
                version = None
                generation = None

                async def seal(shape, *, additional=None):
                    nonlocal generation
                    config = {**shape, "versionId": version, **({"additionalMarking": additional} if additional else {})}
                    saved = await client.post(_BUILDER + "/revisions", json={"expectedGeneration": generation, "config": config})
                    self.assertEqual(saved.status_code, 201, saved.text)
                    generation = saved.json()["generation"]
                    committed = await client.post(_OUTPUTS, json={
                        "requestId": str(uuid4()), "expectedGeneration": generation,
                        "expectedConfigHash": saved.json()["configHash"],
                    })
                    self.assertEqual(committed.status_code, 201, committed.text)
                    return committed.json()

                saved = []
                for index, (identity, value) in enumerate((
                    (int(record), "discard initially"), (int(record), "keep first"),
                    (10, "keep unrelated"), (int(record), "keep updated"), (int(record), "discard now"),
                )):
                    async with self.pg.session() as session:
                        await self.produce(session, identity, value)
                    response = await client.post(_ROUTE + "/runs", json=payload(index))
                    self.assertEqual(response.status_code, 200, response.text)
                    version = response.json()["id"]
                    saved.append(await seal(config_shape, additional=restricted if index == 2 else None))
                different = await seal({**config_shape, "filters": []})
                reversed_ids = [item["id"] for item in reversed(saved)]
                request = {"outputIds": reversed_ids, "recordId": record}

                with patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS", {TEST_ACTOR: [base]}), patch(
                    "app.web.services.pipeline_sandbox_outputs._stored_rows",
                    side_effect=AssertionError("read storage before every selected operand was authorized"),
                ) as read:
                    denied = await client.post(path, json=request)
                    absent = await client.post(path, json={**request, "outputIds": [saved[0]["id"], str(uuid4())]})
                    self.assertEqual((denied.status_code, denied.json()), (404, absent.json()))
                    read.assert_not_called()
                for invalid in (
                    {**request, "recordId": int(record)}, {**request, "recordId": "-0"},
                    {**request, "recordId": "9223372036854775808"}, {**request, "outputIds": [saved[0]["id"]] * 2},
                    {**request, "outputIds": [*reversed_ids, different["id"]]}, {**request, "includeLatest": True},
                ):
                    self.assertEqual((await client.post(path, json=invalid)).status_code, 422)
                self.assertEqual((await client.post(path + "?latest=true", json=request)).status_code, 422)
                async with api(False, self.sessions) as reader:
                    self.assertEqual((await reader.post(path, json=request)).status_code, 403)
                with patch("app.web.services.pipeline_sandbox_outputs._stored_rows",
                           side_effect=AssertionError("recipe validation should precede row reads")) as read:
                    rejected = await client.post(path, json={**request, "outputIds": [saved[0]["id"], different["id"]]})
                    self.assertEqual(rejected.status_code, 409)
                    read.assert_not_called()

                with patch("app.web.services.pipeline_sandbox_outputs._read_records",
                           side_effect=AssertionError("history touched stored CDC rows")) as read:
                    response = await client.post(path, json=request)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.headers["cache-control"], "private, no-store")
                    read.assert_not_called()
                    result = response.json()
                self.assertEqual((result["recordId"], result["projection"], result["markings"]),
                                 (record, ["value", "id"], [base, restricted]))
                self.assertEqual([item["outputId"] for item in result["steps"]], [item["id"] for item in saved])
                self.assertEqual([item["kind"] for item in result["steps"]], ["absent", "added", "unchanged", "changed", "removed"])
                self.assertEqual([item["row"] for item in result["steps"]], [
                    None, ["keep first", record], ["keep first", record], ["keep updated", record], None,
                ])
                baseline = await client.post(path, json={**request, "outputIds": reversed_ids[2:]})
                self.assertEqual([item["kind"] for item in baseline.json()["steps"]], ["absent", "added", "unchanged"])
                baseline = await client.post(path, json={**request, "outputIds": reversed_ids[:3]})
                self.assertEqual([item["kind"] for item in baseline.json()["steps"]], ["baseline", "changed", "removed"])
                with patch("app.web.services.pipeline_sandbox_outputs._RECORD_HISTORY_RESPONSE_BYTES", 100):
                    self.assertEqual((await client.post(path, json=request)).status_code, 409)

                await self.pg.execute("""
                    UPDATE ontology_pipeline_sandbox_outputs SET recorded_at=(
                        SELECT recorded_at FROM ontology_pipeline_sandbox_outputs WHERE id=:first
                    ) WHERE id=:second
                """, {"first": UUID(saved[0]["id"]), "second": UUID(saved[1]["id"])})
                tied = await client.post(path, json=request)
                self.assertEqual(tied.status_code, 409, tied.text)
                self.assertIn("ambiguous", tied.json()["detail"])
                await self.pg.execute("""
                    UPDATE ontology_pipeline_sandbox_output_rows SET row_values=ARRAY['tampered',row_values[2]]
                    WHERE output_id=:id AND position=1
                """, {"id": UUID(saved[3]["id"])})
                bad_seal = await client.post(path, json={**request, "outputIds": reversed_ids[:2]})
                self.assertEqual(bad_seal.status_code, 409, bad_seal.text)
                self.assertNotIn("steps", bad_seal.json())

    async def test_builder_output_row_failure_rolls_back_receipt_and_real_lost_ack_replays_once(self):
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_VERSIONS_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_OUTPUT_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
        ):
            async with api(True, self.sessions) as client:
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 200)
                async with self.pg.session() as session:
                    await self.produce(session, 1, "captured")
                    await session.execute(text("""
                        CREATE FUNCTION ts_cdc_sandbox.fail_output() RETURNS trigger LANGUAGE plpgsql AS $$
                        BEGIN RAISE EXCEPTION 'fixture refused output'; END;
                        $$
                    """))
                    await session.execute(text("""
                        CREATE TRIGGER fail_output BEFORE INSERT ON ontology_pipeline_sandbox_output_rows
                        FOR EACH ROW EXECUTE FUNCTION ts_cdc_sandbox.fail_output()
                    """))
                source = await client.post(_ROUTE+"/runs", json=payload(0))
                self.assertEqual(source.status_code, 200, source.text)
                config = {"versionId": source.json()["id"], "filters": [], "projection": ["id", "value"]}
                draft = await client.post(_BUILDER+"/revisions", json={"expectedGeneration": None, "config": config})
                self.assertEqual(draft.status_code, 201, draft.text)
                request = {"requestId": str(uuid4()), "expectedGeneration": 1,
                           "expectedConfigHash": draft.json()["configHash"]}
                failed = await client.post(_OUTPUTS, json=request)
                self.assertEqual(failed.status_code, 503, failed.text)
                self.assertEqual((await client.get(_OUTPUTS)).json()["outputs"], [])
                self.assertEqual((await self.pg.fetch_one(
                    "SELECT count(*) AS n FROM ontology_pipeline_sandbox_output_rows",
                ))["n"], 0)
                await self.pg.execute("DROP TRIGGER fail_output ON ontology_pipeline_sandbox_output_rows")

                commit = AsyncSession.commit
                lost = False

                async def lose_ack(session):
                    nonlocal lost
                    await commit(session)
                    if not lost:
                        lost = True
                        raise RuntimeError("fixture dropped successful output commit acknowledgement")

                with patch.object(AsyncSession, "commit", lose_ack):
                    unknown = await client.post(_OUTPUTS, json=request)
                self.assertEqual(unknown.status_code, 503, unknown.text)
                prior, = (await client.get(_OUTPUTS)).json()["outputs"]
                self.assertEqual((await client.post(_OUTPUTS, json=request)).json(), prior)
                self.assertEqual((await client.get(f"{_OUTPUTS}/{prior['id']}")).json()["rows"], [["1", "captured"]])
                self.assertEqual((await client.get(_ROUTE+"/runs")).json(), [source.json()])

    async def test_governed_recipe_restore_appends_exact_provenance_without_rows(self):
        base = "sandbox-synthetic"
        restricted = "sandbox-restricted"
        url = _BUILDER + "/revisions/restore"
        async with self.pg.session() as session:
            await self.produce(session, 1, "one")
        execute = AsyncSession.execute

        async def metadata_only(session, query, params=None, **kwargs):
            sql = " ".join(str(query).lower().split())
            self.assertNotIn("ontology_cdc_sandbox_version_rows", sql)
            self.assertNotIn("ontology_pipeline_sandbox_output_rows", sql)
            self.assertNotIn("from only ts_cdc_sandbox.event_log", sql)
            self.assertNotIn("from only ts_cdc_sandbox.target_rows", sql)
            return await execute(session, query, params, **kwargs)

        def body(donor, head):
            return {"sourceGeneration": donor["generation"], "expectedSourceConfigHash": donor["configHash"],
                    "expectedHeadGeneration": head["generation"], "expectedHeadConfigHash": head["configHash"]}

        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
            patch.object(settings, "CDC_SANDBOX_VERSIONS_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS", {TEST_ACTOR: (base, restricted)}),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_MATCH_ENABLED", True),
        ):
            async with api(True, self.sessions) as client, api(False, self.sessions) as reader:
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 200)
                started = await client.post(_ROUTE + "/runs", json=payload(0))
                self.assertEqual(started.status_code, 200, started.text)
                first = started.json()["id"]
                async with self.pg.session() as session:
                    await self.produce(session, 2, "two")
                next_run = await client.post(_ROUTE + "/runs", json=payload(1))
                self.assertEqual(next_run.status_code, 200, next_run.text)
                second = next_run.json()["id"]
                simple = {"versionId": first, "filters": [], "projection": ["id"]}
                match = {**simple, "matchVersionId": second, "additionalMarking": restricted}
                newest = {**simple, "projection": ["value"], "additionalMarking": restricted}

                async def save(config, expected):
                    response = await client.post(_BUILDER + "/revisions", json={"expectedGeneration": expected, "config": config})
                    self.assertEqual(response.status_code, 201, response.text)
                    return response.json()

                low = await save(simple, None)
                donor = await save(match, 1)
                head = await save(newest, 2)
                self.assertNotIn("restoreEnabled", (await client.get(_BUILDER)).json())
                self.assertEqual((await client.post(url, json=body(donor, head))).status_code, 403)
                with (
                    patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_RESTORE_ENABLED", True),
                    patch.object(AsyncSession, "execute", metadata_only),
                ):
                    self.assertTrue((await client.get(_BUILDER)).json()["restoreEnabled"])
                    self.assertEqual((await reader.post(url, json=body(donor, head))).status_code, 403)
                    self.assertEqual((await client.post(url, json=body(low, head))).status_code, 409)
                    self.assertEqual((await client.post(url, json={**body(donor, head), "expectedHeadConfigHash": "a"*64})).status_code, 409)
                    self.assertEqual((await client.post(url, json={**body(donor, head), "sourceGeneration": True})).status_code, 422)
                    self.assertEqual((await client.post(url + "?x=1", json=body(donor, head))).status_code, 422)
                    ambiguous = json.dumps(body(donor, head)).replace("{", '{"sourceGeneration":1,', 1)
                    duplicate = await client.post(url, content=ambiguous, headers={"Content-Type": "application/json"})
                    self.assertEqual((duplicate.status_code, duplicate.headers["cache-control"]), (422, "private, no-store"))
                    with patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS", {TEST_ACTOR: (base,)}):
                        self.assertNotIn("restoreEnabled", (await client.get(_BUILDER)).json())
                        self.assertEqual((await client.post(url, json=body(donor, head))).status_code, 403)
                    await self.pg.execute("UPDATE ontology_cdc_sandbox_versions SET recorded_at=recorded_at+interval '1 second' "
                                          "WHERE receipt_id=:id", {"id": UUID(second)})
                    self.assertEqual((await client.post(url, json=body(donor, head))).status_code, 409)
                    await self.pg.execute("UPDATE ontology_cdc_sandbox_versions SET recorded_at=recorded_at-interval '1 second' "
                                          "WHERE receipt_id=:id", {"id": UUID(second)})
                    outcomes = await asyncio.gather(*(client.post(url, json=body(donor, head)) for _ in range(2)))
                    self.assertEqual(sorted(response.status_code for response in outcomes), [201, 409],
                                     [response.text for response in outcomes])
                    result = next(response for response in outcomes if response.status_code == 201)
                    self.assertEqual(result.headers["cache-control"], "private, no-store")
                    restored = result.json()
                    self.assertEqual((restored["generation"], restored["restoredFromGeneration"]), (4, 2))
                    for key in ("configHash", "config", "markings"):
                        self.assertEqual(restored[key], donor[key])
                    self.assertEqual((await client.get(_BUILDER + "/revisions/2")).json(), donor)
                self.assertNotIn("restoreEnabled", (await client.get(_BUILDER)).json())
                self.assertEqual((await client.get(_BUILDER + "/revisions/4")).json(), restored)
                self.assertEqual((await client.get(_BUILDER + "/revisions")).json()[0], restored)
                await self.pg.execute("TRUNCATE ts_cdc_sandbox.target_rows")
                with patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_RESTORE_ENABLED", True):
                    self.assertEqual((await client.post(url, json=body(head, restored))).status_code, 409)
                self.assertEqual((await client.get(_BUILDER + "/revisions/4")).json(), restored)

        migration = import_module("app.web.alembic.versions.f27ad8ce609b_ontology_pipeline_restore_provenance")

        def downgrade(sync):
            with Operations.context(MigrationContext.configure(sync.connection())):
                migration.downgrade()

        with self.assertRaisesRegex(RuntimeError, "Cannot discard durable Builder restore provenance"):
            async with self.pg.session() as session:
                await session.run_sync(downgrade)

    async def test_match_by_id_preview_review_and_sealed_replay_respect_both_inputs(self):
        base, restricted = "sandbox-synthetic", "sandbox-restricted"
        minimum, maximum = -(2**63), 2**63 - 1
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_VERSIONS_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_MATCH_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_OUTPUT_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
            patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS", {TEST_ACTOR: [base, restricted]}),
        ):
            async with api(True, self.sessions) as client:
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 200)

                async def capture(cursor, pairs):
                    async with self.pg.session() as session:
                        for identity, value in pairs:
                            await self.produce(session, identity, value)
                    applied = await client.post(_ROUTE + "/runs", json=payload(cursor))
                    self.assertEqual(applied.status_code, 200, applied.text)
                    return applied.json()["id"]

                right = await capture(0, [(minimum, "right-min"), (maximum, "right-max")])
                left = await capture(2, [(minimum, "left-min"), (maximum, "left-max"), (0, "only-left")])
                config = {"versionId": left, "matchVersionId": right, "filters": [], "projection": ["value", "id"]}
                self.assertTrue((await client.get(_BUILDER)).json()["matchEnabled"])
                preview = await client.post(_BUILDER + "/preview", json=config)
                self.assertEqual(preview.status_code, 200, preview.text)
                expected = [["left-min", str(minimum)], ["left-max", str(maximum)]]
                self.assertEqual(preview.json()["rows"], expected)
                self.assertEqual((preview.json()["inputRows"], preview.json()["rightInputRows"]), (3, 2))
                self.assertEqual(preview.json()["markings"], [base])
                self.assertEqual((await client.post(_BUILDER + "/preview",
                                                    json={**config, "matchVersionId": left})).status_code, 422)

                with (
                    patch("app.web.services.pipeline_sandbox._read_records",
                          side_effect=AssertionError("read before both operands were authorized")) as left_read,
                    patch("app.web.services.pipeline_sandbox.read_match_ids",
                          side_effect=AssertionError("read before both operands were authorized")) as right_read,
                ):
                    for invalid_config in ({**config, "matchVersionId": str(uuid4())},
                                           {**config, "versionId": str(uuid4())}):
                        denied = await client.post(_BUILDER + "/preview", json=invalid_config)
                        self.assertEqual(denied.status_code, 404, denied.text)
                    left_read.assert_not_called()
                    right_read.assert_not_called()

                saved = await client.post(_BUILDER + "/revisions", json={"expectedGeneration": None, "config": config})
                self.assertEqual(saved.status_code, 201, saved.text)
                self.assertEqual(saved.json()["configHash"], preview.json()["configHash"])
                request = {"requestId": str(uuid4()), "expectedGeneration": 1,
                           "expectedConfigHash": saved.json()["configHash"]}
                receipt = await client.post(_OUTPUTS, json=request)
                self.assertEqual(receipt.status_code, 201, receipt.text)
                receipt = receipt.json()
                self.assertEqual(receipt["matchVersionId"], right)
                self.assertEqual((await client.get(f"{_OUTPUTS}/{receipt['id']}")).json()["rows"], expected)

                empty_config = {**config, "additionalMarking": restricted,
                                "filters": [{"field": "id", "operator": "eq", "value": "0"}]}
                saved = await client.post(_BUILDER + "/revisions", json={"expectedGeneration": 1, "config": empty_config})
                self.assertEqual(saved.status_code, 201, saved.text)
                empty_request = {"requestId": str(uuid4()), "expectedGeneration": 2,
                                 "expectedConfigHash": saved.json()["configHash"]}
                empty = await client.post(_OUTPUTS, json=empty_request)
                self.assertEqual(empty.status_code, 201, empty.text)
                empty = empty.json()
                self.assertEqual(empty["outputRows"], 0)
                self.assertEqual(empty["markings"], [base, restricted])

                with patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS", {TEST_ACTOR: [base]}):
                    self.assertEqual((await client.get(_OUTPUTS)).json()["outputs"], [receipt])
                    self.assertEqual((await client.get(f"{_OUTPUTS}/{empty['id']}")).status_code, 404)

                with (
                    patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_MATCH_ENABLED", False),
                    patch("app.web.services.pipeline_sandbox_outputs._read_records",
                          side_effect=AssertionError("historical replay must not read input rows")) as output_read,
                ):
                    self.assertEqual((await client.post(_BUILDER + "/preview", json=config)).status_code, 403)
                    self.assertEqual((await client.post(_OUTPUTS, json={**empty_request,
                                                                       "requestId": str(uuid4())})).status_code, 403)
                    self.assertEqual((await client.post(_OUTPUTS, json=request)).json(), receipt)
                    self.assertEqual((await client.get(_BUILDER + "/revisions/1")).json()["config"], config)
                    output_read.assert_not_called()

                with patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_ENABLED", False):
                    self.assertNotIn("matchEnabled", (await client.get(_BUILDER)).json())
                    actual = await client.post(_OUTPUTS, json=request)
                    random = await client.post(_OUTPUTS, json={**request, "requestId": str(uuid4())})
                    self.assertEqual((actual.status_code, actual.json()), (403, random.json()))
                    self.assertEqual(random.status_code, 403)

                await self.pg.execute("UPDATE ontology_cdc_sandbox_versions SET marking_level=NULL "
                                      "WHERE receipt_id=CAST(:right AS uuid)", {"right": right})
                with patch("app.web.services.pipeline_sandbox._read_records",
                           side_effect=AssertionError("read a row with an unmarked operand")) as read:
                    bad = await client.post(_BUILDER + "/preview", json=config)
                    self.assertEqual(bad.status_code, 404, bad.text)
                    read.assert_not_called()
                self.assertEqual((await client.get(_OUTPUTS)).json()["outputs"], [])
                await self.pg.execute("UPDATE ontology_cdc_sandbox_versions SET marking_level=1 "
                                      "WHERE receipt_id=CAST(:right AS uuid)", {"right": right})

                # A formerly valid right input rebinding to another ancestry cannot leak even a prior receipt.
                await self.pg.execute("UPDATE ontology_cdc_sandbox_versions SET physical_digest=repeat('f',64) "
                                      "WHERE receipt_id=CAST(:right AS uuid)", {"right": right})
                self.assertEqual((await client.get(_OUTPUTS)).json()["outputs"], [])
                self.assertEqual((await client.get(f"{_OUTPUTS}/{receipt['id']}")).status_code, 404)
                self.assertEqual((await client.get(_BUILDER + "/revisions/1")).status_code, 404)

    async def test_governance_propagates_and_denies_before_access_then_stays_protected_on_disable(self):
        base, restricted = "sandbox-synthetic", "sandbox-restricted"
        versions = _ROUTE + "/versions"
        review_path = _BUILDER + "/revisions"
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_VERSIONS_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SANDBOX_PIPELINE_OUTPUT_ENABLED", True),
            patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS", {TEST_ACTOR: [base, restricted]}),
        ):
            async with api(True, self.sessions) as client:
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 200)
                async with self.pg.session() as session:
                    await self.produce(session, 1, "legacy")
                legacy_request = payload(0)
                legacy = (await client.post(_ROUTE + "/runs", json=legacy_request)).json()
                self.assertNotIn("markings", legacy)
                legacy_config = {"versionId": legacy["id"], "filters": [], "projection": ["id", "value"]}
                saved = await client.post(review_path, json={"expectedGeneration": None, "config": legacy_config})
                self.assertEqual(saved.status_code, 201, saved.text)

                with patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_ENABLED", True):
                    async with self.pg.session() as session:
                        await self.produce(session, 1, "governed")
                    request = payload(1)
                    applied = await client.post(_ROUTE + "/runs", json=request)
                    self.assertEqual(applied.status_code, 200, applied.text)
                    version = applied.json()
                    self.assertEqual(version["markings"], [base])
                    self.assertEqual((await client.get(_ROUTE + "/runs")).json(), [version])
                    self.assertEqual((await client.post(_ROUTE + "/runs", json=legacy_request)).status_code, 404)
                    self.assertEqual((await client.get(f"{versions}/{legacy['id']}")).status_code, 404)
                    self.assertEqual((await client.get(f"{versions}/{version['id']}")).json()["markings"], [base])
                    with patch("app.web.services.cdc_sandbox_versions._read_records",
                               side_effect=AssertionError("read before both operands were authorized")) as read:
                        rejected = await client.get(versions + "/compare", params={
                            "fromVersionId": version["id"], "toVersionId": legacy["id"],
                        })
                        self.assertEqual(rejected.status_code, 404, rejected.text)
                        read.assert_not_called()
                    config = {**legacy_config, "versionId": version["id"], "additionalMarking": restricted}
                    preview = await client.post(_BUILDER + "/preview", json=config)
                    self.assertEqual(preview.status_code, 200, preview.text)
                    self.assertEqual(preview.json()["markings"], [base, restricted])
                    review = await client.post(review_path, json={"expectedGeneration": None, "config": config})
                    self.assertEqual(review.status_code, 201, review.text)
                    saved = review.json()
                    self.assertEqual(saved["generation"], 2)
                    self.assertEqual(saved["configHash"], preview.json()["configHash"])
                    self.assertEqual(saved["markings"], [base, restricted])
                    commit = {"requestId": str(uuid4()), "expectedGeneration": 2,
                              "expectedConfigHash": saved["configHash"]}
                    committed = await client.post(_OUTPUTS, json=commit)
                    self.assertEqual(committed.status_code, 201, committed.text)
                    out = committed.json()
                    self.assertEqual(out["markings"], [base, restricted])
                    self.assertEqual((await client.post(_OUTPUTS, json=commit)).json(), out)
                    self.assertEqual((await client.get(f"{_OUTPUTS}/{out['id']}")).json()["rows"], [["1", "governed"]])

                    with patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS", {TEST_ACTOR: [base]}):
                        head = (await client.get(_BUILDER)).json()
                        self.assertFalse(head["canSave"])
                        self.assertIsNone(head["latestGeneration"])
                        self.assertEqual((await client.get(review_path)).json(), [])
                        self.assertEqual((await client.get(review_path + "/2")).status_code, 404)
                        self.assertEqual((await client.get(_OUTPUTS)).json()["outputs"], [])
                        with patch("app.web.services.pipeline_sandbox_outputs._stored_rows",
                                   side_effect=AssertionError("read an unauthorized output")) as read:
                            self.assertEqual((await client.get(f"{_OUTPUTS}/{out['id']}")).status_code, 404)
                            denied = await client.post(_OUTPUTS, json=commit)
                            missing = await client.post(_OUTPUTS, json={**commit, "requestId": str(uuid4())})
                            self.assertEqual((denied.status_code, denied.json()), (missing.status_code, missing.json()))
                            read.assert_not_called()
                        self.assertEqual((await client.post(_BUILDER + "/preview", json=config)).status_code, 403)
                        base_config = {key: value for key, value in config.items() if key != "additionalMarking"}
                        self.assertEqual((await client.post(_BUILDER + "/preview", json=base_config)).json()["markings"],
                                         [base])
                        self.assertEqual((await client.post(review_path, json={
                            "expectedGeneration": None, "config": base_config,
                        })).status_code, 403)

                    with patch.object(settings, "ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS", {}):
                        status = (await client.get(_ROUTE)).json()
                        self.assertFalse(status["available"])
                        self.assertIsNone(status["generation"])
                        self.assertIsNone(status["sourceHighWater"])
                        self.assertEqual((await client.get(_ROUTE + "/runs")).status_code, 403)
                        self.assertEqual((await client.post(_ROUTE + "/runs", json=request)).status_code, 403)

                    # Tamper only the isolated fixture: a downgraded output header cannot match its immutable review.
                    await self.pg.execute("UPDATE ontology_pipeline_sandbox_outputs SET marking_level=1")
                    self.assertEqual((await client.get(f"{_OUTPUTS}/{out['id']}")).status_code, 404)
                    self.assertEqual((await client.get(_OUTPUTS)).json()["outputs"], [])

                # Turning off policy cannot create a new unmarked copy of previously classified target data.
                async with self.pg.session() as session:
                    await self.produce(session, 1, "unapplied")
                status = (await client.get(_ROUTE)).json()
                self.assertFalse(status["available"])
                self.assertIsNone(status["sourceHighWater"])
                self.assertEqual((await client.get(_ROUTE + "/runs")).json(), [])
                self.assertEqual((await client.post(_ROUTE + "/runs", json=request)).status_code, 403)
                self.assertEqual((await client.post(_ROUTE + "/runs", json=payload(2))).status_code, 403)
                self.assertEqual((await client.get(versions)).json(), {"available": False, "versions": []})
                self.assertFalse((await client.get(_OUTPUTS)).json()["available"])
                self.assertEqual((await self.pg.fetch_one(
                    "SELECT value FROM ts_cdc_sandbox.target_rows WHERE id=1",
                ))["value"], "governed")

    async def test_cdc_mismatched_receipt_scope_is_never_treated_as_versionless(self):
        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "CDC_SANDBOX_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_VERSIONS_ENABLED", True),
            patch.object(settings, "CDC_SANDBOX_STREAM_MARKER", _MARKER),
        ):
            async with api(True, self.sessions) as client:
                self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 200)
                async with self.pg.session() as session:
                    await self.produce(session, 1, "archive")
                request = payload(0)
                self.assertEqual((await client.post(_ROUTE + "/runs", json=request)).status_code, 200)
                await self.pg.execute(
                    "UPDATE ontology_cdc_sandbox_versions SET marking_level=1, physical_digest=repeat('a',64)",
                )
                self.assertEqual((await client.get(_ROUTE + "/runs")).json(), [])
                self.assertEqual((await client.post(_ROUTE + "/runs", json=request)).status_code, 404)

    async def test_no_generic_cdc_schema_or_raw_sql_fixture_bypass(self):
        layers = EtlLayers()
        layers._pg = self.pg
        with patch("app.web.api.routes.etl.get_etl_layers", return_value=layers):
            async with api(True, self.sessions) as client:
                for schema in ("ts_cdc_sandbox", "cdc_sandbox"):
                    root = f"/etl/{schema}"
                    for endpoint in (root + "/tables", root + "/target_rows/data", root + "/target_rows/preview",
                                     root + "/target_rows/columns"):
                        self.assertEqual((await client.get(endpoint)).status_code, 404, endpoint)
                    self.assertEqual((await client.post(root + "/target_rows/preview-rows", json={})).status_code, 404)
                response = await client.post("/etl/query", json={"sql": "SELECT * FROM ts_cdc_sandbox.target_rows"})
                self.assertEqual(response.status_code, 403, response.text)
                response = await client.get("/etl/layers")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn("ts_cdc_sandbox", [layer["schemaName"] for layer in response.json()])
