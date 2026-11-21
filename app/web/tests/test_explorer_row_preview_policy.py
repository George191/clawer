"""Force indexed browsing before legacy row queries on isolated PostgreSQL."""

import asyncio
import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.schema import ontology
from app.etl.schema.object_links import OBJECT_LINKS_DDL_STATEMENTS
from app.etl.schema.object_relations import OBJECT_RELATIONS_DDL_STATEMENTS
from app.storage.postgres_client import PostgresClient
from app.web.core.config import settings
from app.web.core.config.indexed_sample import RequiredIndexedPreviewTable
from app.web.services.etl_layers import EtlLayers
from app.web.services.etl_workspace import EtlWorkspace
from app.web.tests.test_transfer_plans import api

_URL = os.getenv("ONTOLOGY_TEST_DATABASE_URL")
_SCHEMA = "ts_required_index_probe"


class PreviewPolicyShapeTests(unittest.TestCase):
    def test_canonical_exact_table_scope(self):
        RequiredIndexedPreviewTable(schema="ts_ods", table="evidence")
        for invalid in ({"schema": "ods", "table": "evidence"}, {"schema": "ts_ods", "table": "Evidence"},
                        {"schema": "ts_ods", "table": "evidence", "database": "other"}):
            with self.assertRaises(ValidationError):
                RequiredIndexedPreviewTable.model_validate(invalid)


@unittest.skipUnless(_URL, "requires disposable loopback PostgreSQL")
class PreviewPolicyPostgreSQLTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_editors_and_stale_delete_at_the_http_boundary(self):
        url = make_url(_URL)
        self.assertEqual(url.database, "ontology_creation_regression")
        self.assertIn(url.host, {"127.0.0.1", "::1"})
        self.assertFalse(url.query)
        pg = PostgresClient(_URL)
        await pg.connect(max_retries=1)
        self.addAsyncCleanup(pg.close)
        schema = "ts_explorer_version_test"
        async with pg.session() as session:
            for statement in (*ontology.ONTOLOGY_DDL_STATEMENTS, *OBJECT_RELATIONS_DDL_STATEMENTS, *OBJECT_LINKS_DDL_STATEMENTS):
                await session.execute(text(statement))
            await session.execute(text(f"CREATE SCHEMA {schema}"))
            await session.execute(text(f"CREATE TABLE {schema}.assets (id bigint PRIMARY KEY, encode text)"))
            await session.execute(text(f"INSERT INTO {schema}.assets VALUES (1, 'original'), (2, NULL)"))
            await session.execute(text(f"INSERT INTO {schema}.assets VALUES (9007199254740993, 'exact')"))
        self.addAsyncCleanup(pg.execute, f"DROP SCHEMA {schema} CASCADE")
        layers = object.__new__(EtlLayers)
        layers._pg = pg
        workspace = object.__new__(EtlWorkspace)
        workspace._pg, workspace._layers = pg, layers
        endpoint = f"/etl/{schema}/assets"
        with (patch("app.web.api.routes.etl.get_etl_layers", return_value=layers),
              patch("app.web.api.routes.etl.get_etl_workspace", return_value=workspace)):
            async with api(False) as reader:
                self.assertEqual((await reader.post(endpoint+"/preview-rows", json={"forEditing": True})).status_code, 403)
                self.assertNotIn("rowVersions", (await reader.post(endpoint+"/preview-rows", json={})).json())
                exact = (await reader.post(endpoint+"/preview-rows", json={"withIdentity": True, "offset": 2})).json()
                self.assertEqual(exact["rowIdentities"], [{"id": "9007199254740993"}])
                self.assertEqual(exact["rows"][0]["id"], "9007199254740993")
                self.assertNotIn("rowVersions", exact)
            async with api(True) as admin:
                preview = (await admin.post(endpoint+"/preview-rows", json={"limit": 1, "forEditing": True})).json()
                self.assertEqual(preview["rows"], [{"id": 1, "encode": "original"}])
                self.assertEqual(len(preview["rowVersions"]), 1)
                self.assertTrue(preview["hasMore"])
                self.assertEqual(preview["rowActions"], {"edit": True, "delete": True})
                key = {"key": {"id": 1}}
                stale = {**key, "expectedVersion": preview["rowVersions"][0]}
                missing = await admin.patch(endpoint+"/rows", json={**key, "values": {"encode":"missing"}})
                self.assertEqual(missing.status_code, 428)
                replies = await asyncio.gather(*(admin.patch(endpoint+"/rows", json={**stale, "values": {"encode": v}})
                                                 for v in ("first", "second")))
                self.assertEqual(sorted(r.status_code for r in replies), [200, 409])
                conflict = await admin.request("DELETE", endpoint+"/rows", json=stale)
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(conflict.json()["detail"]["code"], "row_version_conflict")
                current = (await admin.post(endpoint+"/preview-rows", json={"limit": 1, "forEditing": True})).json()
                self.assertIn(current["rows"][0]["encode"], ("first", "second"))
                deleted = await admin.request("DELETE", endpoint+"/rows", json={**key, "expectedVersion": current["rowVersions"][0]})
                self.assertEqual(deleted.status_code, 200)

    async def test_real_large_asset_is_blocked_before_sql_and_partition_family_never_reads_rows(self):
        url = make_url(_URL)
        if url.database != "ontology_creation_regression" or url.host not in {"127.0.0.1", "::1"} or url.query:
            self.fail("Only the exact disposable Explorer database may be used")
        pg = PostgresClient(_URL)
        await pg.connect(max_retries=1)
        self.addAsyncCleanup(pg.close)
        async with pg.session() as session:
            for statement in ontology.ONTOLOGY_DDL_STATEMENTS:
                await session.execute(text(statement))
            for sql in (
                f"CREATE SCHEMA {_SCHEMA}",
                f"CREATE TABLE {_SCHEMA}.required (id bigint PRIMARY KEY)",
                f"INSERT INTO {_SCHEMA}.required SELECT id FROM generate_series(1, 110000) id",
                f"CREATE TABLE {_SCHEMA}.unrelated (id bigint PRIMARY KEY)",
                f"INSERT INTO {_SCHEMA}.unrelated VALUES (1)",
                f"CREATE TABLE {_SCHEMA}.partition_root (id bigint) PARTITION BY RANGE(id)",
                f"CREATE TABLE {_SCHEMA}.protected_leaf PARTITION OF {_SCHEMA}.partition_root FOR VALUES FROM (0) TO (100)",
                f"CREATE TABLE {_SCHEMA}.required_parent (id bigint) PARTITION BY RANGE(id)",
                f"CREATE TABLE {_SCHEMA}.unenrolled_child PARTITION OF {_SCHEMA}.required_parent FOR VALUES FROM (0) TO (100)",
                f"INSERT INTO {_SCHEMA}.protected_leaf VALUES (7)",
                f"INSERT INTO {_SCHEMA}.unenrolled_child VALUES (8)",
            ):
                await session.execute(text(sql))
            await session.execute(text(f"CREATE TABLE {_SCHEMA}.unrelated_parent (id int) PARTITION BY LIST(id)"))
            for i in range(130):
                await session.execute(text(
                    f"CREATE TABLE {_SCHEMA}.unrelated_{i} PARTITION OF {_SCHEMA}.unrelated_parent FOR VALUES IN ({i})"
                ))
            await session.execute(text(f"INSERT INTO {_SCHEMA}.unrelated_parent VALUES (123)"))
        self.addAsyncCleanup(pg.execute, f"DROP SCHEMA {_SCHEMA} CASCADE")
        layers = object.__new__(EtlLayers)
        layers._pg = pg
        log = []
        execute = AsyncSession.execute

        async def capture(session, query, params=None, **kwargs):
            log.append(" ".join(str(query).split()))
            return await execute(session, query, params, **kwargs)

        enrolled = tuple(RequiredIndexedPreviewTable(schema=_SCHEMA, table=t)
                         for t in ("required", "protected_leaf", "required_parent", "absent"))
        with (
            patch.object(settings, "ENVIRONMENT", "production"),
            patch.object(settings, "ONTOLOGY_EXPLORER_INDEXED_SAMPLE_ENABLED", False),
            patch.object(settings, "ONTOLOGY_EXPLORER_REQUIRED_INDEXED_PREVIEW_TABLES", enrolled),
            patch("app.web.api.routes.etl.get_etl_layers", return_value=layers),
            patch.object(AsyncSession, "execute", capture),
        ):
            async with api(True) as client:
                for alias in (_SCHEMA, _SCHEMA.removeprefix("ts_")):
                    table = f"/etl/{alias}/required"
                    before = len(log)
                    for response in (await client.get(table + "/data"),
                                     await client.get(table + "/data?partition=wrong"),
                                     await client.post(table + "/data", json={}),
                                     await client.post(table + "/preview-rows", json={"limit": 1})):
                        self.assertEqual(response.status_code, 409, response.text)
                        self.assertEqual(response.json()["detail"]["code"], "indexed_preview_required")
                    self.assertEqual(log[before:], [])
                    mode = await client.get(table + "/row-preview-policy")
                    self.assertEqual((mode.json(), mode.headers["cache-control"]),
                                     ({"mode": "indexed_id_required"}, "private, no-store"))
                    self.assertEqual((await client.get(table + "/row-preview-policy?partition=wrong")).status_code, 422)
                self.assertEqual((await client.get(f"/etl/{_SCHEMA}/absent/data")).status_code, 409)
                self.assertEqual((await client.get(f"/etl/{_SCHEMA}/Required/data")).status_code, 400)
                table = f"/etl/{_SCHEMA}/unrelated"
                self.assertEqual((await client.get(table + "/row-preview-policy")).json(), {"mode": "classic"})
                self.assertEqual((await client.get(table + "/data")).json()["rows"], [{"id": 1}])
                huge = f"/etl/{_SCHEMA}/unrelated_parent"
                self.assertEqual((await client.get(huge + "/row-preview-policy")).json(), {"mode": "classic"})
                self.assertEqual((await client.get(huge + "/data")).json()["rows"], [{"id": 123}])
                for table, query in (("partition_root", ""), ("partition_root", "?partition=protected_leaf"),
                                     ("unenrolled_child", "")):
                    before = len(log)
                    blocked = await client.get(f"/etl/{_SCHEMA}/{table}/data" + query)
                    self.assertEqual(blocked.status_code, 409, blocked.text)
                    self.assertEqual(blocked.json()["detail"]["code"], "indexed_preview_required")
                    self.assertFalse(any("SELECT t.*" in sql or "flow_count" in sql or "EXPLAIN " in sql for sql in log[before:]))
                for protected in ("partition_root", "unenrolled_child"):
                    self.assertEqual((await client.get(f"/etl/{_SCHEMA}/{protected}/row-preview-policy")).json(),
                                     {"mode": "indexed_id_required"})
        self.assertTrue(any("pg_catalog.pg_inherits" in query for query in log))

    async def test_projection_requires_current_review_and_exact_index_only_plan(self):
        from uuid import uuid4

        from app.web.core.config.indexed_sample import IndexedCompoundSampleScope, IndexedProjectionScope, IndexedSampleScope

        url = make_url(_URL)
        if url.database != "ontology_creation_regression" or url.host not in {"127.0.0.1", "::1"} or url.query:
            self.fail("Only the exact disposable Explorer database may be used")
        pg = PostgresClient(_URL)
        await pg.connect(max_retries=1)
        self.addAsyncCleanup(pg.close)
        schema, table = "ts_projection_probe", "sampled"
        marker = uuid4()
        async with pg.session() as session:
            for statement in ontology.ONTOLOGY_DDL_STATEMENTS:
                await session.execute(text(statement))
            for sql in (
                f"CREATE SCHEMA {schema}",
                "CREATE TABLE public.ontology_explorer_indexed_sample_identity(marker uuid PRIMARY KEY)",
                f"CREATE TABLE {schema}.{table} (id bigint, flag boolean, short varchar(32), private text, "
                "PRIMARY KEY(id) INCLUDE(flag,short))",
                f"INSERT INTO {schema}.{table} SELECT g,CASE WHEN g % 2 = 0 THEN true END,"
                "'甲',repeat('never-visible',8) FROM generate_series(1,110000) g",
                f"ANALYZE {schema}.{table}",
            ):
                await session.execute(text(sql))
            await session.execute(text("INSERT INTO public.ontology_explorer_indexed_sample_identity VALUES(:marker)"),
                                  {"marker": marker})
            pins = (await session.execute(text("""
                SELECT i.indrelid::bigint, pg_relation_filenode(i.indrelid)::bigint, i.indexrelid::bigint,
                    pg_relation_filenode(i.indexrelid)::bigint
                FROM pg_index i WHERE i.indrelid=to_regclass(:relation) AND i.indisprimary
            """), {"relation": f"{schema}.{table}"})).one()
        self.addAsyncCleanup(pg.execute, f"DROP SCHEMA {schema} CASCADE")
        self.addAsyncCleanup(pg.execute, "DROP TABLE public.ontology_explorer_indexed_sample_identity")
        binding = dict(database=url.database, schema=schema, table=table, heapOid=pins[0], heapFilenode=pins[1],
                       indexOid=pins[2], indexFilenode=pins[3])
        profile = IndexedProjectionScope(**binding, keyColumn="id", marker=marker, include=[
            {"name": "flag", "type": "boolean"}, {"name": "short", "type": "varchar(32)"},
        ])
        layers = object.__new__(EtlLayers)
        layers._pg = pg
        statements = []
        execute, scalar = AsyncSession.execute, AsyncSession.scalar

        def record(query, params):
            sql = " ".join(str(query).split())
            if f'FROM ONLY "{schema}"."{table}"' in sql and sql.startswith(("SELECT r.", "EXPLAIN ")):
                statements.append((sql, params))

        async def capture(session, query, params=None, **kwargs):
            record(query, params)
            return await execute(session, query, params, **kwargs)

        async def capture_scalar(session, query, params=None, **kwargs):
            record(query, params)
            return await scalar(session, query, params, **kwargs)

        async def sessions():
            async with pg.session() as session:
                yield session

        base = f"/etl/{schema}/{table}"
        route = base + "/indexed-projection-preview"
        with (
            patch.multiple(settings, ENVIRONMENT="local", ONTOLOGY_EXPLORER_INDEXED_PROJECTION_ENABLED=True,
                           ONTOLOGY_EXPLORER_INDEXED_PROJECTION_PROFILE=profile,
                           ONTOLOGY_EXPLORER_REQUIRED_INDEXED_PREVIEW_TABLES=(RequiredIndexedPreviewTable(
                               schema=schema, table=table, mode="indexed_projection_required",
                           ),),
                           ONTOLOGY_EXPLORER_INDEXED_SAMPLE_ENABLED=True,
                           ONTOLOGY_EXPLORER_INDEXED_SAMPLE_DATABASE_MARKER=marker,
                           ONTOLOGY_EXPLORER_INDEXED_SAMPLE_ALLOWLIST=[IndexedSampleScope(**binding, keyColumn="id")],
                           ONTOLOGY_EXPLORER_INDEXED_COMPOUND_SAMPLE_ENABLED=True,
                           ONTOLOGY_EXPLORER_INDEXED_COMPOUND_SAMPLE_PROFILE=IndexedCompoundSampleScope(**binding)),
            patch("app.web.api.routes.etl.get_etl_layers", return_value=layers),
            patch.object(AsyncSession, "execute", capture),
            patch.object(AsyncSession, "scalar", capture_scalar),
        ):
            async with api(True, sessions) as client:
                mode = await client.get(base + "/row-preview-policy")
                self.assertEqual(mode.json(), {"mode": "indexed_projection_required"})
                cap = await client.get(route)
                self.assertTrue(cap.json().get("available"), cap.text)
                fields = [{"name": "id", "type": "bigint"}, {"name": "flag", "type": "boolean"},
                          {"name": "short", "type": "varchar(32)"}]
                self.assertEqual(cap.json()["fields"], fields)
                self.assertEqual(cap.headers["cache-control"], "private, no-store")
                revision = cap.json()["projectionRevision"]
                for old in ("/indexed-id-sample", "/indexed-compound-id-sample"):
                    self.assertFalse((await client.get(base + old)).json()["available"])
                    bounds = {"idFrom": "1", "idTo": "29"}
                    if old.startswith("/indexed-compound"):
                        bounds.update(tenantId="1", groupId="1")
                    self.assertEqual((await client.post(base + old, json=bounds)).json()["reason"], "scope_unavailable")
                self.assertEqual((await client.get(base + "/data")).status_code, 409)
                self.assertEqual((await client.post(base + "/preview-rows", json={})).status_code, 409)
                with patch.object(settings, "ONTOLOGY_EXPLORER_INDEXED_PROJECTION_ENABLED", False):
                    self.assertEqual((await client.get(base + "/row-preview-policy")).json(),
                                     {"mode": "indexed_projection_required"})
                    self.assertEqual((await client.get(route)).json(), {"available": False, "limit": 20})
                    self.assertFalse((await client.get(base + "/indexed-id-sample")).json()["available"])
                stale = await client.post(route, json={"idEquals": "42", "expectedProjectionRevision": str(uuid4())})
                self.assertEqual(stale.json()["reason"], "scope_unavailable")
                invalid = await client.post(route, json={"idEquals": "42", "expectedProjectionRevision": revision,
                                                          "fields": ["private"]})
                self.assertEqual(invalid.status_code, 422)
                self.assertEqual(statements, [])
                body = {"expectedProjectionRevision": revision, "idFrom": "1", "idTo": "29"}
                sampled = await client.post(route, json=body)
                self.assertEqual(sampled.json()["state"], "sampled", sampled.text)
                self.assertEqual(sampled.headers["cache-control"], "private, no-store")
                self.assertEqual(sampled.json()["rows"][:2], [["1", None, "甲"], ["2", True, "甲"]])
                self.assertEqual((len(sampled.json()["rows"]), sampled.json()["hasMore"]), (20, True))
                self.assertEqual((sampled.json()["fields"], sampled.json()["projectionRevision"]), (fields, revision))
                self.assertEqual(len(statements), 2)
                self.assertEqual(statements[0][0].split(") ", 1)[1], statements[1][0])
                self.assertEqual(statements[0][1], statements[1][1])
                self.assertNotIn("private", str(statements))
                with patch("app.web.services.indexed_projection_preview._verified", return_value=False):
                    self.assertEqual((await client.post(route, json=body)).json()["reason"], "unsupported_plan")
                self.assertEqual(len(statements), 3)
                exact = await client.post(route, json={"expectedProjectionRevision": revision, "idEquals": "42"})
                self.assertEqual(exact.json()["rows"], [["42", True, "甲"]], exact.text)
                async with pg.session() as concurrent:
                    await concurrent.execute(text(f"LOCK TABLE ONLY {schema}.{table} IN ROW EXCLUSIVE MODE"))
                    self.assertEqual((await client.post(route, json=body)).json()["state"], "blocked")
                await pg.execute(f"REINDEX INDEX {schema}.{table}_pkey")
                self.assertEqual((await client.post(route, json=body)).json()["reason"], "scope_unavailable")
                self.assertEqual((await client.get(route)).json(), {"available": False, "limit": 20})
