"""Run only against the disposable, isolated ontology_regression PostgreSQL database."""

import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlalchemy import text

from app.etl.schema.object_relations import manage_object_relations
from app.storage.postgres_client import PostgresClient
from app.web.services.etl_layers import EtlLayers
from app.web.services.etl_workspace import EtlWorkspace

ROOT = Path(__file__).resolve().parents[3]
URL = os.environ.get("ONTOLOGY_TEST_DATABASE_URL")


def migration_module():
    spec = importlib.util.spec_from_file_location("ontology_metadata_migration", ROOT / "tools/ontology_metadata.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(URL, "Requires an isolated ontology_regression PostgreSQL instance")
class OntologyMigrationIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pg = PostgresClient(URL)
        await self.pg.connect(max_retries=1)
        database = await self.pg.fetch_one("SELECT current_database() AS name")
        if database["name"] != "ontology_regression":
            await self.pg.close()
            raise RuntimeError("Integration tests refuse to change any database except ontology_regression")
        self.migration = migration_module()
        async with self.pg.session() as session:
            for schema in ("ts_meta", "ts_ods", "ts_custom"):
                await session.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
                await session.execute(text(f"CREATE SCHEMA {schema}"))
            for table in ("ontology_object_properties", "ontology_object_relations", "ontology_objects"):
                await session.execute(text(f"DROP TABLE IF EXISTS public.{table} CASCADE"))
            await session.execute(text("""CREATE TABLE ts_meta.ddlregistry (
                id BIGSERIAL PRIMARY KEY, layer TEXT NOT NULL, table_role TEXT NOT NULL DEFAULT 'current',
                schema_name TEXT NOT NULL, table_name TEXT NOT NULL, ddl_sql TEXT NOT NULL,
                version INTEGER NOT NULL, checksum TEXT NOT NULL, partition_type TEXT NOT NULL DEFAULT 'none',
                partition_column TEXT, partition_granularity TEXT, partition_count INTEGER, description TEXT,
                updated_by TEXT, is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT ddlregistry_table_role_check CHECK (table_role IN ('current','history')))
            """))
            await session.execute(text("CREATE UNIQUE INDEX uq_ddlregistry_table ON ts_meta.ddlregistry (layer,table_role,schema_name,table_name)"))
            await session.execute(text("CREATE INDEX idx_ddlregistry_lookup ON ts_meta.ddlregistry (layer,table_role,schema_name,table_name,version DESC)"))
            definitions = {
                "ods_organization": "cik TEXT NOT NULL PRIMARY KEY, name TEXT, data_type TEXT NOT NULL DEFAULT 'organization'",
                "ods_filing": "accession_number TEXT NOT NULL, cik TEXT NOT NULL, record_id BIGINT NOT NULL, data_type TEXT DEFAULT 'filing', PRIMARY KEY (record_id,accession_number)",
                "ods_filing_document": "accession_number TEXT NOT NULL, document TEXT, data_type TEXT DEFAULT 'filing_document'",
                "ods_financial_fact": "cik TEXT NOT NULL, accn TEXT, data_type TEXT DEFAULT 'financial_fact'",
            }
            for table, fields in definitions.items():
                ddl = f"CREATE TABLE ts_ods.{table} ({fields});"
                await session.execute(text(ddl))
                if table in {"ods_organization", "ods_filing"}:
                    await session.execute(text("""INSERT INTO ts_meta.ddlregistry
                        (layer,schema_name,table_name,ddl_sql,version,checksum,description)
                        VALUES ('ods','ts_ods',:table,:ddl,7,:checksum,'Registered table description')"""),
                        {"table": table, "ddl": ddl, "checksum": "preserved-" + table})
            await session.execute(text("""CREATE TABLE ts_custom.people (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                obsolete TEXT, name VARCHAR(80) NOT NULL, amount NUMERIC(12,2),
                doubled NUMERIC GENERATED ALWAYS AS (amount * 2) STORED,
                created_at TIMESTAMPTZ DEFAULT now())"""))
            await session.execute(text("ALTER TABLE ts_custom.people DROP COLUMN obsolete"))
            await session.execute(text("COMMENT ON COLUMN ts_custom.people.name IS 'Actual field comment'"))
            await session.execute(text("""CREATE TABLE ts_custom.events (id INT NOT NULL PRIMARY KEY)
                PARTITION BY HASH (id)"""))
            await session.execute(text("CREATE TABLE ts_custom.events_p0 PARTITION OF ts_custom.events FOR VALUES WITH (modulus 1,remainder 0)"))
        self.layers = object.__new__(EtlLayers)
        self.layers._pg = self.pg
        self.workspace = object.__new__(EtlWorkspace)
        self.workspace._pg, self.workspace._layers = self.pg, self.layers

    async def asyncTearDown(self):
        await self.pg.close()

    async def test_apply_preserves_registration_and_persists_all_live_properties(self):
        old = await self.pg.fetch_one("SELECT 'ts_meta.ddlregistry'::regclass::oid AS oid")
        plan = await self.migration.migrate(self.pg)
        self.assertFalse(plan["applied"])
        self.assertEqual(plan["registered_objects"], 2)
        self.assertEqual(plan["physical_objects"], 6)
        self.assertFalse(plan["installed"]["public.ontology_objects"])
        applied = await self.migration.migrate(self.pg, apply=True)
        self.assertTrue(applied["existing_registration_preserved"])
        self.assertTrue(applied["physical_property_counts_verified"])
        actual = await self.pg.fetch_one("""SELECT 'public.ontology_objects'::regclass::oid AS oid,
            to_regclass('ts_meta.ddlregistry') IS NULL AS old_removed,
            to_regclass('public.ddlregistry_id_seq') IS NULL AS old_sequence_removed""")
        self.assertEqual(actual["oid"], old["oid"])
        self.assertTrue(actual["old_removed"] and actual["old_sequence_removed"])
        registered = await self.pg.fetch_all("SELECT version,checksum FROM public.ontology_objects WHERE version > 0 ORDER BY table_name")
        self.assertEqual(len(registered), 2)
        self.assertTrue(all(row["version"] == 7 and row["checksum"].startswith("preserved-") for row in registered))
        properties = await self.pg.fetch_all("""SELECT p.* FROM public.ontology_object_properties p
            JOIN public.ontology_objects o ON o.id=p.object_id
            WHERE o.schema_name='ts_custom' AND o.table_name='people' ORDER BY ordinal_position""")
        self.assertEqual([row["name"] for row in properties], ["id", "name", "amount", "doubled", "created_at"])
        fields = {row["name"]: row for row in properties}
        self.assertEqual(fields["id"]["identity_kind"], "a")
        self.assertTrue(fields["id"]["is_primary_key"])
        self.assertEqual(fields["name"]["sql_type"], "character varying(80)")
        self.assertFalse(fields["name"]["is_nullable"])
        self.assertEqual(fields["name"]["comment"], "Actual field comment")
        self.assertEqual(fields["doubled"]["generated_kind"], "s")
        self.assertIsNone(fields["doubled"]["default_expression"])
        self.assertTrue(fields["doubled"]["generation_expression"])
        self.assertEqual(fields["created_at"]["default_expression"], "now()")
        rerun = await self.migration.migrate(self.pg, apply=True)
        self.assertTrue(rerun["existing_registration_preserved"])
        self.assertEqual(rerun["registered_objects_after"], 6)

    async def test_mid_migration_failure_rolls_back_table_move_and_metadata_writes(self):
        original = self.migration.sync_object_metadata
        calls = []
        async def fail_after_first(*args, **kwargs):
            result = await original(*args, **kwargs)
            calls.append(result)
            if len(calls) == 2:
                raise RuntimeError("Deliberate metadata transaction failure")
            return result
        with patch.object(self.migration, "sync_object_metadata", side_effect=fail_after_first):
            with self.assertRaisesRegex(RuntimeError, "Deliberate"):
                await self.migration.migrate(self.pg, apply=True)
        state = await self.pg.fetch_one("""SELECT to_regclass('ts_meta.ddlregistry') IS NOT NULL AS old_exists,
            to_regclass('public.ontology_objects') IS NULL AS new_absent,
            to_regclass('public.ontology_object_properties') IS NULL AS properties_absent""")
        self.assertTrue(all(state.values()))
        self.assertEqual((await self.pg.fetch_one("SELECT count(*) AS count FROM ts_meta.ddlregistry"))["count"], 2)

    async def test_restore_reapply_preserves_ids_versions_and_handles_old_bootstrap_duplicate_indexes(self):
        original_oid = (await self.pg.fetch_one("SELECT 'ts_meta.ddlregistry'::regclass::oid AS oid"))["oid"]
        await self.migration.migrate(self.pg, apply=True)
        original = await self.pg.fetch_all(f"SELECT {self.migration.IDENTITY_COLUMNS} FROM public.ontology_objects ORDER BY id")
        await self.migration.restore_registration(self.pg)
        restored_indexes = await self.pg.fetch_all("""SELECT c.relname AS name FROM pg_index i
            JOIN pg_class c ON c.oid=i.indexrelid WHERE i.indrelid='ts_meta.ddlregistry'::regclass""")
        self.assertTrue(all("ontology_objects" not in row["name"] for row in restored_indexes))
        async with self.pg.session() as session:
            # Reproduce the former restore's new index name plus the old worker bootstrap's duplicate.
            await session.execute(text("ALTER INDEX ts_meta.uq_ddlregistry_table RENAME TO uq_ontology_objects_table"))
            await session.execute(text("""CREATE UNIQUE INDEX uq_ddlregistry_table ON ts_meta.ddlregistry
                (layer,table_role,schema_name,table_name)"""))
            await session.execute(text("""CREATE UNIQUE INDEX IF NOT EXISTS uq_ddlregistry_version ON ts_meta.ddlregistry
                (layer,table_role,schema_name,table_name,version)"""))
            # Quoted identifiers can contain both a quote and the text USING; neither is SQL syntax here.
            unusual = 'idx_ddlregistry_" USING marker'
            for name in (unusual, unusual.replace("ddlregistry", "ontology_objects")):
                await session.execute(text(f"CREATE INDEX {self.migration.identifier(name)} ON ts_meta.ddlregistry (updated_at DESC) WHERE is_active"))
        keep_oid = (await self.pg.fetch_one("SELECT 'ts_meta.uq_ontology_objects_table'::regclass::oid AS oid"))["oid"]
        result = await self.migration.migrate(self.pg, apply=True)
        self.assertTrue(result["existing_registration_preserved"])
        actual = await self.pg.fetch_all(f"SELECT {self.migration.IDENTITY_COLUMNS} FROM public.ontology_objects ORDER BY id")
        self.assertEqual(actual, original)
        self.assertEqual((await self.pg.fetch_one("SELECT 'public.ontology_objects'::regclass::oid AS oid"))["oid"], original_oid)
        self.assertEqual((await self.pg.fetch_one("SELECT 'public.uq_ontology_objects_table'::regclass::oid AS oid"))["oid"], keep_oid)
        indexes = await self.pg.fetch_all("""SELECT c.relname AS name FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
            WHERE i.indrelid='public.ontology_objects'::regclass""")
        self.assertNotIn(unusual, {row["name"] for row in indexes})
        self.assertIn(unusual.replace("ddlregistry", "ontology_objects"), {row["name"] for row in indexes})
        self.assertFalse(any("ddlregistry" in row["name"] for row in indexes))
        await self.migration.restore_registration(self.pg)
        await self.migration.migrate(self.pg, apply=True)
        self.assertEqual(await self.pg.fetch_all(f"SELECT {self.migration.IDENTITY_COLUMNS} FROM public.ontology_objects ORDER BY id"), original)

    async def test_migration_aborts_on_non_equivalent_or_business_index_name_collision(self):
        async with self.pg.session() as session:
            await session.execute(text("CREATE INDEX idx_ddlregistry_collision ON ts_meta.ddlregistry (version)"))
            await session.execute(text("CREATE INDEX idx_ontology_objects_collision ON ts_meta.ddlregistry (version) WHERE is_active"))
        before = await self.pg.fetch_all(f"SELECT {self.migration.IDENTITY_COLUMNS} FROM ts_meta.ddlregistry ORDER BY id")
        with self.assertRaisesRegex(RuntimeError, "different metadata index"):
            await self.migration.migrate(self.pg, apply=True)
        self.assertEqual(await self.pg.fetch_all(f"SELECT {self.migration.IDENTITY_COLUMNS} FROM ts_meta.ddlregistry ORDER BY id"), before)
        async with self.pg.session() as session:
            await session.execute(text("DROP INDEX ts_meta.idx_ontology_objects_collision"))
            await session.execute(text("CREATE TABLE public.ontology_index_collision_fixture (version INTEGER)"))
            await session.execute(text("CREATE INDEX idx_ontology_objects_collision ON public.ontology_index_collision_fixture (version)"))
        business_oid = (await self.pg.fetch_one("SELECT 'public.idx_ontology_objects_collision'::regclass::oid AS oid"))["oid"]
        try:
            with self.assertRaisesRegex(RuntimeError, "different metadata index"):
                await self.migration.migrate(self.pg, apply=True)
            state = await self.pg.fetch_one("""SELECT to_regclass('ts_meta.ddlregistry') IS NOT NULL AS old_exists,
                to_regclass('public.ontology_objects') IS NULL AS new_absent,
                'public.idx_ontology_objects_collision'::regclass::oid AS business_oid""")
            self.assertTrue(state["old_exists"] and state["new_absent"])
            self.assertEqual(state["business_oid"], business_oid)
            self.assertEqual(await self.pg.fetch_all(f"SELECT {self.migration.IDENTITY_COLUMNS} FROM ts_meta.ddlregistry ORDER BY id"), before)
        finally:
            await self.pg.execute("DROP TABLE public.ontology_index_collision_fixture")

    async def test_release_recovery_preserves_object_identity_and_can_be_reapplied(self):
        await self.migration.migrate(self.pg, apply=True)
        before = await self.pg.fetch_all("SELECT id,table_name,version,checksum FROM public.ontology_objects ORDER BY id")
        await self.migration.restore_registration(self.pg)
        self.assertEqual(await self.pg.fetch_all("SELECT id,table_name,version,checksum FROM ts_meta.ddlregistry ORDER BY id"), before)
        await self.migration.migrate(self.pg, apply=True)
        self.assertEqual(await self.pg.fetch_all("SELECT id,table_name,version,checksum FROM public.ontology_objects ORDER BY id"), before)

    async def test_create_table_and_metadata_commit_or_rollback_together(self):
        await self.migration.migrate(self.pg, apply=True)
        payload = {"name": "created_people", "description": "People's : catalog", "fields": [
            {"name": "id", "type": "BIGINT", "primary": True},
            {"name": "title", "type": "VARCHAR", "length": 60, "comment": "A person's label"},
        ]}
        created = await self.workspace.create_table("ts_custom", payload)
        self.assertEqual(created["name"], "created_people")
        preview = await self.layers.preview("ts_custom", "created_people")
        self.assertEqual([row["name"] for row in preview["properties"]], ["id", "title"])
        self.assertEqual(preview["description"], "People's : catalog")
        self.assertEqual(preview["primaryKey"], ["id"])
        registered = await self.pg.fetch_one("SELECT ddl_sql,version,checksum FROM public.ontology_objects WHERE table_name='created_people'")
        self.assertIn('CREATE TABLE "ts_custom"."created_people"', registered["ddl_sql"])
        self.assertEqual(registered["version"], 1)
        self.assertTrue(registered["checksum"])
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as error:
            await self.workspace.rename_schema("ts_custom", {"name": "ts_renamed"})
        self.assertEqual(error.exception.status_code, 409)
        with patch("app.web.services.etl_workspace.sync_object_metadata", side_effect=RuntimeError("metadata failed")):
            with self.assertRaisesRegex(RuntimeError, "metadata failed"):
                await self.workspace.create_table("ts_custom", {**payload, "name": "rolled_back_people"})
        self.assertTrue((await self.pg.fetch_one("SELECT to_regclass('ts_custom.rolled_back_people') IS NULL AS absent"))["absent"])

    async def test_property_type_drift_is_rejected_until_explicit_metadata_sync(self):
        from fastapi import HTTPException
        from app.etl.schema.ontology import sync_object_metadata
        await self.migration.migrate(self.pg, apply=True)
        await self.pg.execute("ALTER TABLE ts_custom.people ALTER COLUMN name TYPE TEXT")
        with self.assertRaises(HTTPException) as error:
            await self.layers.preview("ts_custom", "people")
        self.assertEqual(error.exception.status_code, 409)
        async with self.pg.session() as session:
            await sync_object_metadata(session, "ts_custom", "people")
        preview = await self.layers.preview("ts_custom", "people")
        self.assertEqual(next(row["type"] for row in preview["properties"] if row["name"] == "name"), "text")

    async def test_catalog_and_logical_relationships_use_persisted_ontology_metadata(self):
        await self.migration.migrate(self.pg, apply=True)
        seeds = json.loads((ROOT / "app/etl/schema/object_relations.seed.json").read_text())
        report = await manage_object_relations(self.pg, seeds, apply=True)
        self.assertEqual(len(report["changes"]["insert"]), 4)
        catalog = await self.layers.object_types(limit=100)
        self.assertEqual(catalog["total"], 6)
        self.assertEqual(catalog["groupCounts"]["ingestion"], 4)
        self.assertEqual(catalog["groupCounts"]["other"], 2)
        by_id = {row["id"]: row for row in catalog["items"]}
        self.assertEqual(by_id["ts_ods.ods_organization"]["dataType"], "organization")
        preview = await self.layers.preview("ts_ods", "ods_organization")
        self.assertEqual(preview["description"], "Registered table description")
        self.assertEqual({row["table"] for row in preview["linkedObjectTypes"]}, {"ods_filing", "ods_financial_fact"})
        self.assertTrue(all(relation["sourceKind"] == "logical" for row in preview["linkedObjectTypes"] for relation in row["relationships"]))
        self.assertTrue(all(row["dataType"] for row in preview["linkedObjectTypes"]))
        filing = await self.layers.preview("ts_ods", "ods_filing")
        self.assertEqual(filing["primaryKey"], ["record_id", "accession_number"])
        self.assertTrue(all(not row["constraints"] for row in filing["linkedObjectTypes"]))


if __name__ == "__main__":
    unittest.main()
