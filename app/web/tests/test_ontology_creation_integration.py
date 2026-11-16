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
            await session.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            await session.execute(text(f"CREATE SCHEMA {SCHEMA}"))
            for statement in (*ontology.ONTOLOGY_DDL_STATEMENTS, *OBJECT_RELATIONS_DDL_STATEMENTS):
                await session.execute(text(statement))
            await session.execute(text("DELETE FROM public.ontology_objects WHERE schema_name = :schema"), {"schema": SCHEMA})
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
