"""Real HTTP requests against the disposable ontology_creation_regression database."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app.web.api.dependencies.common import get_current_user
from app.web.api.routes import etl
from app.web.services import explorer_rows
from app.web.tests import test_ontology_creation_integration as creation


@unittest.skipUnless(creation.URL, "Requires the disposable ontology_creation_regression database")
class ExplorerRecordLinks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await creation.OntologyCreationIntegration.asyncSetUp(self)
        self.addAsyncCleanup(self.pg.close)
        for sql in (
            "CREATE TABLE ts_creation.parents (tenant text, id bigint, amount numeric, PRIMARY KEY(tenant,id))",
            """CREATE TABLE ts_creation.children (
                id int PRIMARY KEY, tenant text, parent_id bigint,
                CONSTRAINT child_parent FOREIGN KEY(tenant,parent_id) REFERENCES ts_creation.parents(tenant,id))""",
            "CREATE TABLE ts_creation.standalone (id int PRIMARY KEY, tenant text, parent_id bigint)",
            "INSERT INTO ts_creation.parents VALUES ('a',9007199254740993,3.141592653589793238),('b',9007199254740993,1)",
            "INSERT INTO ts_creation.children SELECT i,'a',9007199254740993 FROM generate_series(1,12) i",
            "INSERT INTO ts_creation.children VALUES (13,'b',9007199254740993),(14,NULL,NULL)",
            "INSERT INTO ts_creation.standalone VALUES (1,'a',9007199254740993)",
        ):
            await self.pg.execute(sql)
        app = FastAPI()
        app.include_router(etl.router)
        self.actor = SimpleNamespace(is_superuser=False)
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.patch = patch.object(explorer_rows, "get_etl_layers", return_value=self.layers)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://qa")
        self.addAsyncCleanup(self.client.aclose)
        self.parent = {"tenant": "a", "id": "9007199254740993"}

    async def post(self, table, action, key, **params):
        return await self.client.post(f"/etl/ts_creation/{table}/{action}", json={"key": key, **params})

    async def test_exact_rows_both_directions_and_composite_identity(self):
        details = await self.post("parents", "row-by-key", self.parent)
        self.assertEqual(details.status_code, 200, details.text)
        item = details.json()
        self.assertEqual(item["identity"], self.parent)
        self.assertEqual(item["row"]["id"], self.parent["id"])
        self.assertEqual(item["row"]["amount"], "3.141592653589793238")
        self.assertNotIn("rowVersion", item)
        self.actor.is_superuser = True
        admin = (await self.post("parents", "row-by-key", self.parent)).json()
        self.assertEqual(len(admin["rowVersion"]), 64)
        self.assertTrue(admin["rowActions"]["edit"])
        self.assertEqual((await self.post("parents", "row-by-key", {"tenant":"a"})).status_code, 422)
        self.assertEqual((await self.post("parents", "row-by-key", {"tenant":"c","id":"9007199254740993"})).status_code, 404)
        page = (await self.post("parents", "row-links", self.parent)).json()
        self.assertEqual([(r["name"], r["direction"]) for r in page["relations"]], [("child_parent","incoming")])
        self.assertEqual(page["identities"], [{"id":str(i)} for i in range(1,11)])
        self.assertTrue(page["hasMore"])
        next_page = (await self.post("parents", "row-links", self.parent, relation=page["selected"], offset=10)).json()
        self.assertEqual(next_page["identities"], [{"id":"11"},{"id":"12"}])
        self.assertFalse(next_page["hasMore"])
        parent_link = (await self.post("children", "row-links", {"id":"12"})).json()
        self.assertEqual(parent_link["identities"], [self.parent])
        self.assertEqual((await self.post("children","row-links",{"id":"14"})).json()["identities"], [])
        independent = (await self.post("standalone","row-links",{"id":"1"})).json()
        self.assertEqual(independent["relations"], [])
        await self.pg.execute("CREATE TABLE ts_creation.codes (code char(4) PRIMARY KEY)")
        await self.pg.execute("INSERT INTO ts_creation.codes VALUES ('ABCD')")
        self.assertEqual((await self.post("codes", "row-by-key", {"code":"ABCD"})).status_code,200)
        self.assertEqual((await self.post("codes", "row-by-key", {"code":"ABCDE"})).status_code,404)
        await self.pg.execute("CREATE TABLE ts_creation.legacy_child () INHERITS (ts_creation.codes)")
        self.assertEqual((await self.post("codes", "row-by-key", {"code":"ABCD"})).status_code,404)
        with patch.object(explorer_rows, "_required_indexed_preview_tables", return_value={("ts_creation","parents")}):
            self.assertEqual((await self.post("parents", "row-by-key", self.parent)).status_code, 404)
            self.assertEqual((await self.post("children","row-links",{"id":"12"},relation=parent_link["selected"])).status_code,404)

    async def test_hidden_disabled_and_large_unindexed_targets_do_not_leak(self):
        await self.pg.execute("""INSERT INTO public.ontology_object_relations
             (name,source_schema,source_table,source_fields,target_schema,target_table,target_fields,
              relationship_kind,cardinality,provenance)
             VALUES ('declared_asset','ts_creation','standalone',ARRAY['tenant','parent_id'],
                 'ts_creation','parents',ARRAY['tenant','id'],'parent','many_to_one','test contract')""")
        observed = (await self.post("standalone","row-links",{"id":"1"})).json()
        self.assertEqual(observed["identities"], [self.parent])
        self.assertEqual(observed["relations"][0]["kind"], "logical")
        await self.pg.execute("UPDATE public.ontology_object_relations SET enabled=false WHERE name='declared_asset'")
        self.assertEqual((await self.post("standalone","row-links",{"id":"1"},relation=observed["selected"])).status_code,404)
        await self.pg.execute("""INSERT INTO public.ontology_objects(layer,schema_name,table_name,visibility)
                              VALUES ('creation','ts_creation','parents','hidden')""")
        self.assertEqual((await self.post("children","row-links",{"id":"1"})).json()["relations"], [])
        self.assertEqual((await self.post("parents","row-by-key",self.parent)).status_code,404)
        await self.pg.execute("UPDATE public.ontology_objects SET visibility='normal' WHERE schema_name='ts_creation' AND table_name='parents'")
        await self.pg.execute("INSERT INTO ts_creation.children SELECT i,'b',9007199254740993 FROM generate_series(100,35000) i")
        await self.pg.execute("ANALYZE ts_creation.children")
        blocked = (await self.post("parents","row-links",self.parent)).json()
        self.assertIn("ordered index",blocked["reason"])
        self.assertEqual(blocked["identities"], [])
        await self.pg.execute("CREATE INDEX child_relation_order ON ts_creation.children(tenant,parent_id,id)")
        found = (await self.post("parents","row-links",self.parent)).json()
        self.assertNotIn("reason",found)
        self.assertEqual(len(found["identities"]),10)
