"""Real creation checks limited to the disposable ontology_creation_regression database."""

import os
import unittest
from copy import deepcopy
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.etl.schema import ontology
from app.etl.schema.object_relations import OBJECT_RELATIONS_DDL_STATEMENTS
from app.storage.postgres_client import PostgresClient
from app.web.services.etl_layers import EtlLayers
from app.web.services.etl_workspace import EtlWorkspace


URL = os.environ.get("ONTOLOGY_TEST_DATABASE_URL")
DATABASE = "ontology_creation_regression"
SCHEMA = "ts_creation"
TARGET_SCHEMA = "ts_creation_targets"
PAYLOAD = {"name": "firms", "dataType": "company", "description": "Companies registered through Flow",
    "fields": [
        {"name": "record_id", "type": "TEXT", "primary": True, "nullable": False, "comment": "Business record identifier"},
        {"name": "company_name", "type": "VARCHAR", "length": "160", "nullable": False, "comment": "Registered company name"}],
    "indexes": [{"name": "company_name_lookup", "type": "BTREE", "fields": "company_name", "unique": False}],
    "checks": [{"name": "company_name_nonempty", "expression": "length(company_name) > 0"}],
    "options": {"comment": "Companies registered through Flow"}}


@unittest.skipUnless(URL, "Requires an isolated ontology_creation_regression PostgreSQL instance")
class OntologyCreationIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Reject other database names before even opening a connection.
        if make_url(URL).database != DATABASE:
            raise RuntimeError("Creation tests refuse any database except ontology_creation_regression")
        self.pg = PostgresClient(URL)
        await self.pg.connect(max_retries=1)
        if (await self.pg.fetch_one("SELECT current_database() AS name"))["name"] != DATABASE:
            await self.pg.close()
            raise RuntimeError("Connected database is not ontology_creation_regression")
        async with self.pg.session() as session:
            for schema in (SCHEMA, TARGET_SCHEMA):
                await session.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
                await session.execute(text(f"CREATE SCHEMA {schema}"))
            for statement in (*ontology.ONTOLOGY_DDL_STATEMENTS, *OBJECT_RELATIONS_DDL_STATEMENTS):
                await session.execute(text(statement))
            await session.execute(text("""DELETE FROM public.ontology_object_relations
                WHERE source_schema IN (:source, :target) OR target_schema IN (:source, :target)"""),
                {"source": SCHEMA, "target": TARGET_SCHEMA})
            await session.execute(text("DELETE FROM public.ontology_objects WHERE schema_name IN (:source, :target)"),
                {"source": SCHEMA, "target": TARGET_SCHEMA})
        self.layers = object.__new__(EtlLayers)
        self.layers._pg = self.pg
        self.workspace = object.__new__(EtlWorkspace)
        self.workspace._pg, self.workspace._layers = self.pg, self.layers

    async def asyncTearDown(self):
        await self.pg.close()

    async def metadata(self, table="firms"):
        return await self.pg.fetch_one("SELECT * FROM public.ontology_objects WHERE schema_name=:schema AND table_name=:table",
                                       {"schema": SCHEMA, "table": table})

    async def assert_absent(self, table):
        self.assertIsNone((await self.pg.fetch_one("SELECT to_regclass(:qualified) AS name", {"qualified": f"{SCHEMA}.{table}"}))["name"])
        self.assertIsNone(await self.metadata(table))
        self.assertEqual(await self.pg.fetch_all("""SELECT p.name FROM public.ontology_object_properties p
            JOIN public.ontology_objects o ON o.id=p.object_id WHERE o.schema_name=:schema AND o.table_name=:table""",
            {"schema": SCHEMA, "table": table}), [])

    async def relation_payload(self):
        await self.workspace.create_table(TARGET_SCHEMA, {"name": "companies", "dataType": "company",
            "fields": deepcopy(PAYLOAD["fields"])})
        return {**deepcopy(PAYLOAD), "relations": [{"name": "firms_company", "targetSchema": TARGET_SCHEMA,
            "targetTable": "companies", "sourceFields": ["record_id"], "targetFields": ["record_id"],
            "cardinality": "many_to_one", "description": "Parent company"}]}

    async def test_lineage_neighbors_are_direct_typed_visible_and_bounded(self):
        await self.workspace.create_table(SCHEMA, await self.relation_payload())
        history = f"{SCHEMA}_hist"
        async with self.pg.session() as session:
            await session.execute(text(f"DROP SCHEMA IF EXISTS {history} CASCADE"))
            await session.execute(text(f"CREATE SCHEMA {history}"))
            await session.execute(text(f"""CREATE TABLE {history}.not_current
                (record_id text REFERENCES {SCHEMA}.firms(record_id))"""))
            await session.execute(text(f"""ALTER TABLE {SCHEMA}.firms ADD CONSTRAINT firms_company_fk
                FOREIGN KEY (record_id) REFERENCES {TARGET_SCHEMA}.companies(record_id)"""))
            for index in range(14):
                await session.execute(text(f"""CREATE TABLE {SCHEMA}.peer_{index:02}
                    (record_id text REFERENCES {SCHEMA}.firms(record_id))"""))
            await session.execute(text(f"CREATE VIEW {SCHEMA}.not_table AS SELECT * FROM {SCHEMA}.firms"))
            await session.execute(text(f"""
                INSERT INTO public.ontology_object_relations
                    (name, source_schema, source_table, source_fields, target_schema, target_table,
                     target_fields, relationship_kind, cardinality, provenance, enabled)
                VALUES ('missing_column', :schema, 'firms', ARRAY['removed'], :schema,
                        'peer_00', ARRAY['record_id'], 'association', 'many_to_one', 'test', true),
                       ('disabled_link', :schema, 'firms', ARRAY['record_id'], :schema,
                        'peer_01', ARRAY['record_id'], 'association', 'many_to_one', 'test', false),
                       ('view_link', :schema, 'firms', ARRAY['record_id'], :schema,
                        'not_table', ARRAY['record_id'], 'association', 'many_to_one', 'test', true)
            """), {"schema": SCHEMA})

        outgoing = await self.layers.lineage_neighbors(SCHEMA, "firms", "outgoing")
        self.assertEqual(outgoing.model_dump(by_alias=True), {"direction": "outgoing", "truncated": False, "neighbors": [
            {"schema": TARGET_SCHEMA, "table": "companies", "kinds": ["foreign_key", "object_relation"]}]})

        incoming = await self.layers.lineage_neighbors(SCHEMA, "firms", "incoming")
        self.assertTrue(incoming.truncated)
        self.assertEqual([node.table for node in incoming.neighbors], [f"peer_{i:02}" for i in range(12)])
        self.assertTrue(all(node.kinds == ["foreign_key"] for node in incoming.neighbors))

        for table in ("peer_00", "peer_01"):
            await self.workspace.set_object_visibility(SCHEMA, table, "hidden", None)
        incoming = await self.layers.lineage_neighbors(SCHEMA, "firms", "incoming")
        self.assertFalse(incoming.truncated)
        self.assertEqual([node.table for node in incoming.neighbors], [f"peer_{i:02}" for i in range(2, 14)])

        for schema, table in ((SCHEMA, "peer_00"), (SCHEMA, "not_table"),
                              (history, "not_current"), ("creation", "firms")):
            with self.assertRaises(HTTPException) as error:
                await self.layers.lineage_neighbors(schema, table, "outgoing")
            self.assertEqual(error.exception.status_code, 404)

    async def test_lineage_neighbors_keep_native_fk_when_optional_registry_is_absent(self):
        async with self.pg.session() as session:
            await session.execute(text(f"CREATE TABLE {SCHEMA}.parent (id bigint PRIMARY KEY)"))
            await session.execute(text(f"""CREATE TABLE {SCHEMA}.child
                (parent_id bigint REFERENCES {SCHEMA}.parent(id))"""))
            await session.execute(text("DROP TABLE public.ontology_object_relations"))
        for root, direction, other in (("parent", "incoming", "child"), ("child", "outgoing", "parent")):
            page = await self.layers.lineage_neighbors(SCHEMA, root, direction)
            self.assertEqual(page.model_dump(by_alias=True), {
                "direction": direction, "truncated": False,
                "neighbors": [{"schema": SCHEMA, "table": other, "kinds": ["foreign_key"]}],
            })
        self.assertIsNone((await self.pg.fetch_one(
            "SELECT to_regclass('public.ontology_object_relations') AS registry"))["registry"])

    async def test_logical_relation_is_persisted_cross_schema_and_visible_from_both_objects_without_foreign_keys(self):
        payload = await self.relation_payload()
        await self.workspace.create_table(SCHEMA, payload)
        row = await self.pg.fetch_one("SELECT * FROM public.ontology_object_relations WHERE name='firms_company'")
        self.assertEqual((row["source_schema"], row["target_schema"], row["source_fields"], row["target_fields"]),
                         (SCHEMA, TARGET_SCHEMA, ["record_id"], ["record_id"]))
        self.assertEqual((row["provenance"], row["relationship_kind"], row["cardinality"]),
                         ("ontology:create_table", "association", "many_to_one"))
        outgoing = (await self.layers.preview(SCHEMA, "firms"))["linkedObjectTypes"]
        incoming = (await self.layers.preview(TARGET_SCHEMA, "companies"))["linkedObjectTypes"]
        self.assertEqual([(item["id"], item["direction"]) for item in outgoing], [(f"{TARGET_SCHEMA}.companies", "outgoing")])
        self.assertEqual([(item["id"], item["direction"]) for item in incoming], [(f"{SCHEMA}.firms", "incoming")])
        self.assertEqual(outgoing[0]["constraints"], [])
        self.assertEqual(outgoing[0]["relationships"][0]["sourceKind"], "logical")
        constraints = await self.pg.fetch_all("SELECT contype::text AS kind FROM pg_constraint WHERE conrelid=to_regclass(:table)",
                                             {"table": f"{SCHEMA}.firms"})
        self.assertNotIn("f", {item["kind"] for item in constraints})

    async def test_multiple_composite_indexes_are_created_with_distinct_ordered_properties(self):
        payload = deepcopy(PAYLOAD)
        payload["indexes"] = [{"name": "record_name", "fields": ["record_id", "company_name"]},
                              {"name": "name_record", "fields": ["company_name", "record_id"]}]
        await self.workspace.create_table(SCHEMA, payload)
        rows = await self.pg.fetch_all("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname=:schema", {"schema": SCHEMA})
        indexes = {row["indexname"]: row["indexdef"] for row in rows}
        self.assertIn("(record_id, company_name)", indexes["record_name"])
        self.assertIn("(company_name, record_id)", indexes["name_record"])

    async def test_stale_or_unregistered_target_properties_reject_and_roll_back_creation(self):
        payload = await self.relation_payload()
        async with self.pg.session() as session:
            await session.execute(text(f"ALTER TABLE {TARGET_SCHEMA}.companies DROP COLUMN company_name"))
        payload["relations"][0].update(sourceFields=["company_name"], targetFields=["company_name"])
        with self.assertRaises(HTTPException) as error:
            await self.workspace.create_table(SCHEMA, payload)
        self.assertEqual(error.exception.status_code, 422)
        await self.assert_absent("firms")
        self.assertIsNone(await self.pg.fetch_one("SELECT name FROM public.ontology_object_relations WHERE name='firms_company'"))

    async def test_relation_write_failure_rolls_back_table_indexes_properties_and_earlier_relation(self):
        payload = await self.relation_payload()
        payload["relations"].append({**payload["relations"][0], "name": "firms_company_rejected",
                                     "sourceFields": ["company_name"], "targetFields": ["company_name"]})
        async with self.pg.session() as session:
            await session.execute(text(f"""CREATE FUNCTION {SCHEMA}.reject_relation() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'Isolated relation rejection' USING ERRCODE='23514';
                END; $$"""))
            await session.execute(text(f"""CREATE TRIGGER creation_reject_relation BEFORE INSERT
                ON public.ontology_object_relations FOR EACH ROW WHEN (NEW.name='firms_company_rejected')
                EXECUTE FUNCTION {SCHEMA}.reject_relation()"""))
        try:
            with self.assertRaises(HTTPException) as error:
                await self.workspace.create_table(SCHEMA, payload)
            self.assertEqual(error.exception.status_code, 422)
            await self.assert_absent("firms")
            self.assertEqual(await self.pg.fetch_all("SELECT name FROM public.ontology_object_relations WHERE source_schema=:schema",
                                                     {"schema": SCHEMA}), [])
            self.assertIsNone((await self.pg.fetch_one("SELECT to_regclass(:table) AS name",
                                                     {"table": f"{SCHEMA}.company_name_lookup"}))["name"])
        finally:
            async with self.pg.session() as session:
                await session.execute(text("DROP TRIGGER creation_reject_relation ON public.ontology_object_relations"))
                await session.execute(text(f"DROP FUNCTION {SCHEMA}.reject_relation()"))

    async def test_creation_commits_ddl_registration_properties_and_comments(self):
        created = await self.workspace.create_table(SCHEMA, deepcopy(PAYLOAD))
        self.assertEqual((created["name"], created["schemaName"], created["dataType"]), ("firms", SCHEMA, "company"))
        row = await self.metadata()
        self.assertEqual((row["data_type"], row["data_type_source"], row["description"]),
                         ("company", "registration", PAYLOAD["description"]))
        self.assertEqual(row["provenance"], "flow:create_table")
        self.assertTrue(row["is_active"])
        self.assertIn(f'CREATE TABLE "{SCHEMA}"."firms"', row["ddl_sql"])
        columns = await self.pg.fetch_all("""SELECT name, sql_type, ordinal_position, is_nullable,
            is_primary_key, primary_key_position, comment FROM public.ontology_object_properties
            WHERE object_id=:id ORDER BY ordinal_position""", {"id": row["id"]})
        self.assertEqual(columns, [
            {"name": "record_id", "sql_type": "text", "ordinal_position": 1, "is_nullable": False,
             "is_primary_key": True, "primary_key_position": 1, "comment": "Business record identifier"},
            {"name": "company_name", "sql_type": "character varying(160)", "ordinal_position": 2, "is_nullable": False,
             "is_primary_key": False, "primary_key_position": None, "comment": "Registered company name"}])
        physical = await self.pg.fetch_one("SELECT obj_description(to_regclass(:qualified), 'pg_class') AS description",
                                          {"qualified": f"{SCHEMA}.firms"})
        self.assertEqual(physical["description"], PAYLOAD["description"])
        constraints = await self.pg.fetch_all("""SELECT conname, contype::text AS kind FROM pg_constraint
            WHERE conrelid=to_regclass(:qualified) ORDER BY conname""", {"qualified": f"{SCHEMA}.firms"})
        self.assertEqual({row["kind"] for row in constraints}, {"p", "c"})
        self.assertIsNotNone((await self.pg.fetch_one("SELECT to_regclass(:qualified) AS name",
                                                    {"qualified": f"{SCHEMA}.company_name_lookup"}))["name"])

    async def test_new_connection_reads_registered_type_from_catalog_and_preview(self):
        await self.workspace.create_table(SCHEMA, deepcopy(PAYLOAD))
        reader = PostgresClient(URL)
        await reader.connect(max_retries=1)
        layers = object.__new__(EtlLayers)
        layers._pg = reader
        try:
            catalog = await layers.object_types(schema=SCHEMA)
            self.assertEqual(catalog["total"], 1)
            self.assertEqual((catalog["items"][0]["id"], catalog["items"][0]["dataType"], catalog["items"][0]["dataTypeSource"]),
                             (f"{SCHEMA}.firms", "company", "registration"))
            preview = await layers.preview(SCHEMA, "firms")
            self.assertEqual((preview["dataType"], preview["dataTypeSource"], preview["primaryKey"]),
                             ("company", "registration", ["record_id"]))
            self.assertEqual(preview["description"], PAYLOAD["description"])
            self.assertEqual([item["name"] for item in preview["properties"]], ["record_id", "company_name"])
        finally:
            await reader.close()

    async def test_metadata_resync_preserves_explicit_semantic_type(self):
        await self.workspace.create_table(SCHEMA, deepcopy(PAYLOAD))
        before = await self.metadata()
        async with self.pg.session() as session:
            await ontology.sync_object_metadata(session, SCHEMA, "firms", provenance="isolated:resync")
        after = await self.metadata()
        self.assertEqual((after["id"], after["data_type"], after["data_type_source"]),
                         (before["id"], "company", "registration"))
        self.assertEqual(after["description"], before["description"])
        self.assertEqual((await self.layers.preview(SCHEMA, "firms"))["dataType"], "company")

    async def test_invalid_or_missing_type_is_rejected_before_ddl_or_database_session(self):
        invalid = [{}, *({"dataType": value} for value in (None, "", "Company", " company ", "company-type", 42, True, "x" * 64))]
        with patch.object(self.workspace, "_session", side_effect=AssertionError("Invalid type reached the transaction")) as transaction:
            for position, values in enumerate(invalid):
                with self.subTest(values=values):
                    payload = deepcopy(PAYLOAD)
                    payload.pop("dataType")
                    payload.update(values, name=f"invalid_type_{position}")
                    with self.assertRaises(HTTPException) as raised:
                        await self.workspace.create_table(SCHEMA, payload)
                    self.assertEqual(raised.exception.status_code, 422)
            transaction.assert_not_called()
        for position in range(len(invalid)):
            await self.assert_absent(f"invalid_type_{position}")

    async def test_metadata_database_failure_rolls_back_physical_ddl_and_partial_metadata(self):
        async with self.pg.session() as session:
            await session.execute(text(f"""CREATE FUNCTION {SCHEMA}.reject_property() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'Isolated metadata write rejection' USING ERRCODE='23514';
                END; $$"""))
            await session.execute(text(f"""CREATE TRIGGER creation_reject_property BEFORE INSERT
                ON public.ontology_object_properties FOR EACH ROW WHEN (NEW.name='company_name')
                EXECUTE FUNCTION {SCHEMA}.reject_property()"""))
        try:
            with self.assertRaises(HTTPException) as raised:
                await self.workspace.create_table(SCHEMA, deepcopy(PAYLOAD))
            self.assertEqual(raised.exception.status_code, 422)
            await self.assert_absent("firms")
            self.assertIsNone((await self.pg.fetch_one("SELECT to_regclass(:qualified) AS name",
                                                     {"qualified": f"{SCHEMA}.company_name_lookup"}))["name"])
        finally:
            async with self.pg.session() as session:
                await session.execute(text("DROP TRIGGER creation_reject_property ON public.ontology_object_properties"))
                await session.execute(text(f"DROP FUNCTION {SCHEMA}.reject_property()"))
