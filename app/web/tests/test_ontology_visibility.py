"""Object visibility contracts using metadata fixtures, without database or network access."""

from contextlib import asynccontextmanager
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.etl.schema import ontology, registry
from app.web.api.dependencies.common import get_current_user
from app.web.api.routes import etl
from app.web.services.etl_workspace import EtlWorkspace
from app.web.tests.test_etl_object_types import Result, foreign, service, summary
from app.web.tests.test_ontology_metadata import MetadataPg


def migration_module():
    path = Path(__file__).resolve().parents[3] / "tools/ontology_visibility.py"
    spec = importlib.util.spec_from_file_location("ontology_visibility_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


READY = {"type": "text", "not_null": True, "default_expression": "'normal'::text",
         "constraint_definition": "CHECK ((visibility = ANY (ARRAY['normal'::text, 'prominent'::text, 'hidden'::text])))",
         "validated": True}


class MigrationPg:
    def __init__(self, installed=True, column=None):
        self.installed, self.column, self.calls = installed, column, []
        self.favorites = False
        self.favorite_definition = dict.fromkeys(("ordinary_table", "user_id", "object_id", "created_at", "primary_key", "object_foreign_key"), True)

    @asynccontextmanager
    async def session(self):
        before = deepcopy(self.column)
        try:
            yield self
        except Exception:
            self.column = before
            raise

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.calls.append(sql)
        if sql.startswith("SELECT to_regclass"):
            return Result(scalar=self.favorites if "ontology_object_favorites" in sql else self.installed)
        if sql.startswith("SELECT format_type"):
            return Result([self.column] if self.column else [])
        if sql.startswith("SELECT c.relkind"):
            return Result([self.favorite_definition] if self.favorites else [])
        if sql.startswith("ALTER TABLE public.ontology_objects ADD COLUMN visibility"):
            self.column = deepcopy(READY)
        elif sql.startswith("CREATE TABLE IF NOT EXISTS public.ontology_object_favorites"):
            self.favorites = True
        elif not sql.startswith(("SELECT pg_advisory_xact_lock", "SET ")):
            raise AssertionError("Unexpected visibility migration SQL: " + sql)
        return Result()


class VisibilityPg(MetadataPg):
    async def connect(self, **kwargs):
        pass

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        if sql.startswith("SET LOCAL"):
            return Result()
        if sql.startswith("SELECT 1 FROM pg_namespace"):
            return Result(scalar=1 if any(key[0] == params["schema"] for key in self.catalog) else None)
        if sql.startswith("SELECT to_regclass"):
            return Result(scalar=True)
        if sql.startswith("UPDATE public.ontology_objects SET visibility"):
            self.calls.append((sql, deepcopy(params)))
            for value in self.objects.values():
                if value["id"] == params["id"]:
                    value.update(visibility=params["visibility"], updated_by=params["actor"])
                    if "is_active = TRUE" in sql:
                        value["is_active"] = True
            return Result()
        if sql.startswith("UPDATE public.ontology_objects SET is_active = TRUE"):
            for value in self.objects.values():
                if value["id"] == params["id"]:
                    value["is_active"] = True
            return Result()
        result = await super().execute(statement, params)
        if sql.startswith("INSERT INTO ontology_objects"):
            for value in self.objects.values():
                value.setdefault("visibility", "normal")
        return result


class VisibilityMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_is_read_only_apply_is_idempotent_and_does_not_revisit_legacy_registry(self):
        module, pg = migration_module(), MigrationPg()
        check = await module.migrate(pg)
        self.assertFalse(check["ready"])
        self.assertIsNone(pg.column)
        self.assertTrue(all(sql.startswith(("SELECT", "SET TRANSACTION READ ONLY")) for sql in pg.calls))
        first = await module.migrate(pg, apply=True)
        self.assertTrue(first["ready"])
        self.assertEqual(first["changes"], ["add_visibility", "create_favorites"])
        second = await module.migrate(pg, apply=True)
        self.assertEqual(second["changes"], [])
        self.assertEqual(sum(sql.startswith("ALTER TABLE") for sql in pg.calls), 1)
        self.assertEqual(sum(sql.startswith("CREATE TABLE") for sql in pg.calls), 1)
        self.assertTrue(all("ddlregistry" not in sql and not sql.startswith(("INSERT", "UPDATE")) for sql in pg.calls))
        self.assertIn("visibility TEXT NOT NULL DEFAULT 'normal'", ontology.ONTOLOGY_OBJECTS_DDL)
        self.assertIn("ontology_objects_visibility_check", ontology.ONTOLOGY_OBJECTS_DDL)

    async def test_missing_catalog_and_conflicting_column_fail_without_mutation(self):
        module = migration_module()
        for pg in (MigrationPg(installed=False), MigrationPg(column={**READY, "type": "integer"}),
                   MigrationPg(column={**READY, "validated": False})):
            with self.subTest(column=pg.column):
                before = deepcopy(pg.column)
                with self.assertRaises(RuntimeError):
                    await module.migrate(pg, apply=True)
                self.assertEqual(pg.column, before)
                self.assertFalse(any(sql.startswith("ALTER") for sql in pg.calls))

    async def test_conflicting_favorites_table_is_rejected_without_changing_visibility_or_data(self):
        module, pg = migration_module(), MigrationPg()
        pg.favorites = True
        pg.favorite_definition["object_foreign_key"] = False
        for apply in (False, True):
            with self.assertRaisesRegex(RuntimeError, "favorites definition differs"):
                await module.migrate(pg, apply=apply)
        self.assertIsNone(pg.column)
        self.assertFalse(any(sql.startswith(("ALTER", "CREATE", "INSERT", "UPDATE", "DELETE")) for sql in pg.calls))


class VisibilityWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def workspace(self):
        instance = object.__new__(EtlWorkspace)
        instance._pg = VisibilityPg(table="custom_items")
        return instance

    async def test_save_all_states_and_metadata_sync_registration_preserve_visibility(self):
        instance = self.workspace()
        pg = instance._pg
        original_catalog = deepcopy(pg.catalog)
        for visibility in ("normal", "prominent", "hidden", "normal"):
            result = await instance.set_object_visibility("custom", "custom_items", visibility, "user:42")
            self.assertEqual(result, {"schema": "ts_custom", "table": "custom_items", "visibility": visibility})
            actual = pg.objects[("ts_custom", "custom_items")]
            self.assertEqual((actual["visibility"], actual["updated_by"]), (visibility, "user:42"))
        await instance.set_object_visibility("custom", "custom_items", "hidden", "user:42")
        async with pg.session() as session:
            await ontology.sync_object_metadata(session, "ts_custom", "custom_items", catalog_session=session)
        await registry.save_registered_ddl(pg, layer="custom", table="items", ddl_sql="CREATE TABLE ts_custom.custom_items (id BIGINT)")
        self.assertEqual(pg.objects[("ts_custom", "custom_items")]["visibility"], "hidden")
        self.assertEqual(pg.catalog, original_catalog)
        self.assertFalse(any(sql.startswith(("CREATE", "ALTER", "DROP")) for sql, _ in pg.calls))
        update = next(sql for sql, _ in pg.calls if sql.startswith("UPDATE public.ontology_objects SET visibility"))
        self.assertIn("updated_at = now()", update)

    async def test_reject_invalid_internal_missing_and_partition_objects(self):
        instance = self.workspace()
        for schema, table, value, status in (("ts_meta", "items", "normal", 404),
                ("bad;schema", "items", "normal", 400), ("custom", "bad;table", "normal", 400),
                ("custom", "custom_items", "unknown", 422), ("custom", "missing", "normal", 404),
                ("missing", "items", "normal", 404)):
            with self.subTest(schema=schema, table=table, value=value):
                with self.assertRaises(HTTPException) as raised:
                    await instance.set_object_visibility(schema, table, value, "admin")
                self.assertEqual(raised.exception.status_code, status)
        self.assertFalse(instance._pg.objects)

    async def test_registered_changes_only_touch_visibility_audit_and_inactive_objects_are_activated(self):
        instance = self.workspace()
        pg = instance._pg
        async with pg.session() as session:
            await ontology.sync_object_metadata(
                session, "ts_custom", "custom_items", catalog_session=session, provenance="preserve:original",
            )
        before = deepcopy((pg.objects, pg.properties))
        with patch("app.web.services.etl_workspace.sync_object_metadata", new=AsyncMock()) as sync:
            await instance.set_object_visibility("custom", "custom_items", "hidden", "admin")
        sync.assert_not_awaited()
        actual = deepcopy(pg.objects)
        actual[("ts_custom", "custom_items")].pop("visibility")
        actual[("ts_custom", "custom_items")].pop("updated_by")
        expected = deepcopy(before[0])
        expected[("ts_custom", "custom_items")].pop("visibility")
        self.assertEqual(actual, expected)
        self.assertEqual(pg.properties, before[1])
        pg.objects[("ts_custom", "custom_items")]["is_active"] = False
        await instance.set_object_visibility("custom", "custom_items", "prominent", "admin")
        self.assertTrue(pg.objects[("ts_custom", "custom_items")]["is_active"])
        self.assertEqual(pg.objects[("ts_custom", "custom_items")]["visibility"], "prominent")


class VisibilityReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_ids_and_all_facets_share_hidden_filter_and_stable_prominent_sort(self):
        instance, session = service(Result([]))
        await instance.object_types(schema="ods", search="station", group="ingestion", ids="ts_ods.hidden")
        count_query = next(sql for sql, _ in session.calls if "count(*) AS count" in sql)
        self.assertIn("COALESCE(o.visibility, 'normal') <> 'hidden'", count_query)
        self.assertIn("ANY(CAST(:ids AS text[]))", count_query)
        instance, session = service(Result([{"schema": "ts_ods", "resource_group": "ingestion", "count": 1}]),
                                    Result([summary(visibility="prominent")]))
        result = await instance.object_types(include_hidden=True)
        self.assertEqual(result["items"][0]["visibility"], "prominent")
        self.assertTrue(all("<> 'hidden'" not in sql for sql, _ in session.calls))
        self.assertIn("ORDER BY CASE WHEN COALESCE(o.visibility, 'normal') = 'prominent' THEN 0 ELSE 1 END", session.calls[-1][0])

    async def test_preview_shows_hidden_logical_neighbor_only_when_requested(self):
        from app.web.tests.test_etl_object_relations import logical, SEEDS
        logical_hidden = {**logical(SEEDS[2]), "source_table": "objects", "target_visibility": "hidden"}
        hidden = foreign(target_schema="ts_custom", target_table="secret", target_visibility="hidden")
        for include_hidden in (False, True):
            instance, _ = service(Result([summary(properties_count=0)]), Result([hidden]), Result(scalar=True), Result([logical_hidden]))
            preview = await instance.preview("ods", "objects", include_hidden=include_hidden)
            linked = preview["linkedObjectTypes"]
            if include_hidden:
                peer_id = f"{logical_hidden['target_schema']}.{logical_hidden['target_table']}"
                self.assertEqual([(peer["id"], peer["visibility"]) for peer in linked], [(peer_id, "hidden")])
                self.assertEqual(linked[0]["constraints"], [])
                self.assertEqual([(rel["name"], rel["sourceKind"]) for rel in linked[0]["relationships"]],
                                 [(logical_hidden["name"], "logical")])
            else:
                self.assertEqual(linked, [])
        instance, session = service(Result([]))
        with self.assertRaises(HTTPException) as raised:
            await instance.preview("ods", "hidden")
        self.assertEqual(raised.exception.status_code, 404)
        self.assertIn("<> 'hidden'", session.calls[-1][0])


class VisibilityRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(etl.router, prefix="/api/v1")
        self.user = SimpleNamespace(id="user:42", is_superuser=True)
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(self.app)

    def test_put_requires_superuser_valid_payload_and_uses_authenticated_actor(self):
        workspace = SimpleNamespace(set_object_visibility=AsyncMock(return_value={"visibility": "hidden"}))
        path = "/api/v1/etl/ts_custom/items/visibility"
        with patch.object(etl, "get_etl_workspace", return_value=workspace):
            self.assertEqual(self.client.put(path, json={"visibility": "hidden"}).status_code, 200)
            workspace.set_object_visibility.assert_awaited_once_with("ts_custom", "items", "hidden", "user:42")
            for payload in ({}, {"visibility": "unknown"}, {"visibility": None}, {"visibility": "normal", "actor": "fake"}):
                self.assertEqual(self.client.put(path, json=payload).status_code, 422)
            self.user.is_superuser = False
            self.assertEqual(self.client.put(path, json={"visibility": "normal"}).status_code, 403)
        def reject():
            raise HTTPException(401, "Authentication required")
        self.app.dependency_overrides[get_current_user] = reject
        self.assertEqual(self.client.put(path, json={"visibility": "normal"}).status_code, 401)

    def test_include_hidden_is_camel_case_and_admin_only_for_both_read_routes(self):
        layers = SimpleNamespace(object_types=AsyncMock(return_value={"items": []}), preview=AsyncMock(return_value={"visibility": "hidden"}))
        with patch.object(etl, "get_data_connections", return_value=SimpleNamespace(warehouse=lambda _: layers)):
            self.assertEqual(self.client.get("/api/v1/etl/object-types?includeHidden=true").status_code, 200)
            layers.object_types.assert_awaited_once_with(None, "", "", 100, 0, None, include_hidden=True, favorite_user_id=None)
            self.assertEqual(self.client.get("/api/v1/etl/object-types?favoritesOnly=true").status_code, 200)
            layers.object_types.assert_awaited_with(None, "", "", 100, 0, None, include_hidden=False, favorite_user_id=self.user.id)
            self.assertEqual(self.client.get("/api/v1/etl/data-connections/warehouse/object-types/custom/items/preview?includeHidden=true").status_code, 200)
            layers.preview.assert_awaited_once_with("custom", "items", include_hidden=True)
            self.user.is_superuser = False
            for path in ("/api/v1/etl/object-types", "/api/v1/etl/custom/items/preview"):
                self.assertEqual(self.client.get(path + "?includeHidden=true").status_code, 403)
                self.assertEqual(self.client.get(path + "?includeHidden=false").status_code, 200)
                self.assertEqual(self.client.get(path + "?includeHidden=invalid").status_code, 422)
