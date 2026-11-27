"""Real catalog-only estimator with same-OID and replacement races."""

import os
import unittest
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.etl.schema import ontology
from app.web.services import column_planner_statistic as stats
from app.web.tests.test_transfer_plans import api

_URL = os.getenv("ONTOLOGY_TEST_DATABASE_URL")
_DB = "ontology_creation_regression"
_SCHEMA = "ts_planner_stats"
_PATH = f"/etl/{_SCHEMA}/records/column-planner-statistic"


@unittest.skipUnless(_URL, "requires disposable loopback PostgreSQL")
class PlannerStatisticPGTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_live_statistic_no_data_sql_and_ddl_race_fails_closed(self):
        url = make_url(_URL)
        if url.database != _DB or url.host not in {"127.0.0.1", "::1"} or url.query:
            self.fail("Use only the exact disposable planner-statistic database")
        engine = create_async_engine(url)
        self.addAsyncCleanup(engine.dispose)
        role = "stats_read_" + uuid4().hex[:8]
        role_password = uuid4().hex
        async with engine.begin() as db:
            for statement in ontology.ONTOLOGY_DDL_STATEMENTS:
                await db.execute(text(statement))
            await db.execute(text(f"CREATE SCHEMA {_SCHEMA}"))
            await db.execute(text(f"CREATE TABLE {_SCHEMA}.records (id bigint PRIMARY KEY, value text, unstatted int)"))
            await db.execute(text(f"INSERT INTO {_SCHEMA}.records VALUES (1,NULL,1),(2,NULL,2),(3,'fixture',3),(4,'fixture',4)"))
            await db.execute(text(f"ANALYZE {_SCHEMA}.records (value)"))
            await db.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{role_password}'"))
            await db.execute(text(f"GRANT SELECT ON app.helio_objects TO {role}"))
            await db.execute(text(f"GRANT USAGE ON SCHEMA {_SCHEMA} TO {role}"))
            await db.execute(text(f"GRANT SELECT(value) ON {_SCHEMA}.records TO {role}"))

        async def cleanup():
            async with engine.begin() as db:
                await db.execute(text(f"DROP SCHEMA {_SCHEMA} CASCADE"))
                await db.execute(text(f"DROP OWNED BY {role}"))
                await db.execute(text(f"DROP ROLE {role}"))

        read_engine = create_async_engine(url.set(username=role, password=role_password))
        self.addAsyncCleanup(cleanup)
        self.addAsyncCleanup(read_engine.dispose)
        statements = []
        race_sql = []

        class ObservedSession(AsyncSession):
            async def execute(self, stmt, params=None, **kwargs):
                sql = str(stmt).strip()
                statements.append(sql)
                if sql.startswith("LOCK TABLE ONLY") and race_sql:
                    async with engine.begin() as admin:
                        for ddl in race_sql.pop():
                            await admin.execute(text(ddl))
                return await super().execute(stmt, params, **kwargs)

        async def source_session():
            async with ObservedSession(engine) as db:
                yield db

        async def restricted_session():
            async with ObservedSession(read_engine) as db:
                yield db

        async with api(True, source_session) as client, api(False, source_session) as reader:
            unauth = await reader.get(_PATH, params={"column": "value"})
            self.assertEqual(unauth.status_code, 403)
            for query in ("", "?column=value&column=value", "?column=Value", "?column=value&extra=1", "?column=missing"):
                denied = await client.get(_PATH + query)
                self.assertEqual((denied.status_code, denied.headers["cache-control"]), (404, "private, no-store"))
            found = await client.get(_PATH, params={"column": "value"})
            self.assertEqual(found.status_code, 200, found.text)
            self.assertEqual(found.headers["cache-control"], "private, no-store")
            self.assertEqual(set(found.json()), {"status", "nullFraction", "observedAt"})
            self.assertEqual((found.json()["status"], found.json()["nullFraction"]), ("observed", 0.5))
            self.assertTrue(found.json()["observedAt"].endswith(("Z", "+00:00")))
            self.assertEqual((await client.get(_PATH, params={"column": "unstatted"})).json(), {"status": "unavailable"})
            oid_sql = text(f"SELECT '{_SCHEMA}.records'::regclass::oid")
            async with engine.connect() as db:
                first_oid = await db.scalar(oid_sql)
            race_sql.append((f"ALTER TABLE {_SCHEMA}.records ENABLE ROW LEVEL SECURITY",))
            before = len(statements)
            self.assertEqual((await client.get(_PATH, params={"column": "value"})).status_code, 404)
            self.assertTrue(statements[before:])
            self.assertFalse(any("FROM pg_catalog.pg_stats" in sql for sql in statements[before:]))
            async with engine.begin() as db:
                self.assertEqual(await db.scalar(oid_sql), first_oid)
                await db.execute(text(f"ALTER TABLE {_SCHEMA}.records DISABLE ROW LEVEL SECURITY"))
                await db.execute(text(f"LOCK TABLE {_SCHEMA}.records IN ACCESS EXCLUSIVE MODE"))
                self.assertEqual((await client.get(_PATH, params={"column": "value"})).status_code, 404)

        async with api(True, restricted_session) as limited:
            self.assertEqual((await limited.get(_PATH, params={"column": "value"})).status_code, 404)
            async with engine.begin() as db:
                await db.execute(text(f"GRANT SELECT ON {_SCHEMA}.records TO {role}"))
            self.assertEqual((await limited.get(_PATH, params={"column": "value"})).json()["status"], "observed")
            async with engine.begin() as db:
                await db.execute(text(f"REVOKE USAGE ON SCHEMA {_SCHEMA} FROM {role}"))
            self.assertEqual((await limited.get(_PATH, params={"column": "value"})).status_code, 404)

        async with api(True, source_session) as client:
            race_sql.append((
                f"ALTER TABLE {_SCHEMA}.records RENAME TO prior_records",
                f"CREATE TABLE {_SCHEMA}.records (id bigint PRIMARY KEY, value text, unstatted int)",
            ))
            before = len(statements)
            self.assertEqual((await client.get(_PATH, params={"column": "value"})).status_code, 404)
            self.assertFalse(any("FROM pg_catalog.pg_stats" in sql for sql in statements[before:]))
            async with engine.connect() as db:
                self.assertNotEqual(await db.scalar(oid_sql), first_oid)
        self.assertTrue(any("READ ONLY" in sql for sql in statements))
        self.assertFalse(any(f"FROM {_SCHEMA}.records" in sql or sql.startswith(("INSERT", "UPDATE", "ANALYZE"))
                             for sql in statements))
