"""Real PostgreSQL checks restricted to the disposable ontology_regression database."""

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.etl.schema import ontology, registry
from app.etl.schema.object_relations import OBJECT_RELATIONS_DDL_STATEMENTS, manage_object_relations
from app.storage.postgres_client import PostgresClient
from app.web.services.etl_layers import EtlLayers
from app.web.services.etl_workspace import EtlWorkspace
from app.web.api.dependencies.common import get_current_user
from app.web.api.routes import etl
from app.web.tests.test_ontology_visibility import migration_module


URL = os.environ.get("ONTOLOGY_TEST_DATABASE_URL")
SCHEMA = "ts_visibility"
NAMES = ("visibility_normal", "visibility_hidden", "visibility_prominent")
RELATION = {"name": "visibility_fixture_link", "source_schema": SCHEMA,
    "source_table": "visibility_prominent", "source_fields": ["id"], "target_schema": SCHEMA,
    "target_table": "visibility_hidden", "target_fields": ["id"], "relationship_kind": "references",
    "cardinality": "many_to_one", "description": "Isolated fixture", "provenance": "isolated test"}


@unittest.skipUnless(URL, "Requires an isolated ontology_regression PostgreSQL instance")
class OntologyVisibilityIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pg = PostgresClient(URL)
        await self.pg.connect(max_retries=1)
        if (await self.pg.fetch_one("SELECT current_database() AS name"))["name"] != "ontology_regression":
            await self.pg.close()
            raise RuntimeError("Visibility tests refuse to change any database except ontology_regression")
        self.migration = migration_module()
        async with self.pg.session() as session:
            await session.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            await session.execute(text(f"CREATE SCHEMA {SCHEMA}"))
            for table in ("ontology_object_favorites", "ontology_object_properties", "ontology_object_relations", "ontology_objects"):
                await session.execute(text(f"DROP TABLE IF EXISTS public.{table} CASCADE"))
            for statement in (*ontology.ONTOLOGY_DDL_STATEMENTS, *OBJECT_RELATIONS_DDL_STATEMENTS):
                await session.execute(text(statement))
            for table in NAMES:
                extra = f", owner_id BIGINT REFERENCES {SCHEMA}.visibility_hidden(id)" if table.endswith("prominent") else ""
                await session.execute(text(f"CREATE TABLE {SCHEMA}.{table} (id BIGINT PRIMARY KEY{extra})"))
                await ontology.sync_object_metadata(session, SCHEMA, table)
            await session.execute(text(f"INSERT INTO {SCHEMA}.visibility_normal VALUES (42)"))
            # Reproduce the deployed public catalog before this migration, without revisiting ddlregistry.
            await session.execute(text("ALTER TABLE public.ontology_objects DROP COLUMN visibility"))
            await session.execute(text("DROP TABLE public.ontology_object_favorites"))
        await manage_object_relations(self.pg, [RELATION], apply=True)
        self.layers = object.__new__(EtlLayers)
        self.layers._pg = self.pg
        self.workspace = object.__new__(EtlWorkspace)
        self.workspace._pg, self.workspace._layers = self.pg, self.layers

    async def asyncTearDown(self):
        await self.pg.close()

    async def snapshot(self):
        return {name: await self.pg.fetch_all(f"SELECT * FROM public.{name} ORDER BY 1,2")
                for name in ("ontology_objects", "ontology_object_properties", "ontology_object_relations")}

    async def test_migration_only_adds_normal_default_and_preserves_existing_metadata(self):
        before = await self.snapshot()
        check = await self.migration.migrate(self.pg)
        self.assertFalse(check["ready"])
        self.assertEqual(await self.snapshot(), before)
        result = await self.migration.migrate(self.pg, apply=True)
        self.assertTrue(result["ready"])
        after = await self.snapshot()
        for row in after["ontology_objects"]:
            self.assertEqual(row.pop("visibility"), "normal")
        self.assertEqual(after, before)
        repeat = await self.migration.migrate(self.pg, apply=True)
        self.assertEqual(repeat["changes"], [])
        self.assertTrue((await self.migration.migrate(self.pg))["ready"])
        self.assertEqual(await self.pg.fetch_all(f"SELECT * FROM {SCHEMA}.visibility_normal"), [{"id": 42}])

    async def test_save_audit_sync_and_registry_preserve_settings_without_touching_business_rows(self):
        await self.migration.migrate(self.pg, apply=True)
        before = await self.snapshot()
        for visibility in ("prominent", "hidden", "normal", "hidden"):
            result = await self.workspace.set_object_visibility("visibility", "visibility_normal", visibility, "admin:42")
            self.assertEqual(result["visibility"], visibility)
            row = await self.pg.fetch_one("""SELECT visibility, updated_by, updated_at FROM public.ontology_objects
                WHERE schema_name=:schema AND table_name='visibility_normal'""", {"schema": SCHEMA})
            self.assertEqual((row["visibility"], row["updated_by"]), (visibility, "admin:42"))
            self.assertIsNotNone(row["updated_at"])
        after = await self.snapshot()
        self.assertEqual(after["ontology_object_properties"], before["ontology_object_properties"])
        self.assertEqual(after["ontology_object_relations"], before["ontology_object_relations"])
        for snapshot in (before, after):
            for row in snapshot["ontology_objects"]:
                for field in ("visibility", "updated_by", "updated_at"):
                    row.pop(field)
        self.assertEqual(after, before)
        async with self.pg.session() as session:
            await ontology.sync_object_metadata(session, SCHEMA, "visibility_normal")
        await registry.save_registered_ddl(self.pg, layer="visibility", table="normal",
            ddl_sql=f"CREATE TABLE {SCHEMA}.visibility_normal (id BIGINT PRIMARY KEY)")
        row = await self.pg.fetch_one("SELECT visibility FROM public.ontology_objects WHERE table_name='visibility_normal'")
        self.assertEqual(row["visibility"], "hidden")
        with self.assertRaises(DBAPIError):
            async with self.pg.session() as session:
                await session.execute(text("UPDATE public.ontology_objects SET visibility='invalid'"))
        self.assertEqual(await self.pg.fetch_all(f"SELECT * FROM {SCHEMA}.visibility_normal"), [{"id": 42}])
        async with self.pg.session() as session:
            await session.execute(text(f"CREATE TABLE {SCHEMA}.visibility_new (id BIGINT)"))
            await ontology.sync_object_metadata(session, SCHEMA, "visibility_new")
        self.assertEqual((await self.pg.fetch_one("SELECT visibility FROM public.ontology_objects WHERE table_name='visibility_new'"))["visibility"], "normal")

    async def test_hidden_ids_facets_pagination_sort_and_logical_link_preview(self):
        await self.migration.migrate(self.pg, apply=True)
        await self.workspace.set_object_visibility("visibility", "visibility_hidden", "hidden", "admin:42")
        await self.workspace.set_object_visibility("visibility", "visibility_prominent", "prominent", "admin:42")
        result = await self.layers.object_types(schema="visibility", search=SCHEMA + ".", limit=1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["items"][0]["table"], "visibility_prominent")
        self.assertEqual(result["schemaCounts"], {SCHEMA: 2})
        self.assertEqual(result["groupCounts"]["other"], 2)
        self.assertEqual(result["groupSchemaCounts"]["other"], {SCHEMA: 2})
        page = await self.layers.object_types(schema="visibility", search=SCHEMA + ".", limit=1, offset=1)
        self.assertEqual(page["items"][0]["table"], "visibility_normal")
        hidden_id = SCHEMA + ".visibility_hidden"
        favorites = await self.layers.object_types(ids=hidden_id)
        self.assertEqual((favorites["items"], favorites["total"], favorites["schemaCounts"]), ([], 0, {}))
        self.assertTrue(all(not counts for counts in favorites["groupSchemaCounts"].values()))
        included = await self.layers.object_types(ids=hidden_id, include_hidden=True)
        self.assertEqual(included["items"][0]["visibility"], "hidden")
        all_items = await self.layers.object_types(schema="visibility", search=SCHEMA + ".", include_hidden=True)
        self.assertEqual([row["table"] for row in all_items["items"]],
                         ["visibility_prominent", "visibility_hidden", "visibility_normal"])
        with self.assertRaises(HTTPException) as raised:
            await self.layers.preview("visibility", "visibility_hidden")
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual((await self.layers.preview("visibility", "visibility_hidden", include_hidden=True))["visibility"], "hidden")
        self.assertEqual((await self.layers.preview("visibility", "visibility_prominent"))["linkedObjectTypes"], [])
        preview = await self.layers.preview("visibility", "visibility_prominent", include_hidden=True)
        self.assertEqual(len(preview["linkedObjectTypes"]), 1)
        linked = preview["linkedObjectTypes"][0]
        self.assertEqual((linked["id"], linked["visibility"]), (hidden_id, "hidden"))
        self.assertEqual(linked["constraints"], [])
        self.assertEqual([(rel["name"], rel["sourceKind"]) for rel in linked["relationships"]],
                         [(RELATION["name"], "logical")])
        async with self.pg.session() as session:
            await session.execute(text(f"CREATE TABLE {SCHEMA}.partitioned (id BIGINT) PARTITION BY HASH(id)"))
            await session.execute(text(f"CREATE TABLE {SCHEMA}.partition_child PARTITION OF {SCHEMA}.partitioned FOR VALUES WITH (MODULUS 1, REMAINDER 0)"))
        for table in ("missing", "partition_child"):
            with self.assertRaises(HTTPException) as raised:
                await self.workspace.set_object_visibility("visibility", table, "hidden", "admin")
            self.assertEqual(raised.exception.status_code, 404)

    async def test_two_users_favorites_persist_hidden_import_union_and_cascade_on_metadata_delete(self):
        from app.web.tests.test_ontology_favorites import USER_A, USER_B
        await self.migration.migrate(self.pg, apply=True)
        for _ in range(2):
            await self.workspace.set_object_favorite("visibility", "visibility_hidden", True, USER_A)
        await self.workspace.set_object_favorite("visibility", "visibility_normal", True, USER_B)
        await self.workspace.set_object_visibility("visibility", "visibility_hidden", "hidden", "admin")
        self.assertEqual(await self.workspace.object_favorites(USER_A), {"ids": [SCHEMA + ".visibility_hidden"]})
        self.assertEqual(await self.workspace.object_favorites(USER_B), {"ids": [SCHEMA + ".visibility_normal"]})
        identifiers = [SCHEMA + "." + name for name in NAMES] + [SCHEMA + ".deleted"]
        imported = await self.workspace.import_object_favorites(identifiers, USER_A)
        self.assertEqual((imported["imported"], imported["skipped"]), (2, 1))
        self.assertEqual(len(imported["ids"]), 3)
        second = await self.layers.object_types(schema=SCHEMA, favorite_user_id=USER_A, limit=1, offset=1)
        self.assertEqual(second["total"], 2)
        self.assertEqual([row["table"] for row in second["items"]], ["visibility_prominent"])
        other = await self.layers.object_types(schema=SCHEMA, favorite_user_id=USER_B, include_hidden=True)
        self.assertEqual([row["table"] for row in other["items"]], ["visibility_normal"])
        self.assertEqual((await self.workspace.import_object_favorites(identifiers, USER_A))["imported"], 0)
        another_pg = PostgresClient(URL)
        try:
            await another_pg.connect(max_retries=1)
            restarted = object.__new__(EtlWorkspace)
            restarted._pg = another_pg
            self.assertEqual(await restarted.object_favorites(USER_A), {"ids": imported["ids"]})
        finally:
            await another_pg.close()
        for _ in range(2):
            await self.workspace.set_object_favorite("visibility", "visibility_hidden", False, USER_A)
        self.assertEqual(len((await self.workspace.object_favorites(USER_A))["ids"]), 2)
        self.assertEqual(await self.workspace.object_favorites(USER_B), {"ids": [SCHEMA + ".visibility_normal"]})
        async with self.pg.session() as session:
            await session.execute(text("DELETE FROM public.ontology_objects WHERE table_name='visibility_prominent'"))
        self.assertEqual(await self.workspace.object_favorites(USER_A), {"ids": [SCHEMA + ".visibility_normal"]})
        app = FastAPI()
        app.include_router(etl.router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=USER_A, is_superuser=False)
        path = f"/api/v1/etl/{SCHEMA}/visibility_hidden/favorite"
        with patch.object(etl, "get_etl_workspace", return_value=self.workspace):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                self.assertEqual((await client.put(path, json={"user_id": USER_B})).status_code, 422)
                self.assertEqual((await client.request("DELETE", path, json={"user_id": USER_B})).status_code, 422)
                self.assertEqual((await client.post("/api/v1/etl/object-types/favorites/import",
                    json={"ids": [], "user_id": USER_B})).status_code, 422)
                self.assertEqual((await client.put(path + "?user_id=" + USER_B)).status_code, 200)
        self.assertEqual(await self.workspace.object_favorites(USER_B), {"ids": [SCHEMA + ".visibility_normal"]})
        self.assertIn(SCHEMA + ".visibility_hidden", (await self.workspace.object_favorites(USER_A))["ids"])

    async def test_conflicting_favorites_table_refuses_migration_without_modifying_existing_objects(self):
        async with self.pg.session() as session:
            await session.execute(text("CREATE TABLE public.ontology_object_favorites (user_id TEXT, object_id BIGINT)"))
        before = await self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "favorites definition differs"):
            await self.migration.migrate(self.pg, apply=True)
        self.assertEqual(await self.snapshot(), before)
        self.assertIsNone((await self.pg.fetch_one("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='ontology_objects' AND column_name='visibility'")))
