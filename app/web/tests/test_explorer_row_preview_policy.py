"""Force indexed browsing before legacy row queries on isolated PostgreSQL."""

import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.schema import ontology
from app.storage.postgres_client import PostgresClient
from app.web.core.config import settings
from app.web.core.config.indexed_sample import RequiredIndexedPreviewTable
from app.web.services.etl_layers import EtlLayers
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
