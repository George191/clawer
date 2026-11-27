"""Real HTTP requests against the disposable ontology_creation_regression database."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.schema.object_links import canonical_object_key, object_key_hash
from app.web.api.dependencies.common import get_current_user
from app.web.api.routes import etl
from app.web.services import explorer_rows
from app.web.services import ontology_object_links as links
from app.web.services import etl_workspace
from app.web.tests import test_ontology_creation_integration as creation


@unittest.skipUnless(creation.URL, "Requires the disposable ontology_creation_regression database")
class ExplorerRecordLinks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await creation.OntologyCreationIntegration.asyncSetUp(self)
        self.addAsyncCleanup(self.pg.close)
        for sql in (
            "CREATE TABLE ts_creation.parents (tenant text, id bigint, amount numeric, area jsonb, PRIMARY KEY(tenant,id))",
            """CREATE TABLE ts_creation.children (
                id int PRIMARY KEY, tenant text, parent_id bigint,
                CONSTRAINT child_parent FOREIGN KEY(tenant,parent_id) REFERENCES ts_creation.parents(tenant,id))""",
            "CREATE TABLE ts_creation.standalone (id int PRIMARY KEY, tenant text, parent_id bigint)",
            """INSERT INTO ts_creation.parents VALUES
                ('a',9007199254740993,3.141592653589793238,
                    jsonb_build_object('type','Point','coordinates',jsonb_build_array(0.1,0.2),
                        'nested',jsonb_build_object('exact',9007199254740993,'count',1))),
                ('b',9007199254740993,1,NULL)""",
            "INSERT INTO ts_creation.children SELECT i,'a',9007199254740993 FROM generate_series(1,12) i",
            "INSERT INTO ts_creation.children VALUES (13,'b',9007199254740993),(14,NULL,NULL)",
            "INSERT INTO ts_creation.standalone VALUES (1,'a',9007199254740993)",
        ):
            await self.pg.execute(sql)
        app = FastAPI()
        app.include_router(etl.router)
        self.actor = SimpleNamespace(is_superuser=False, id=uuid4())
        app.dependency_overrides[get_current_user] = lambda: self.actor
        for module, name, value in ((explorer_rows, "get_etl_layers", self.layers),
                                    (links, "get_etl_layers", self.layers),
                                    (etl_workspace, "get_etl_workspace", self.workspace),
                                    (etl, "get_etl_workspace", self.workspace)):
            active_patch = patch.object(module, name, return_value=value)
            active_patch.start()
            self.addCleanup(active_patch.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://qa")
        self.addAsyncCleanup(self.client.aclose)
        self.parent = {"tenant": "a", "id": "9007199254740993"}
        await self.declare("child_parent", "children", ['tenant','parent_id'],
                           [( {"id":str(i)}, {**self.parent, "tenant":"b" if i == 13 else "a"}) for i in range(1,14)])

    async def declare(self, name, source, fields, pairs=()):
        async with self.pg.session() as session:
            relation = await session.scalar(text("""INSERT INTO app.helio_object_relations
                (name,source_schema,source_table,source_fields,target_schema,target_table,target_fields,
                 relationship_kind,cardinality,provenance) VALUES (:name,'ts_creation',:source,:fields,
                'ts_creation','parents',ARRAY['tenant','id'],'parent','many_to_one','stored-edge test') RETURNING id"""),
                {"name":name,"source":source,"fields":fields})
            for i, (source_key, target_key) in enumerate(pairs, 1):
                await session.execute(text("""INSERT INTO app.helio_object_links
                    (id,relation_id,source_key,target_key,source_hash,target_hash)
                    VALUES (CAST(:id AS uuid),:relation,CAST(:source AS jsonb),CAST(:target AS jsonb),:sh,:th)"""),
                    {"id":str(UUID(int=(relation << 32) + i)),"relation":relation,"source":canonical_object_key(source_key),
                     "target":canonical_object_key(target_key),"sh":object_key_hash(source_key),"th":object_key_hash(target_key)})
            return str(relation)

    async def post(self, table, action, key, **params):
        return await self.client.post(f"/etl/ts_creation/{table}/{action}", json={"key": key, **params})

    async def test_preview_and_record_nullability_follow_catalog_not_current_values(self):
        expected = {"tenant": False, "id": False, "amount": True, "area": True}
        for admin in (False, True):
            self.actor.is_superuser = admin
            for tenant in ("a", "b"):
                response = await self.post("parents", "row-by-key", {**self.parent, "tenant": tenant})
                self.assertEqual(response.status_code, 200, response.text)
                details = response.json()
                self.assertEqual(details["columnNullable"], expected)
                self.assertEqual("rowVersion" in details, admin)
        for filters in ([], [{"field": "tenant", "operator": "=", "value": "missing"}]):
            preview = await self.layers.data("ts_creation", "parents", {
                "withIdentity": True, "forEditing": True, "filters": filters,
            }, preview_rows=True)
            self.assertEqual(preview["columnNullable"], expected)
            self.assertEqual(preview["rowCount"], 0 if filters else 2)

    async def test_exact_rows_both_directions_and_composite_identity(self):
        details = await self.post("parents", "row-by-key", self.parent)
        self.assertEqual(details.status_code, 200, details.text)
        item = details.json()
        self.assertEqual(item["identity"], self.parent)
        self.assertEqual(item["row"]["id"], self.parent["id"])
        self.assertEqual(item["row"]["amount"], "3.141592653589793238")
        self.assertEqual(item["row"]["area"], {
            "type": "Point", "coordinates": [0.1, 0.2],
            "nested": {"exact": "9007199254740993", "count": 1},
        })
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
        await self.declare("declared_asset", "standalone", ['tenant','parent_id'], [({"id":"1"},self.parent)])
        observed = (await self.post("standalone","row-links",{"id":"1"})).json()
        self.assertEqual(observed["identities"], [self.parent])
        self.assertEqual(observed["relations"][0]["kind"], "logical")
        await self.pg.execute("UPDATE app.helio_object_relations SET enabled=false WHERE name='declared_asset'")
        self.assertEqual((await self.post("standalone","row-links",{"id":"1"},relation=observed["selected"])).status_code,404)
        await self.pg.execute("""INSERT INTO app.helio_objects(layer,schema_name,table_name,visibility)
                              VALUES ('creation','ts_creation','parents','hidden')""")
        self.assertEqual((await self.post("children","row-links",{"id":"1"})).json()["relations"], [])
        self.assertEqual((await self.post("parents","row-by-key",self.parent)).status_code,404)
        await self.pg.execute("UPDATE app.helio_objects SET visibility='normal' WHERE schema_name='ts_creation' AND table_name='parents'")
        await self.pg.execute("INSERT INTO ts_creation.children SELECT i,'b',9007199254740993 FROM generate_series(100,35000) i")
        await self.pg.execute("ANALYZE ts_creation.children")
        statements = []
        execute = AsyncSession.execute
        async def observe(session, query, *args, **kwargs):
            statements.append(str(query))
            return await execute(session, query, *args, **kwargs)
        with patch.object(AsyncSession, "execute", observe):
            found = (await self.post("parents","row-links",self.parent)).json()
        self.assertNotIn("reason",found)
        self.assertEqual(len(found["identities"]),10)
        self.assertTrue(any("EXPLAIN" in sql for sql in statements))
        self.assertFalse(any("pg_constraint" in sql or "CREATE " in sql for sql in statements))

    async def test_instance_unlink_cas_tombstones_permissions_and_row_guards(self):
        relation = await self.declare("independent", "standalone", ['tenant','parent_id'])
        body = {"key":{"id":"0001"},"targetKey":self.parent,"relationId":relation,"direction":"outgoing"}
        endpoint = "/etl/ts_creation/standalone/row-links"
        self.assertEqual((await self.client.post(endpoint+"/edges",json=body)).status_code,403)
        self.actor.is_superuser = True
        created = await self.client.post(endpoint+"/edges",json=body)
        self.assertEqual(created.status_code,201,created.text)
        edge = created.json()
        page = (await self.post("standalone","row-links",{"id":"1"})).json()
        self.assertEqual(page["links"][0]["linkId"],edge["linkId"])
        self.assertTrue(page["links"][0]["canUnlink"])
        self.assertEqual(page["identities"],[self.parent])
        self.actor.is_superuser = False
        self.assertFalse((await self.post("standalone","row-links",{"id":"1"})).json()["links"][0]["canUnlink"])
        removal = {**body,"expectedVersion":edge["version"]}
        self.assertEqual((await self.client.request("DELETE",endpoint+'/'+edge["linkId"],json=removal)).status_code,403)
        self.actor.is_superuser = True
        detail = (await self.post("standalone","row-by-key",{"id":"1"})).json()
        mutation = {"key":{"id":"1"},"expectedVersion":detail["rowVersion"]}
        self.assertEqual((await self.client.request("DELETE","/etl/ts_creation/standalone/rows",json=mutation)).status_code,409)
        self.assertEqual((await self.client.patch("/etl/ts_creation/standalone/rows",json={**mutation,"values":{"id":2}})).status_code,409)
        changed = await self.client.patch("/etl/ts_creation/standalone/rows",json={**mutation,"values":{"parent_id":None}})
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual((await self.post("standalone","row-links",{"id":"1"})).json()["identities"],[self.parent])
        removed = await self.client.request("DELETE",endpoint+'/'+edge["linkId"],json=removal)
        self.assertEqual(removed.status_code,200,removed.text)
        async with self.pg.session() as session:
            original = (await session.execute(text("SELECT * FROM app.helio_object_links WHERE id=CAST(:id AS uuid)"),{"id":edge["linkId"]})).mappings().one()
            self.assertEqual(original["source_key"],{"id":"1"})
            await session.execute(text("""INSERT INTO app.helio_object_links
                (id,relation_id,source_key,target_key,source_hash,target_hash) VALUES
                (CAST(:id AS uuid),:relation,CAST(:source AS jsonb),CAST(:target AS jsonb),:sh,:th)
                ON CONFLICT DO NOTHING"""), {"id":str(uuid4()),"relation":int(relation),"source":json.dumps(original["source_key"]),
                "target":json.dumps(original["target_key"]),"sh":original["source_hash"],"th":original["target_hash"]})
        self.assertEqual((await self.post("standalone","row-links",{"id":"1"})).json()["identities"],[])
        recreated = await self.client.post(endpoint+"/edges",json=body)
        self.assertEqual(recreated.status_code,201,recreated.text)
        fresh = recreated.json()
        self.assertNotEqual(fresh["linkId"],edge["linkId"])
        self.assertGreater(int(fresh["version"]),int(edge["version"]))
        self.assertEqual((await self.client.request("DELETE",endpoint+'/'+edge["linkId"],json=removal)).status_code,409)
        fresh_removal = {**body,"expectedVersion":fresh["version"]}
        self.assertEqual((await self.client.request("DELETE",endpoint+'/'+fresh["linkId"],json=fresh_removal)).status_code,200)
        current = (await self.post("standalone","row-by-key",{"id":"1"})).json()
        deleted = await self.client.request("DELETE","/etl/ts_creation/standalone/rows",json={"key":{"id":"1"},"expectedVersion":current["rowVersion"]})
        self.assertEqual(deleted.status_code,200,deleted.text)

    async def test_concurrent_cardinality_and_incoming_unlink(self):
        relation = await self.declare("independent", "standalone", ['tenant','parent_id'])
        self.actor.is_superuser = True
        body = {"key":{"id":"1"},"relationId":relation,"direction":"outgoing"}
        endpoint = "/etl/ts_creation/standalone/row-links"
        replies = await asyncio.gather(*(self.client.post(endpoint+"/edges",json={**body,"targetKey":{**self.parent,"tenant":tenant}}) for tenant in ('a','b')))
        self.assertEqual(sorted(reply.status_code for reply in replies),[201,409])
        page = (await self.post("standalone","row-links",{"id":"1"})).json()
        parent = page["identities"][0]
        reverse = (await self.post("parents","row-links",parent)).json()
        selected = next(item for item in reverse["relations"] if item["relationId"] == relation)
        incoming = (await self.post("parents","row-links",parent,relation=selected["id"])).json()
        edge = incoming["links"][0]
        result = await self.client.request("DELETE",f"/etl/ts_creation/parents/row-links/{edge['linkId']}",json={
            "key":parent,"targetKey":{"id":"1"},"relationId":relation,"direction":"incoming","expectedVersion":edge["version"]})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual((await self.post("standalone","row-links",{"id":"1"})).json()["links"],[])
