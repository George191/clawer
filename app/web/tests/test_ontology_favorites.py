"""Account-isolated, persisted favorite memberships using local metadata fixtures."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.etl.schema.ontology import ONTOLOGY_FAVORITES_DDL
from app.web.api.dependencies.common import get_current_user
from app.web.api.routes import etl
from app.web.services.etl_workspace import EtlWorkspace
from app.web.tests.test_etl_object_types import Result
from app.web.tests.test_ontology_visibility import VisibilityPg


USER_A = "10000000-0000-0000-0000-000000000001"
USER_B = "20000000-0000-0000-0000-000000000002"


class FavoritePg(VisibilityPg):
    def __init__(self):
        super().__init__(table="custom_items")
        self.catalog[("ts_custom", "second")] = {**self.catalog[("ts_custom", "custom_items")], "oid": 101}
        self.columns[101] = []
        self.favorites = {}

    @asynccontextmanager
    async def session(self):
        before = deepcopy(self.favorites)
        try:
            async with super().session() as session:
                yield session
        except Exception:
            self.favorites = before
            raise

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        if sql.startswith("SELECT o.schema_name ||"):
            selected = {object_id for (user_id, object_id) in self.favorites if user_id == params["user_id"]}
            return Result([{"id": f"{schema}.{table}"} for (schema, table), value in sorted(self.objects.items())
                           if value["id"] in selected])
        if sql.startswith("INSERT INTO public.ontology_object_favorites"):
            ids = params.get("object_ids", [params.get("object_id")])
            inserted = []
            for object_id in ids:
                key = (params["user_id"], object_id)
                if key not in self.favorites:
                    self.favorites[key] = "original-created-at"
                    inserted.append({"object_id": object_id})
            return Result(inserted)
        if sql.startswith("DELETE FROM public.ontology_object_favorites"):
            row = self.objects.get((params["schema"], params["table"]))
            if row:
                self.favorites.pop((params["user_id"], row["id"]), None)
            return Result()
        return await super().execute(statement, params)


def workspace(pg=None):
    instance = object.__new__(EtlWorkspace)
    instance._pg = pg or FavoritePg()
    return instance


class FavoriteServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_membership_is_account_scoped_idempotent_and_survives_service_restart_and_hidden_state(self):
        instance = workspace()
        self.assertEqual(await instance.object_favorites(USER_A), {"ids": []})
        for _ in range(2):
            self.assertEqual(await instance.set_object_favorite("custom", "custom_items", True, USER_A),
                             {"id": "ts_custom.custom_items", "favorite": True})
        self.assertEqual(len(instance._pg.favorites), 1)
        await instance.set_object_visibility("custom", "custom_items", "hidden", "admin")
        restarted = workspace(instance._pg)
        self.assertEqual(await restarted.object_favorites(USER_A), {"ids": ["ts_custom.custom_items"]})
        self.assertEqual(await restarted.object_favorites(USER_B), {"ids": []})
        await instance.set_object_favorite("custom", "custom_items", True, USER_B)
        for _ in range(2):
            await instance.set_object_favorite("custom", "custom_items", False, USER_A)
        self.assertEqual(await instance.object_favorites(USER_A), {"ids": []})
        self.assertEqual(await instance.object_favorites(USER_B), {"ids": ["ts_custom.custom_items"]})
        self.assertEqual(await instance.set_object_favorite("custom", "deleted", False, USER_A),
                         {"id": "ts_custom.deleted", "favorite": False})

    async def test_legacy_import_unions_existing_devices_skips_deleted_and_never_changes_other_accounts(self):
        instance = workspace()
        await instance.set_object_favorite("custom", "custom_items", True, USER_A)
        await instance.set_object_favorite("custom", "second", True, USER_B)
        ids = ["ts_custom.second", "ts_custom.missing", "ts_custom.second"]
        first = await instance.import_object_favorites(ids, USER_A)
        self.assertEqual(first, {"ids": ["ts_custom.custom_items", "ts_custom.second"], "imported": 1, "skipped": 1})
        self.assertEqual((await instance.import_object_favorites(ids, USER_A))["imported"], 0)
        self.assertEqual(await instance.object_favorites(USER_B), {"ids": ["ts_custom.second"]})
        self.assertEqual((await instance.import_object_favorites([], USER_A))["ids"], first["ids"])
        for invalid in (["ts_custom.second", "ts_meta.secret"], ["bad.id"], ["ts_custom.bad;sql"], ["ts_custom.second"] * 201):
            before = deepcopy(instance._pg.favorites)
            with self.assertRaises(HTTPException) as raised:
                await instance.import_object_favorites(invalid, USER_A)
            self.assertEqual(raised.exception.status_code, 422)
            self.assertEqual(instance._pg.favorites, before)
        with self.assertRaises(HTTPException) as raised:
            await instance.set_object_favorite("custom", "missing", True, USER_A)
        self.assertEqual(raised.exception.status_code, 404)
        self.assertIn("PRIMARY KEY (user_id, object_id)", ONTOLOGY_FAVORITES_DDL)
        self.assertIn("REFERENCES public.ontology_objects(id) ON DELETE CASCADE", ONTOLOGY_FAVORITES_DDL)
        self.assertNotIn("REFERENCES public.user", ONTOLOGY_FAVORITES_DDL)


class FavoriteRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(etl.router, prefix="/api/v1")
        self.user = SimpleNamespace(id=USER_A, is_superuser=False)
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(self.app)

    def test_ordinary_users_can_mutate_only_their_membership_and_cannot_forge_actor(self):
        instance = SimpleNamespace(object_favorites=AsyncMock(return_value={"ids": []}),
            set_object_favorite=AsyncMock(return_value={"id": "ts_custom.items", "favorite": True}),
            import_object_favorites=AsyncMock(return_value={"ids": [], "imported": 0, "skipped": 0}))
        with patch.object(etl, "get_etl_workspace", return_value=instance):
            self.assertEqual(self.client.get("/api/v1/etl/object-types/favorites").status_code, 200)
            instance.object_favorites.assert_awaited_once_with(USER_A)
            path = "/api/v1/etl/custom/items/favorite"
            self.assertEqual(self.client.put(path).status_code, 200)
            instance.set_object_favorite.assert_awaited_with("custom", "items", True, USER_A)
            self.assertEqual(self.client.delete(path).status_code, 200)
            instance.set_object_favorite.assert_awaited_with("custom", "items", False, USER_A)
            self.assertEqual(self.client.put(path, json={"user_id": USER_B}).status_code, 422)
            self.assertEqual(self.client.request("DELETE", path, json={"user_id": USER_B}).status_code, 422)
            import_path = "/api/v1/etl/object-types/favorites/import"
            self.assertEqual(self.client.post(import_path, json={"ids": ["ts_custom.items"]}).status_code, 200)
            instance.import_object_favorites.assert_awaited_once_with(["ts_custom.items"], USER_A)
            for payload in ({"ids": [], "user_id": USER_B}, {"ids": ["ts_custom.items"] * 201}, {}):
                self.assertEqual(self.client.post(import_path, json=payload).status_code, 422)
        def reject():
            raise HTTPException(401, "Authentication required")
        self.app.dependency_overrides[get_current_user] = reject
        for method, path in (("get", "/api/v1/etl/object-types/favorites"), ("put", "/api/v1/etl/custom/items/favorite"),
                             ("delete", "/api/v1/etl/custom/items/favorite")):
            self.assertEqual(getattr(self.client, method)(path).status_code, 401)
        self.assertEqual(self.client.post("/api/v1/etl/object-types/favorites/import", json={"ids": []}).status_code, 401)
