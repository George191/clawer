"""Real metadata-management HTTP checks in the guarded disposable creation database."""

import asyncio
import unittest
from unittest.mock import patch

from app.web.services import storage_relationship_types as service
from app.web.tests import test_explorer_record_links as records
from app.web.tests import test_ontology_creation_integration as creation


@unittest.skipUnless(creation.URL, "Requires the disposable ontology_creation_regression database")
class StorageRelationshipTypes(unittest.IsolatedAsyncioTestCase):
    declare = records.ExplorerRecordLinks.declare
    post = records.ExplorerRecordLinks.post

    async def asyncSetUp(self):
        await records.ExplorerRecordLinks.asyncSetUp(self)
        for name, value in (("get_etl_layers", self.layers), ("get_etl_workspace", self.workspace)):
            active_patch = patch.object(service, name, return_value=value)
            active_patch.start()
            self.addCleanup(active_patch.stop)
        self.relation_id = str((await self.pg.fetch_one(
            "SELECT id FROM public.ontology_object_relations WHERE name='child_parent'"))["id"])

    async def listing(self, table="parents"):
        return await self.client.get(f"/etl/ts_creation/{table}/relationship-types")

    async def update(self, item, *, table="parents", **changes):
        return await self.client.patch(f"/etl/ts_creation/{table}/relationship-types/{item['id']}", json={
            "name": item["name"], "description": item["description"], "expectedRevision": item["revision"], **changes,
        })

    async def snapshot(self):
        return {
            "parents": await self.pg.fetch_all("SELECT * FROM ts_creation.parents ORDER BY tenant,id"),
            "children": await self.pg.fetch_all("SELECT * FROM ts_creation.children ORDER BY id"),
            "links": await self.pg.fetch_all("SELECT * FROM public.ontology_object_links ORDER BY id"),
            "definition": await self.pg.fetch_one("""SELECT id, source_schema, source_table, source_fields,
                target_schema, target_table, target_fields, cardinality, enabled, provenance, created_at
                FROM public.ontology_object_relations WHERE id=:id""", {"id": int(self.relation_id)}),
        }

    async def test_list_is_scoped_bidirectional_and_includes_disabled_and_self_once(self):
        disabled = await self.declare("inactive_parent", "standalone", ["tenant", "parent_id"])
        await self.pg.execute("UPDATE public.ontology_object_relations SET enabled=false WHERE id=:id", {"id": int(disabled)})
        self_id = await self.declare("self_parent", "parents", ["tenant", "id"])
        response = await self.listing()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        page = response.json()
        self.assertFalse(page["canManage"])
        items = {item["id"]: item for item in page["items"]}
        self.assertEqual(len(items), 3)
        self.assertEqual(items[self.relation_id]["direction"], "incoming")
        self.assertEqual(items[self_id]["direction"], "outgoing")
        self.assertFalse(items[disabled]["enabled"])
        self.assertEqual(items[self.relation_id]["source"], {"schema": "ts_creation", "table": "children", "fields": ["tenant", "parent_id"]})
        self.assertEqual(items[self.relation_id]["target"], {"schema": "ts_creation", "table": "parents", "fields": ["tenant", "id"]})
        child = (await self.listing("children")).json()["items"]
        self.assertEqual([item["id"] for item in child], [self.relation_id])
        self.assertEqual(child[0]["direction"], "outgoing")
        self.actor.is_superuser = True
        self.assertTrue((await self.listing()).json()["canManage"])

    async def test_only_superusers_can_save_and_anchor_scope_is_enforced(self):
        item = (await self.listing()).json()["items"][0]
        self.assertEqual((await self.update(item, name="Denied")).status_code, 403)
        self.actor.is_superuser = True
        self.assertEqual((await self.update(item, table="standalone", name="Wrong anchor")).status_code, 404)
        self.assertEqual((await self.update({**item, "id": "999999999"})).status_code, 404)
        self.assertEqual((await self.listing("missing_table")).status_code, 404)
        self.assertEqual((await self.listing()).json()["items"][0], item)

    async def test_rename_preserves_business_rows_edges_definition_identity_and_current_selector(self):
        self.actor.is_superuser = True
        item = (await self.listing()).json()["items"][0]
        page = (await self.post("parents", "row-links", self.parent)).json()
        self.assertEqual(page["selected"], self.relation_id + ":incoming")
        before = await self.snapshot()
        renamed = await self.update(item, name=" 任职企业 ", description="Updated relationship description")
        self.assertEqual(renamed.status_code, 200, renamed.text)
        changed = renamed.json()
        self.assertEqual(changed["name"], "任职企业")
        self.assertEqual(changed["description"], "Updated relationship description")
        self.assertEqual(changed["id"], item["id"])
        self.assertNotEqual(changed["revision"], item["revision"])
        self.assertEqual(before, await self.snapshot())
        following = await self.post("parents", "row-links", self.parent, relation=page["selected"])
        self.assertEqual(following.status_code, 200, following.text)
        self.assertEqual(following.json()["links"], page["links"])
        self.assertEqual(following.json()["selected"], page["selected"])
        self.assertEqual(following.json()["relations"][0]["name"], "任职企业")
        reverse = (await self.listing("children")).json()["items"][0]
        self.assertEqual(reverse["revision"], changed["revision"])
        saved = await self.update(reverse, table="children", description="Edited from source")
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual((await self.listing()).json()["items"][0]["description"], "Edited from source")

    async def test_noop_keeps_revision_stale_and_duplicate_edits_do_not_overwrite(self):
        self.actor.is_superuser = True
        item = (await self.listing()).json()["items"][0]
        unchanged = await self.update(item)
        self.assertEqual(unchanged.status_code, 200, unchanged.text)
        self.assertEqual(unchanged.json(), item)
        other = await self.declare("Other name", "standalone", ["tenant", "parent_id"])
        collision = await self.update(item, name="Other name")
        self.assertEqual(collision.status_code, 409, collision.text)
        self.assertEqual(collision.json()["detail"]["code"], "relation_name_conflict")
        self.assertEqual((await self.update(item, name="New name")).status_code, 200)
        stale = await self.update(item, name="Stale overwrite")
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["code"], "relation_version_conflict")
        current = {row["id"]: row for row in (await self.listing()).json()["items"]}
        self.assertEqual(current[item["id"]]["name"], "New name")
        self.assertEqual(current[other]["name"], "Other name")

    async def test_concurrent_revisions_allow_exactly_one_writer(self):
        self.actor.is_superuser = True
        item = (await self.listing()).json()["items"][0]
        responses = await asyncio.gather(self.update(item, name="First writer"), self.update(item, name="Second writer"))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        winner = next(response.json() for response in responses if response.status_code == 200)
        self.assertEqual((await self.listing()).json()["items"][0], winner)

    async def test_large_identifiers_are_exact_strings_and_payload_cannot_change_structure(self):
        self.actor.is_superuser = True
        await self.pg.execute("CREATE TABLE ts_creation.precision_source (id int PRIMARY KEY, tenant text, parent_id bigint)")
        exact_id = "9007199254740993"
        await self.pg.execute("""INSERT INTO public.ontology_object_relations
            (id,name,source_schema,source_table,source_fields,target_schema,target_table,target_fields,
             relationship_kind,cardinality,provenance) VALUES (:id,'Exact relation','ts_creation','precision_source',
             ARRAY['tenant','parent_id'],'ts_creation','parents',ARRAY['tenant','id'],'association','many_to_one','precision test')""", {"id": int(exact_id)})
        item = (await self.listing("precision_source")).json()["items"][0]
        self.assertEqual(item["id"], exact_id)
        self.assertEqual((await self.update(item, table="precision_source", name="Exact renamed")).json()["id"], exact_id)
        item = (await self.listing()).json()["items"][0]
        for values in ({"name": ""}, {"name": " "}, {"name": "a" * 129}, {"name": "\nBad"},
                       {"name": "Bad\x00name"}, {"name": 123}, {"description": "a" * 4001},
                       {"expectedRevision": None}, {"source": {}}, {"cardinality": "many_to_many"}, {"enabled": False}):
            with self.subTest(values=values):
                response = await self.update(item, **values)
                self.assertEqual(response.status_code, 422, response.text)
        for relation_id in ("0", "-1", "9223372036854775808", "01", "invalid"):
            self.assertEqual((await self.update({**item, "id": relation_id})).status_code, 422)

    async def test_uninitialized_registry_fails_explicitly_without_installing_it(self):
        self.actor.is_superuser = True
        item = (await self.listing()).json()["items"][0]
        await self.pg.execute("ALTER TABLE public.ontology_object_relations RENAME TO ontology_object_relations_unavailable")
        try:
            self.assertEqual((await self.listing()).status_code, 503)
            self.assertEqual((await self.update(item)).status_code, 503)
            self.assertFalse((await self.pg.fetch_one("SELECT to_regclass('public.ontology_object_relations') IS NOT NULL AS installed"))["installed"])
        finally:
            await self.pg.execute("ALTER TABLE public.ontology_object_relations_unavailable RENAME TO ontology_object_relations")
