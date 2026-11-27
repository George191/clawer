"""Real metadata-management HTTP checks in the guarded disposable creation database."""

import asyncio
import unittest
from unittest.mock import patch

from app.web.services import storage_relationship_types as service
from app.web.tests import test_explorer_record_links as records
from app.web.tests import test_ontology_creation_integration as creation
from app.etl.schema.object_relations import read_object_relations
from sqlalchemy.exc import IntegrityError
from tools.migrate_storage_relationship_type_definitions import (
    ENDPOINT_FIELDS, LEGACY_IMPORT_FIELDS_CHECK, migrate_relationship_type_definitions,
)


@unittest.skipUnless(creation.URL, "Requires the disposable ontology_creation_regression database")
class StorageRelationshipTypes(unittest.IsolatedAsyncioTestCase):
    declare = records.ExplorerRecordLinks.declare
    post = records.ExplorerRecordLinks.post

    async def asyncSetUp(self):
        await records.ExplorerRecordLinks.asyncSetUp(self)
        async with self.pg.session() as session:
            await migrate_relationship_type_definitions(session, apply=True)
        for name, value in (("get_etl_layers", self.layers), ("get_etl_workspace", self.workspace)):
            active_patch = patch.object(service, name, return_value=value)
            active_patch.start()
            self.addCleanup(active_patch.stop)
        self.relation_id = str((await self.pg.fetch_one(
            "SELECT id FROM app.helio_object_relations WHERE name='child_parent'"))["id"])

    async def listing(self, table="parents"):
        return await self.client.get(f"/etl/ts_creation/{table}/relationship-types")

    async def update(self, item, *, table="parents", **changes):
        return await self.client.patch(f"/etl/ts_creation/{table}/relationship-types/{item['id']}", json={
            "name": item["name"], "description": item["description"], "expectedRevision": item["revision"], **changes,
        })

    async def create(self, name="Participates in", *, table="parents", **changes):
        return await self.client.post(f"/etl/ts_creation/{table}/relationship-types", json={
            "name": name, "description": "Semantic relationship", "target": {"schema": "ts_creation", "table": "children"},
            "cardinality": "many_to_many", "direction": "outgoing", **changes,
        })

    async def snapshot(self):
        return {
            "parents": await self.pg.fetch_all("SELECT * FROM ts_creation.parents ORDER BY tenant,id"),
            "children": await self.pg.fetch_all("SELECT * FROM ts_creation.children ORDER BY id"),
            "links": await self.pg.fetch_all("SELECT * FROM app.helio_object_links ORDER BY id"),
            "definition": await self.pg.fetch_one("""SELECT id, source_schema, source_table, source_fields,
                target_schema, target_table, target_fields, cardinality, enabled, provenance, created_at
                FROM app.helio_object_relations WHERE id=:id""", {"id": int(self.relation_id)}),
        }

    async def test_list_is_scoped_bidirectional_and_includes_disabled_and_self_once(self):
        disabled = await self.declare("inactive_parent", "standalone", ["tenant", "parent_id"])
        await self.pg.execute("UPDATE app.helio_object_relations SET enabled=false WHERE id=:id", {"id": int(disabled)})
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
        await self.pg.execute("""INSERT INTO app.helio_object_relations
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
        await self.pg.execute("ALTER TABLE app.helio_object_relations RENAME TO ontology_object_relations_unavailable")
        try:
            self.assertEqual((await self.listing()).status_code, 503)
            self.assertEqual((await self.update(item)).status_code, 503)
            self.assertFalse((await self.pg.fetch_one("SELECT to_regclass('app.helio_object_relations') IS NOT NULL AS installed"))["installed"])
        finally:
            await self.pg.execute("ALTER TABLE app.ontology_object_relations_unavailable RENAME TO helio_object_relations")

    async def test_create_supports_multiple_business_dimensions_without_import_rules_or_automatic_edges(self):
        self.assertEqual((await self.create()).status_code, 403)
        self.actor.is_superuser = True
        before = await self.snapshot()
        foreign_keys = await self.pg.fetch_all("SELECT oid,conname FROM pg_constraint WHERE contype='f' ORDER BY oid")
        ids = []
        for name, card in (("Participates in", "many_to_many"), ("Manages", "one_to_many"), ("Assigned to", "many_to_one")):
            response = await self.create(name, cardinality=card)
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.headers["cache-control"], "private, no-store")
            item = response.json()
            self.assertEqual(item["source"], {"schema": "ts_creation", "table": "parents", "fields": []})
            self.assertEqual(item["target"], {"schema": "ts_creation", "table": "children", "fields": []})
            self.assertEqual(item["cardinality"], card)
            self.assertEqual(item["direction"], "outgoing")
            ids.append(item["id"])
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(await self.snapshot(), before)
        self.assertEqual(foreign_keys, await self.pg.fetch_all("SELECT oid,conname FROM pg_constraint WHERE contype='f' ORDER BY oid"))
        async with self.layers.session() as session:
            definitions = await read_object_relations(session, "ts_creation", "parents")
        self.assertEqual({row["name"] for row in definitions if not row["columns"]}, {"Participates in", "Manages", "Assigned to"})
        self.assertEqual(next(row["columns"] for row in definitions if row["name"] == "child_parent"), ["tenant", "parent_id"])

    async def test_create_incoming_and_self_relationships_preserve_canonical_endpoints(self):
        self.actor.is_superuser = True
        incoming = await self.create("Employs", direction="incoming", cardinality="one_to_many")
        self.assertEqual(incoming.status_code, 201, incoming.text)
        item = incoming.json()
        self.assertEqual(item["source"]["table"], "children")
        self.assertEqual(item["target"]["table"], "parents")
        self.assertEqual(item["direction"], "incoming")
        self.assertEqual(item["cardinality"], "one_to_many")
        same = await self.create("Reports to", target={"schema": "ts_creation", "table": "parents"})
        self.assertEqual(same.status_code, 201, same.text)
        self_id = same.json()["id"]
        page = (await self.post("parents", "row-links", self.parent)).json()
        choices = [row for row in page["relations"] if row["relationId"] == self_id]
        self.assertEqual({row["id"] for row in choices}, {self_id + ":incoming", self_id + ":outgoing"})

    async def test_create_validates_endpoints_payload_and_concurrent_name_collisions(self):
        self.actor.is_superuser = True
        for sql in ("CREATE TABLE ts_creation.no_primary (id int)",
                    "CREATE TABLE ts_creation.partitioned (id int PRIMARY KEY) PARTITION BY HASH(id)"):
            await self.pg.execute(sql)
        for table, expected in (("no_primary", 422), ("partitioned", 422), ("missing", 404)):
            response = await self.create(target={"schema": "ts_creation", "table": table})
            self.assertEqual(response.status_code, expected, response.text)
        for change in ({"name": "\nBad"}, {"name": ""}, {"name": "a" * 129}, {"target": {"schema": "public", "table": "parents"}},
                       {"cardinality": "all"}, {"direction": "both"}, {"source_fields": ["id"]}, {"enabled": False}):
            response = await self.create(**change)
            self.assertEqual(response.status_code, 422, response.text)
        responses = await asyncio.gather(self.create("Concurrent dimension"), self.create("Concurrent dimension"))
        self.assertEqual(sorted(row.status_code for row in responses), [201, 409])
        self.assertEqual(next(row.json() for row in responses if row.status_code == 409)["detail"]["code"], "relation_name_conflict")

    async def test_new_type_adds_explicit_instances_in_both_directions_and_enforces_each_cardinality(self):
        self.actor.is_superuser = True
        before = await self.snapshot()
        for card in ("one_to_one", "many_to_one", "one_to_many", "many_to_many"):
            item = (await self.create("Dimension " + card, cardinality=card)).json()
            first = await self.post("parents", "row-links/edges", self.parent,
                relationId=item["id"], direction="outgoing", targetKey={"id": "1"})
            self.assertEqual(first.status_code, 201, first.text)
            duplicate = await self.post("parents", "row-links/edges", self.parent,
                relationId=item["id"], direction="outgoing", targetKey={"id": "1"})
            self.assertEqual(duplicate.status_code, 409, duplicate.text)
            outgoing = await self.post("parents", "row-links", self.parent, relation=item["id"] + ":outgoing")
            self.assertEqual(outgoing.json()["links"][0]["identity"], {"id": "1"})
            incoming = await self.post("children", "row-links", {"id": "1"}, relation=item["id"] + ":incoming")
            self.assertEqual(incoming.json()["links"][0]["identity"], self.parent)
            source_limit = await self.post("parents", "row-links/edges", self.parent,
                relationId=item["id"], direction="outgoing", targetKey={"id": "2"})
            self.assertEqual(source_limit.status_code, 409 if card in ("one_to_one", "many_to_one") else 201, source_limit.text)
            target_limit = await self.post("parents", "row-links/edges", {**self.parent, "tenant": "b"},
                relationId=item["id"], direction="outgoing", targetKey={"id": "1"})
            self.assertEqual(target_limit.status_code, 409 if card in ("one_to_one", "one_to_many") else 201, target_limit.text)
            wrong_direction = await self.post("parents", "row-links/edges", self.parent,
                relationId=item["id"], direction="incoming", targetKey={"id": "1"})
            self.assertEqual(wrong_direction.status_code, 404)
        after = await self.snapshot()
        for key in ("parents", "children", "definition"):
            self.assertEqual(after[key], before[key])
        self.assertGreater(len(after["links"]), len(before["links"]))

    async def test_empty_relation_page_still_exposes_management_permission(self):
        for manage in (False, True):
            self.actor.is_superuser = manage
            page = (await self.post("standalone", "row-links", {"id": "1"})).json()
            self.assertEqual(page["relations"], [])
            self.assertEqual(page["canManage"], manage)

    async def test_explicit_metadata_migration_is_verified_idempotent_and_preserves_existing_rows(self):
        await self.pg.execute("ALTER TABLE app.helio_object_relations DROP CONSTRAINT ck_ontology_relation_import_fields")
        await self.pg.execute("ALTER TABLE app.helio_object_relations ADD CONSTRAINT legacy_import_fields CHECK (" + LEGACY_IMPORT_FIELDS_CHECK + ")")
        await self.pg.execute("ALTER TABLE app.helio_object_relations ADD CONSTRAINT legacy_endpoint_mapping UNIQUE (" + ",".join(ENDPOINT_FIELDS) + ")")
        before = await self.snapshot()
        async with self.pg.session() as session:
            review = await migrate_relationship_type_definitions(session)
        self.assertTrue(review["migrationRequired"])
        self.assertFalse(review["applied"])
        async with self.pg.session() as session:
            report = await migrate_relationship_type_definitions(session, apply=True)
        self.assertTrue(report["definitionsAndLinksUnchanged"])
        self.assertEqual(set(report["changedConstraints"]), {"legacy_import_fields", "legacy_endpoint_mapping"})
        self.assertEqual(report["before"], report["after"])
        async with self.pg.session() as session:
            repeat = await migrate_relationship_type_definitions(session, apply=True)
        self.assertFalse(repeat["migrationRequired"])
        self.assertEqual(await self.snapshot(), before)
        self.actor.is_superuser = True
        self.assertEqual((await self.create()).status_code, 201)
        for values in ("ARRAY[]::text[],ARRAY['id']::text[]", "ARRAY[NULL]::text[],ARRAY['id']::text[]"):
            with self.assertRaises(IntegrityError):
                await self.pg.execute("UPDATE app.helio_object_relations SET (source_fields,target_fields)=(" + values + ") WHERE name='Participates in'")

    async def test_metadata_migration_refuses_unknown_constraints_without_rewriting_them(self):
        await self.pg.execute("ALTER TABLE app.helio_object_relations DROP CONSTRAINT ck_ontology_relation_import_fields")
        await self.pg.execute("""ALTER TABLE app.helio_object_relations ADD CONSTRAINT unreviewed_fields
            CHECK (array_length(source_fields,1)=array_length(target_fields,1))""")
        try:
            before = await self.snapshot()
            with self.assertRaisesRegex(ValueError, "differs from the reviewed contract"):
                async with self.pg.session() as session:
                    await migrate_relationship_type_definitions(session, apply=True)
            self.assertEqual(await self.snapshot(), before)
        finally:
            from app.etl.schema.object_relations import RELATION_IMPORT_FIELDS_CHECK
            await self.pg.execute("ALTER TABLE app.helio_object_relations DROP CONSTRAINT unreviewed_fields")
            await self.pg.execute("ALTER TABLE app.helio_object_relations ADD CONSTRAINT ck_ontology_relation_import_fields CHECK (" + RELATION_IMPORT_FIELDS_CHECK + ")")
