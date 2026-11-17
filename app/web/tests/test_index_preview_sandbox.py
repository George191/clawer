"""Contract and guarded real PostgreSQL verification of the synthetic index gate."""
import asyncio
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI

from pydantic import ValidationError
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.web.api.dependencies.common import get_current_user
from app.web.api.dependencies.session import get_db
from app.web.api.models.index_preview_sandbox import IndexSandboxQuery
from app.web.api.routes.etl import router
from app.web.core.config import settings
from app.web.services.index_preview_sandbox import _approved_plan
from app.web.tests.test_transfer_plans import api

_URL = os.getenv("ONTOLOGY_INDEX_SANDBOX_TEST_URL")
_DB = "ontology_index_regression"
_ROUTE = "/etl/index-preview-sandbox"
_GENERIC = "/etl/ts_index_preview_sandbox/records"
_QUERY = {"mode": "preview", "idFrom": "70", "idTo": "80", "limit": 4}
_SELECT = "SELECT r.id, r.label"


class IndexPreviewContract(unittest.IsolatedAsyncioTestCase):
    def test_rejects_unbounded_filters_and_untyped_or_untrusted_input(self):
        for invalid in (
            {"idFrom": None, "idTo": None}, {"idEquals": "8"}, {"idFrom": "100"}, {"idFrom": "-0"},
            {"idFrom": "-9223372036854775809"}, {"idFrom": "9223372036854775808"}, {"idFrom": 70},
            {"idTo": "80 OR TRUE"}, {"mode": "analyze"}, {"limit": 0}, {"limit": 101}, {"limit": "4"},
            {"sql": "SELECT 1"}, {"schema": "public"}, {"order": "DESC"},
            {"mode": "explain", "cursor": "malformed"}, {"idFrom": None, "idTo": None, "idEquals": "70", "cursor": "x"},
            {"cursor": "a" * 513},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                IndexSandboxQuery.model_validate({**_QUERY, **invalid})

    def test_plan_accepts_only_direct_exact_forward_primary_without_residual_work(self):
        leaf = {
            "Node Type": "Index Only Scan", "Parent Relationship": "Outer", "Parallel Aware": False,
            "Schema": "ts_index_preview_sandbox", "Relation Name": "records", "Alias": "r",
            "Index Name": "records_pkey", "Scan Direction": "Forward", "Index Cond": "valid",
        }
        root = {"Node Type": "Limit", "Parallel Aware": False}
        self.assertEqual(_approved_plan([{"Plan": {**root, "Plans": [leaf]}}], "records_pkey").scan, "index_only")
        for change in (
            {"Node Type": "Bitmap Heap Scan"}, {"Node Type": "Sort"}, {"Index Name": "other"},
            {"Filter": "(label = 'example')"}, {"Schema": "public"}, {"Scan Direction": "Backward"},
            {"Plans": []}, {"Parallel Aware": True}, {"Index Cond": None},
        ):
            with self.subTest(change=change):
                self.assertIsNone(_approved_plan([{"Plan": {**root, "Plans": [{**leaf, **change}]}}], "records_pkey"))
        self.assertIsNone(_approved_plan([{"Plan": {**root, "Plans": [leaf, leaf]}}], "records_pkey"))

    async def test_admin_and_explicit_environment_gate(self):
        with patch.object(settings, "ONTOLOGY_INDEX_PREVIEW_SANDBOX_ENABLED", False):
            async with api(True) as client:
                self.assertEqual((await client.get(_ROUTE)).json(), {"available": False})
                self.assertEqual((await client.post(_ROUTE, json=_QUERY)).status_code, 404)
        with patch.object(settings, "ONTOLOGY_INDEX_PREVIEW_SANDBOX_ENABLED", True):
            with patch.object(settings, "ENVIRONMENT", "production"):
                async with api(True) as client:
                    self.assertEqual((await client.get(_ROUTE)).json(), {"available": False})
                    self.assertEqual((await client.post(_ROUTE, json=_QUERY)).status_code, 404)
            async with api(False) as client:
                self.assertEqual((await client.get(_ROUTE)).status_code, 403)
                self.assertEqual((await client.post(_ROUTE, json=_QUERY)).status_code, 403)

    async def test_no_generic_row_read_for_exact_synthetic_fixture(self):
        async with api(False) as client:
            self.assertEqual((await client.get(_GENERIC + "/data")).status_code, 403)
            self.assertEqual((await client.post(_GENERIC + "/data", json={})).status_code, 403)
            self.assertEqual((await client.post(_GENERIC + "/preview-rows", json={})).status_code, 403)


@unittest.skipUnless(_URL, "requires isolated synthetic PostgreSQL fixture")
class IndexPreviewPostgres(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        url = make_url(_URL)
        if url.host not in ("localhost", "127.0.0.1", "::1") or url.database != _DB or url.query:
            self.fail("Only the dedicated local index fixture can be provisioned")
        self.engine = create_async_engine(url)
        self.addAsyncCleanup(self.engine.dispose)
        async with self.engine.begin() as conn:
            await conn.execute(text("CREATE SCHEMA IF NOT EXISTS ts_index_preview_sandbox"))
            await conn.execute(text("DROP TABLE IF EXISTS ts_index_preview_sandbox.records"))
            await conn.execute(text(
                "CREATE TABLE ts_index_preview_sandbox.records (id bigint PRIMARY KEY, label varchar(128) NOT NULL) "
                "WITH (autovacuum_enabled = false)",
            ))
            await conn.execute(text(
                "INSERT INTO ts_index_preview_sandbox.records SELECT n, 'record ' || n FROM generate_series(1, 20000) n",
            ))
            await conn.execute(text("ANALYZE ts_index_preview_sandbox.records"))
        self.statements = []
        event.listen(self.engine.sync_engine, "before_cursor_execute", self._capture)
        self.factory = async_sessionmaker(self.engine)
        env = patch.object(settings, "ENVIRONMENT", "local")
        enabled = patch.object(settings, "ONTOLOGY_INDEX_PREVIEW_SANDBOX_ENABLED", True)
        env.start()
        enabled.start()
        self.addCleanup(env.stop)
        self.addCleanup(enabled.stop)

    def _capture(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append((statement, parameters))

    async def _session(self):
        async with self.factory() as session:
            yield session

    async def _post(self, json):
        async with api(True, self._session) as client:
            response = await client.post(_ROUTE, json=json)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _business_selects(self):
        return [(sql, args) for sql, args in self.statements if sql.startswith(_SELECT)]

    def _explain(self):
        return [(sql, args) for sql, args in self.statements if sql.startswith("EXPLAIN ")]

    async def test_actual_explain_only_and_matching_bounded_statement_execution(self):
        explain = await self._post({**_QUERY, "mode": "explain"})
        self.assertEqual(explain["status"], "allowed", explain)
        self.assertEqual(explain["plan"], {"scan": "index", "key": "id", "order": "ascending"})
        self.assertIsNone(explain["rows"])
        self.assertIsNone(explain["hasMore"])
        self.assertEqual(self._business_selects(), [])
        self.statements.clear()
        result = await self._post(_QUERY)
        self.assertEqual(result["status"], "allowed", result)
        self.assertEqual(result["rows"], [{"id": str(n), "label": f"record {n}"} for n in range(70, 74)])
        self.assertTrue(result["hasMore"])
        [(explain_sql, explain_params)] = self._explain()
        [(query_sql, query_params)] = self._business_selects()
        self.assertEqual(explain_sql, "EXPLAIN (FORMAT JSON, VERBOSE TRUE, ANALYZE FALSE) " + query_sql)
        self.assertEqual(explain_params, query_params)
        self.assertIn("READ ONLY", self.statements[0][0])
        self.assertEqual(query_params, (70, 80, 5))

    async def test_missing_primary_index_and_wrong_fixture_shape_never_read_rows(self):
        async with self.engine.begin() as conn:
            await conn.execute(text("ALTER TABLE ts_index_preview_sandbox.records DROP CONSTRAINT records_pkey"))
            await conn.execute(text("CREATE INDEX fake ON ts_index_preview_sandbox.records (id)"))
        output = await self._post(_QUERY)
        self.assertEqual(output["reason"], "primary_index_required", output)
        async with self.engine.begin() as conn:
            await conn.execute(text("ALTER TABLE ts_index_preview_sandbox.records ALTER COLUMN label TYPE text"))
        output = await self._post(_QUERY)
        self.assertEqual(output["reason"], "table_unavailable", output)
        self.assertIsNone(output["plan"])
        self.assertEqual(self._explain(), [])
        self.assertEqual(self._business_selects(), [])

    async def test_naturally_chosen_seqscan_is_blocked_without_fallback(self):
        async with self.engine.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE ts_index_preview_sandbox.records"))
            await conn.execute(text("INSERT INTO ts_index_preview_sandbox.records VALUES (70, 'one')"))
            await conn.execute(text("ANALYZE ts_index_preview_sandbox.records"))
        output = await self._post(_QUERY)
        self.assertEqual(output["reason"], "unsupported_plan", output)
        self.assertIsNone(output["rows"])
        self.assertEqual(len(self._explain()), 1)
        self.assertEqual(self._business_selects(), [])

    async def test_lock_contention_has_no_explain_or_business_read(self):
        async with self.engine.begin() as conn:
            await conn.execute(text("LOCK ts_index_preview_sandbox.records IN SHARE UPDATE EXCLUSIVE MODE"))
            output = await self._post(_QUERY)
        self.assertEqual(output["reason"], "query_unavailable", output)
        self.assertEqual(self._explain(), [])
        self.assertEqual(self._business_selects(), [])

    async def test_covering_primary_can_use_a_real_index_only_plan(self):
        async with self.engine.begin() as conn:
            await conn.execute(text("ALTER TABLE ts_index_preview_sandbox.records DROP CONSTRAINT records_pkey"))
            await conn.execute(text(
                "ALTER TABLE ts_index_preview_sandbox.records ADD PRIMARY KEY (id) INCLUDE (label)",
            ))
        result = await self._post(_QUERY)
        self.assertEqual(result["plan"], {"scan": "index_only", "key": "id", "order": "ascending"})
        self.assertEqual(result["rows"][0], {"id": "70", "label": "record 70"})

    async def test_empty_index_hit_and_bigint_bound_roundtrip(self):
        big_id = "-9223372036854775808"
        async with self.engine.begin() as conn:
            await conn.execute(text("INSERT INTO ts_index_preview_sandbox.records(id,label) VALUES (:id,'big')"),
                               {"id": int(big_id)})
        output = await self._post({"mode": "preview", "idEquals": big_id, "limit": 1})
        self.assertEqual(output["rows"], [{"id": big_id, "label": "big"}], output)
        self.assertFalse(output["hasMore"])
        output = await self._post({"mode": "preview", "idEquals": "9223372036854775807", "limit": 1})
        self.assertEqual(output["rows"], [], output)
        self.assertFalse(output["hasMore"])


    async def test_keyset_continuation_same_bound_statement_and_real_page_cap(self):
        with patch.object(settings, "SECRET_KEY", "isolated-index-cursor-secret-for-tests"):
            async with api(True, self._session) as client:
                self.assertEqual((await client.get(_ROUTE)).json(), {"available": True, "rangePagination": True})
            first = await self._post(_QUERY)
            self.assertEqual((first["page"], first["paginationStatus"]), (1, "more"))
            self.statements.clear()
            second = await self._post({**_QUERY, "cursor": first["nextCursor"]})
            self.assertEqual([row["id"] for row in second["rows"]], [str(n) for n in range(74, 78)])
            [(explain_sql, explain_args)] = self._explain()
            [(query, args)] = self._business_selects()
            self.assertEqual(explain_sql, "EXPLAIN (FORMAT JSON, VERBOSE TRUE, ANALYZE FALSE) " + query)
            self.assertEqual(explain_args, args)
            self.assertEqual(args, (70, 80, 73, 5))
            self.assertIn("r.id > CAST", query)
            self.assertNotIn("OFFSET", query)
            last = await self._post({**_QUERY, "cursor": second["nextCursor"]})
            self.assertEqual((last["page"], last["paginationStatus"], last["hasMore"]), (3, "end", False))
            self.assertNotIn("nextCursor", last)

            request = {"mode": "preview", "idFrom": "500", "idTo": "2000", "limit": 100}
            ids = []
            for page in range(1, 11):
                output = await self._post(request)
                self.assertEqual(output["page"], page)
                ids.extend(int(row["id"]) for row in output["rows"])
                if page < 10:
                    self.assertEqual(output["paginationStatus"], "more")
                    request["cursor"] = output["nextCursor"]
            self.assertEqual(ids, list(range(500, 1500)))
            self.assertTrue(output["hasMore"])
            self.assertEqual(output["paginationStatus"], "capped")
            self.assertNotIn("nextCursor", output)

    async def test_cursor_denies_cross_actor_tampering_filters_expiry_and_physical_rewrite_before_reads(self):
        with (
            patch.object(settings, "SECRET_KEY", "isolated-index-cursor-secret-for-tests"),
            patch("app.web.services.index_preview_cursor.epoch_seconds", return_value=1000) as clock,
        ):
            first = await self._post(_QUERY)
            cursor = first["nextCursor"]

            async def deny(payload):
                self.statements.clear()
                async with api(True, self._session) as client:
                    denied = await client.post(_ROUTE, json=payload)
                self.assertEqual(denied.status_code, 409, denied.text)
                self.assertEqual(denied.json()["detail"]["code"], "cursor_invalid")
                self.assertEqual((self._explain(), self._business_selects()), ([], []))

            await deny({**_QUERY, "cursor": ("A" if cursor[0] != "A" else "B") + cursor[1:]})
            await deny({**_QUERY, "cursor": cursor, "limit": 3})
            await deny({**_QUERY, "cursor": cursor, "idFrom": "71"})
            with patch("app.web.tests.test_transfer_plans.TEST_ACTOR", uuid4()):
                await deny({**_QUERY, "cursor": cursor})
            self.assertEqual(self.statements, [])
            clock.return_value = 1100
            second = await self._post({**_QUERY, "cursor": cursor})
            clock.return_value = 1300
            await deny({**_QUERY, "cursor": second["nextCursor"]})
            self.assertEqual(self.statements, [])
            clock.return_value = 1100
            async with self.engine.begin() as conn:
                await conn.execute(text("REINDEX INDEX ts_index_preview_sandbox.records_pkey"))
            await deny({**_QUERY, "cursor": cursor})
            # Starting a separate chain after REINDEX may race ephemeral DB
            # activity under the intentionally short production time budget.
            for _ in range(4):
                new_first = await self._post(_QUERY)
                if new_first["reason"] != "query_unavailable":
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(new_first.get("paginationStatus"), "more", new_first)
            updated = new_first["nextCursor"]
            async with self.engine.begin() as conn:
                await conn.execute(text("TRUNCATE ts_index_preview_sandbox.records"))
            await deny({**_QUERY, "cursor": updated})

    async def test_range_continuation_keeps_signed_bigint_string_and_requires_stable_key(self):
        bound = "-9223372036854775808"
        async with self.engine.begin() as conn:
            await conn.execute(text("INSERT INTO ts_index_preview_sandbox.records VALUES (:id,'lowest'),(-1,'negative')"),
                               {"id": int(bound)})
        request = {"mode": "preview", "idFrom": bound, "idTo": "0", "limit": 1}
        with patch.object(settings, "SECRET_KEY", "isolated-index-cursor-secret-for-tests"):
            first = await self._post(request)
            self.assertEqual(first["rows"][0]["id"], bound)
            second = await self._post({**request, "cursor": first["nextCursor"]})
            self.assertEqual(second["rows"][0]["id"], "-1")
            self.assertEqual(second["paginationStatus"], "end")
        with patch.object(settings, "SECRET_KEY", "too-short"):
            output = await self._post(request)
            self.assertTrue(output["hasMore"])
            self.assertNotIn("nextCursor", output)
            self.assertNotIn("page", output)

    async def test_disconnect_cancels_real_pg_work_and_releases_relation_lock(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4(), is_superuser=True)
        app.dependency_overrides[get_db] = self._session
        disconnect, sent = asyncio.Event(), []
        request_sent = False

        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": json.dumps(_QUERY).encode("ascii"), "more_body": False}
            await disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http", "http_version": "1.1", "method": "POST", "scheme": "http",
            "path": _ROUTE, "raw_path": _ROUTE.encode("ascii"), "query_string": b"",
            "root_path": "", "headers": [(b"content-type", b"application/json")],
            "server": ("fixture", 80), "client": ("127.0.0.1", 1200),
        }
        execute = AsyncSession.execute
        sentinel = "SELECT pg_catalog.pg_sleep(8) /* exact-index-disconnect */"

        async def slow_read(session, statement, *args, **kwargs):
            if session.bind is self.engine and str(statement).startswith(_SELECT):
                # This fixture extends the SQL statement budget only to distinguish
                # client disconnect from the ordinary 400ms database fallback.
                await execute(session, text("SET LOCAL statement_timeout = '10s'"))
                await execute(session, text(sentinel))
            return await execute(session, statement, *args, **kwargs)

        async def wait_until_driver_is_running():
            async with self.engine.connect() as other:
                while not await other.scalar(text("""
                    SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                    WHERE pid <> pg_backend_pid() AND state='active' AND query=:sentinel)
                """), {"sentinel": sentinel}):
                    await asyncio.sleep(0.01)
                    await other.rollback()

        with patch.object(AsyncSession, "execute", slow_read):
            running = asyncio.create_task(app(scope, receive, send))
            try:
                await asyncio.wait_for(wait_until_driver_is_running(), 1.5)
            finally:
                disconnect.set()
            await asyncio.wait_for(running, 0.7)
        self.assertEqual(sent[0]["status"], 499, sent)
        self.assertEqual(self._business_selects(), [])
        async with self.engine.begin() as other:
            await other.execute(text("LOCK ts_index_preview_sandbox.records IN ACCESS EXCLUSIVE MODE NOWAIT"))

    async def test_aggregate_deadline_blocks_when_no_disconnect_arrives(self):
        execute = AsyncSession.execute
        cancelled = asyncio.Event()

        async def slow_checkout(session, statement, *args, **kwargs):
            if str(statement).startswith("SET TRANSACTION ISOLATION LEVEL"):
                try:
                    await asyncio.sleep(8)
                finally:
                    cancelled.set()
            return await execute(session, statement, *args, **kwargs)

        with patch.object(AsyncSession, "execute", slow_checkout):
            output = await asyncio.wait_for(self._post(_QUERY), 3)
        self.assertTrue(cancelled.is_set())
        self.assertEqual((output["status"], output["reason"]), ("blocked", "query_unavailable"))
        self.assertEqual((self._explain(), self._business_selects()), ([], []))
