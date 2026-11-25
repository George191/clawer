"""Restore missing stores and preserve populated stores during schema renames."""

from importlib import import_module
import os
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

_URL = os.getenv("ONTOLOGY_TEST_DATABASE_URL")
_MIGRATION = import_module("app.web.alembic.versions.b82c1a760e53_handoff_snapshot_schemas")
_TABLES = {
    "handoff": {"sink_state", "copies", "copy_rows"},
    "snapshot": {"identity", "intents", "records", "deliveries"},
}


def migrate(connection, operation):
    with Operations.context(MigrationContext.configure(connection)):
        operation()


@unittest.skipUnless(_URL, "requires disposable local ontology PostgreSQL")
class PrivateSchemaMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        url = make_url(_URL)
        self.assertEqual(url.database, "ontology_creation_regression")
        self.assertIn(url.host, {"127.0.0.1", "::1"})
        self.assertFalse(url.query)
        self.engine = create_async_engine(url)
        self.addAsyncCleanup(self.engine.dispose)
        async with self.engine.connect() as db:
            for old, new, _ in _MIGRATION._SCHEMAS:
                for name in (old, new):
                    self.assertIsNone(await db.scalar(text("SELECT to_regnamespace(:name)"), {"name": name}))

        async def cleanup():
            async with self.engine.begin() as db:
                for old, new, _ in _MIGRATION._SCHEMAS:
                    for name in (old, new):
                        await db.execute(text(f"DROP SCHEMA IF EXISTS {name} CASCADE"))

        self.addAsyncCleanup(cleanup)

    async def test_restore_then_rename_preserves_rows_identity_and_privileges(self):
        async with self.engine.begin() as db:
            await db.run_sync(migrate, _MIGRATION.upgrade)
            for schema, names in _TABLES.items():
                actual = (await db.scalars(text("""
                    SELECT relname FROM pg_class WHERE relnamespace=to_regnamespace(:schema) AND relkind='r'
                """), {"schema": schema})).all()
                self.assertEqual(set(actual), names)
                for name in names:
                    self.assertEqual(await db.scalar(text(f"SELECT count(*) FROM {schema}.{name}")), 0)
            await db.execute(text("INSERT INTO handoff.sink_state VALUES(true,'00000000-0000-4000-8000-000000000001')"))
            await db.execute(text("INSERT INTO snapshot.identity VALUES(true,'00000000-0000-4000-8000-000000000002')"))
            await db.execute(text("""
                INSERT INTO handoff.copies VALUES (
                    '00000000-0000-4000-8000-000000000003','00000000-0000-4000-8000-000000000004',
                    '00000000-0000-4000-8000-000000000005','00000000-0000-4000-8000-000000000006',
                    '00000000-0000-4000-8000-000000000007',repeat('a',64),repeat('b',64),
                    '00000000-0000-4000-8000-000000000008',1,1,now())
            """))
            await db.execute(text("""
                INSERT INTO handoff.copy_rows VALUES ('00000000-0000-4000-8000-000000000003',1,ARRAY['preserved'])
            """))
            await db.execute(text("""
                INSERT INTO snapshot.intents VALUES (
                    '00000000-0000-4000-8000-000000000009','00000000-0000-4000-8000-000000000007',
                    '00000000-0000-4000-8000-000000000004',0,'00000000-0000-4000-8000-000000000006',
                    repeat('a',64),repeat('b',64),1,now())
            """))
            await db.execute(text("""
                INSERT INTO snapshot.records VALUES ('00000000-0000-4000-8000-000000000009',1,42,'preserved')
            """))
            inventory = text("""
                SELECT n.oid,n.nspacl::text,c.relname,c.oid,c.relfilenode,c.relacl::text
                FROM pg_namespace n JOIN pg_class c ON c.relnamespace=n.oid
                WHERE n.nspname IN ('handoff','snapshot') ORDER BY n.oid,c.oid
            """)
            before = (await db.execute(inventory)).all()
            await db.run_sync(migrate, _MIGRATION.downgrade)
            await db.run_sync(migrate, _MIGRATION.upgrade)
            self.assertEqual((await db.execute(inventory)).all(), before)
            self.assertEqual(str(await db.scalar(text("SELECT marker FROM handoff.sink_state"))),
                             "00000000-0000-4000-8000-000000000001")
            self.assertEqual(str(await db.scalar(text("SELECT marker FROM snapshot.identity"))),
                             "00000000-0000-4000-8000-000000000002")
            self.assertEqual(await db.scalar(text("SELECT row_values FROM handoff.copy_rows")), ["preserved"])
            self.assertEqual(await db.scalar(text("SELECT caption FROM snapshot.records")), "preserved")
            self.assertEqual(await db.scalar(text("""
                SELECT confrelid FROM pg_constraint
                WHERE conrelid='handoff.copy_rows'::regclass AND contype='f'
            """)), await db.scalar(text("SELECT 'handoff.copies'::regclass::oid")))
            self.assertEqual(await db.scalar(text("""
                SELECT confrelid FROM pg_constraint
                WHERE conrelid='snapshot.records'::regclass AND contype='f'
            """)), await db.scalar(text("SELECT 'snapshot.intents'::regclass::oid")))

    async def test_existing_target_schema_is_not_overwritten(self):
        async with self.engine.begin() as db:
            await db.execute(text("CREATE SCHEMA handoff"))
            await db.execute(text("CREATE TABLE handoff.keep_me(id integer)"))
            await db.execute(text("INSERT INTO handoff.keep_me VALUES(42)"))
        with self.assertRaisesRegex(RuntimeError, "schema handoff already exists"):
            async with self.engine.begin() as db:
                await db.run_sync(migrate, _MIGRATION.upgrade)
        async with self.engine.connect() as db:
            self.assertEqual(await db.scalar(text("SELECT id FROM handoff.keep_me")), 42)
            self.assertIsNone(await db.scalar(text("SELECT to_regnamespace('ontology_handoff_private')")))
