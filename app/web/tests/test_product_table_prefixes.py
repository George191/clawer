"""Exercise table renames against an explicitly selected disposable database."""

from importlib import import_module
import os
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

migration = import_module("app.web.alembic.versions.f6a8c2d901be_product_table_prefixes")
URL = os.getenv("PRODUCT_PREFIX_TEST_DATABASE_URL")


def run_migration(connection, operation):
    with Operations.context(MigrationContext.configure(connection)):
        operation()


@unittest.skipUnless(URL, "requires disposable product_prefix_regression database")
class ProductTablePrefixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertEqual(make_url(URL).database, "product_prefix_regression")
        self.engine = create_async_engine(URL)
        self.addAsyncCleanup(self.engine.dispose)
        self.connection = await self.engine.connect()
        self.addAsyncCleanup(self.connection.close)
        self.transaction = await self.connection.begin()
        self.addAsyncCleanup(self.transaction.rollback)
        await self.connection.execute(text("CREATE SCHEMA app"))

    async def test_upgrade_downgrade_preserve_identity_rows_fk_and_trigger(self):
        db = self.connection
        for old, _ in migration.TABLE_RENAMES:
            await db.execute(text(f'CREATE TABLE app."{old}" (id bigint PRIMARY KEY, value bigint DEFAULT 0)'))
            await db.execute(text(f'INSERT INTO app."{old}"(id) VALUES (1)'))
        await db.execute(text("ALTER TABLE app.ontology_object_properties ADD CONSTRAINT parent_fk FOREIGN KEY(id) REFERENCES app.ontology_objects(id)"))
        await db.exec_driver_sql("""CREATE FUNCTION app.prefix_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN UPDATE app.ontology_objects SET value=value+1 WHERE id=NEW.id; RETURN NEW; END $$""")
        await db.execute(text("CREATE TRIGGER prefix_test AFTER UPDATE ON app.ontology_object_properties FOR EACH ROW EXECUTE FUNCTION app.prefix_trigger()"))
        before = dict((await db.execute(text("SELECT relname,oid FROM pg_class WHERE relnamespace='app'::regnamespace AND relkind='r'"))).all())
        await db.run_sync(run_migration, migration.upgrade)
        for old, new in migration.TABLE_RENAMES:
            self.assertEqual(await db.scalar(text("SELECT to_regclass(:name)::oid"), {"name": f"app.{new}"}), before[old])
            self.assertIsNone(await db.scalar(text("SELECT to_regclass(:name)"), {"name": f"app.{old}"}))
            self.assertEqual(await db.scalar(text(f'SELECT count(*) FROM app."{new}"')), 1)
        await db.execute(text("UPDATE app.helio_object_properties SET value=1 WHERE id=1"))
        self.assertEqual(await db.scalar(text("SELECT value FROM app.helio_objects WHERE id=1")), 1)
        self.assertEqual(await db.scalar(text("SELECT confrelid FROM pg_constraint WHERE conname='parent_fk' AND connamespace='app'::regnamespace")), before['ontology_objects'])
        await db.run_sync(run_migration, migration.downgrade)
        await db.execute(text("UPDATE app.ontology_object_properties SET value=2 WHERE id=1"))
        self.assertEqual(await db.scalar(text("SELECT value FROM app.ontology_objects WHERE id=1")), 2)

    async def test_conflict_fails_before_any_rename(self):
        db = self.connection
        await db.execute(text("CREATE TABLE app.ai_collect_analyses(id int)"))
        await db.execute(text("CREATE TABLE app.ontology_objects(id int)"))
        await db.execute(text("CREATE TABLE app.helio_objects(id int)"))
        with self.assertRaisesRegex(RuntimeError, "Both app.ontology_objects"):
            await db.run_sync(run_migration, migration.upgrade)
        self.assertIsNotNone(await db.scalar(text("SELECT to_regclass('app.ai_collect_analyses')")))
