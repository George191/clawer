"""Actual bounded declared paths with whole-edge ACL and graph limit checks."""

import os
import unittest
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.etl.schema import ontology
from app.etl.schema.object_relations import OBJECT_RELATIONS_DDL_STATEMENTS
from app.web.tests.test_transfer_plans import api

_URL = os.getenv("ONTOLOGY_TEST_DATABASE_URL")
_DB = "ontology_creation_regression"
_SCHEMA = "ts_mapping_path_probe"


@unittest.skipUnless(_URL, "requires disposable loopback PostgreSQL")
class ColumnMappingPathsPGTests(unittest.IsolatedAsyncioTestCase):
    async def test_ordinals_bounds_cycles_and_edge_acl(self):
        url = make_url(_URL)
        if url.database != _DB or url.host not in {"127.0.0.1", "::1"} or url.query:
            self.fail("Use only the exact disposable reference-graph database")
        engine = create_async_engine(url)
        self.addAsyncCleanup(engine.dispose)
        role = "lineage_read_" + uuid4().hex[:8]
        password = uuid4().hex

        async def mapping(db, name, start, end, source=("id",), target=("id",), enabled=True):
            await db.execute(text("""
                INSERT INTO app.helio_object_relations
                (name,source_schema,source_table,source_fields,target_schema,target_table,target_fields,
                 relationship_kind,cardinality,provenance,enabled)
                VALUES (:name,:schema,:source,CAST(:from_fields AS text[]),:schema,:target,CAST(:to_fields AS text[]),
                        'fixture','many_to_one','fixture',:enabled)
            """), {"name": "fixture_mapping_" + name, "schema": _SCHEMA, "source": start, "target": end,
                   "from_fields": list(source), "to_fields": list(target), "enabled": enabled})

        async with engine.begin() as db:
            for statement in (*ontology.ONTOLOGY_DDL_STATEMENTS, *OBJECT_RELATIONS_DDL_STATEMENTS):
                await db.execute(text(statement))
            await db.execute(text(f"CREATE SCHEMA {_SCHEMA}"))
            for table in ("a", "b", "c", "d", "e", "fk_only", "invalid", "duplicate", "disabled", "fanout",
                          *[f"fan{i}" for i in range(8)]):
                await db.execute(text(f"CREATE TABLE {_SCHEMA}.{table} (id bigint PRIMARY KEY, alt bigint, sensitive bigint)"))
            await db.execute(text(
                f"ALTER TABLE {_SCHEMA}.fk_only ADD FOREIGN KEY (id) REFERENCES {_SCHEMA}.a(id)"
            ))
            await mapping(db, "ab", "a", "b", ("alt", "id"), ("id", "alt"))
            await mapping(db, "bc", "b", "c", ("alt",), ("id",))
            await mapping(db, "cd", "c", "d")
            await mapping(db, "de", "d", "e")
            await mapping(db, "ca", "c", "a")
            await mapping(db, "invalid", "a", "invalid", ("id", "alt"), ("id", "missing"))
            await mapping(db, "duplicate", "a", "duplicate", ("id", "id"), ("id", "alt"))
            await mapping(db, "disabled", "a", "disabled", enabled=False)
            for i in range(8):
                await mapping(db, f"fan{i}", "fanout", f"fan{i}")
            await db.execute(text(f"""
                INSERT INTO app.helio_objects (layer,schema_name,table_name,visibility,is_active)
                VALUES ('ods','{_SCHEMA}','fan0','hidden',true),('ods','{_SCHEMA}','fan1','normal',false)
            """))
            await db.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'"))
            await db.execute(text(f"GRANT USAGE ON SCHEMA {_SCHEMA} TO {role}"))
            await db.execute(text(f"GRANT SELECT ON app.helio_objects, app.helio_object_relations TO {role}"))
            # The actual selected root id and mapped b.alt are readable. The
            # other pair a.alt→b.id is not, so the whole a→b mapping is suppressed.
            await db.execute(text(f"GRANT SELECT (id) ON {_SCHEMA}.a TO {role}"))
            await db.execute(text(f"GRANT SELECT (alt) ON {_SCHEMA}.b TO {role}"))

        async def cleanup():
            async with engine.begin() as db:
                await db.execute(text(f"DELETE FROM app.helio_object_relations WHERE source_schema='{_SCHEMA}'"))
                await db.execute(text(f"DELETE FROM app.helio_objects WHERE schema_name='{_SCHEMA}'"))
                await db.execute(text(f"DROP SCHEMA {_SCHEMA} CASCADE"))
                await db.execute(text(f"DROP OWNED BY {role}"))
                await db.execute(text(f"DROP ROLE {role}"))

        read_engine = create_async_engine(url.set(username=role, password=password))
        self.addAsyncCleanup(cleanup)
        self.addAsyncCleanup(read_engine.dispose)
        statements = []
        registry_race = []

        class Capture(AsyncSession):
            async def execute(self, statement, params=None, **kwargs):
                sql = str(statement)
                statements.append(sql)
                if sql.startswith("LOCK TABLE ONLY app.helio_object_relations") and registry_race:
                    async with engine.begin() as admin:
                        await admin.execute(text(registry_race.pop()))
                return await super().execute(statement, params, **kwargs)

        async def admin_sessions():
            async with Capture(engine) as db:
                yield db

        async def limited_sessions():
            async with Capture(read_engine) as db:
                yield db

        def path(table="a"):
            return f"/etl/{_SCHEMA}/{table}/lineage-column-mapping-paths"

        async def request(client, table="a", column="id", direction="outgoing"):
            return await client.get(path(table), params={"column": column, "direction": direction})

        def values(output):
            return [[(node["table"], node["column"]) for node in path["nodes"]] for path in output["paths"]]

        async with api(True, admin_sessions) as admin, api(False, admin_sessions) as normal:
            self.assertEqual((await request(normal)).status_code, 403)
            wrong = await admin.get(path() + "?column=id&column=alt&direction=outgoing")
            self.assertEqual((wrong.status_code, wrong.headers["cache-control"]), (404, "private, no-store"))
            self.assertEqual((await request(admin, table="fan0")).status_code, 404)
            result = await request(admin)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["status"], "incomplete")
            self.assertEqual(values(result.json()), [
                [("a", "id"), ("b", "alt")],
                [("a", "id"), ("b", "alt"), ("c", "id")],
                [("a", "id"), ("b", "alt"), ("c", "id"), ("d", "id")],
            ])
            incoming = await request(admin, "a", direction="incoming")
            self.assertEqual(values(incoming.json()), [
                [("c", "id"), ("a", "id")],
                [("b", "alt"), ("c", "id"), ("a", "id")],
            ])
            self.assertEqual(incoming.json()["status"], "complete")
            fanout = (await request(admin, "fanout")).json()
            self.assertEqual(fanout["status"], "complete")
            self.assertEqual([p[-1][0] for p in values(fanout)], [f"fan{i}" for i in range(2, 8)])
            async with engine.begin() as db:
                await db.execute(text(f"UPDATE app.helio_objects SET is_active=true WHERE schema_name='{_SCHEMA}'"))
            self.assertEqual((await request(admin, "fanout")).json()["status"], "incomplete")
            registry_race.append("ALTER TABLE app.helio_object_relations ENABLE ROW LEVEL SECURITY")
            try:
                before = len(statements)
                self.assertEqual((await request(admin)).json(),
                                 {"status": "unavailable", "direction": "outgoing", "paths": []})
                self.assertFalse(any("bounded_mapping" in sql for sql in statements[before:]))
            finally:
                async with engine.begin() as db:
                    await db.execute(text("ALTER TABLE app.helio_object_relations DISABLE ROW LEVEL SECURITY"))


        async with api(True, limited_sessions) as limited:
            self.assertEqual((await request(limited)).json(), {"status": "complete", "direction": "outgoing", "paths": []})
            async with engine.begin() as db:
                await db.execute(text(f"GRANT SELECT (alt) ON {_SCHEMA}.a TO {role}"))
                await db.execute(text(f"GRANT SELECT (id) ON {_SCHEMA}.b TO {role}"))
            self.assertEqual(values((await request(limited)).json()), [[("a", "id"), ("b", "alt")]])
            async with engine.begin() as db:
                await db.execute(text(f"REVOKE SELECT ON app.helio_object_relations FROM {role}"))
            self.assertEqual((await request(limited)).json(), {"status": "unavailable", "direction": "outgoing", "paths": []})
        self.assertTrue(any("READ ONLY" in sql for sql in statements))
        self.assertFalse(any(f"FROM {_SCHEMA}." in sql or "pg_constraint" in sql or "RECEIPT" in sql.upper()
                             for sql in statements))
