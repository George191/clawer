"""Synthetic CDC fixture playback; an explicit isolated PostgreSQL URL enables writes."""

import asyncio
from importlib import import_module
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.postgres_client import PostgresClient
from app.web.api.models.cdc_sandbox import CdcSandboxApply, CdcSandboxReview
from app.web.core.config import settings
from app.web.tests.test_transfer_plans import api

_URL = os.getenv("ONTOLOGY_CDC_SANDBOX_TEST_URL")
_DB = "ontology_cdc_regression"
_ROUTE = "/etl/cdc-sandbox"
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
            self.assertEqual((await client.put(_ROUTE, json=_REVIEW)).status_code, 403)
            self.assertEqual((await client.post(_ROUTE + "/runs", json=payload(0))).status_code, 403)


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
        migration = import_module("app.web.alembic.versions.984b2ef31a6c_ontology_cdc_sandbox")

        def upgrade(sync):
            with Operations.context(MigrationContext.configure(sync.connection())):
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
