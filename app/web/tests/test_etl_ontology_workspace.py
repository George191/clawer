"""Workspace DDL uses the business client; Ontology writes use the system client."""

import re
import unittest
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError

from app.web.services.etl_workspace import EtlWorkspace
from app.web.tests.test_etl_object_types import Result


class MissingMetadata(Exception):
    sqlstate = "42P01"


class TransactionPg:
    def __init__(self, *, business=False):
        self.business = business
        self.state = {"schemas": {"ts_custom", "ts_other"}, "tables": {("ts_other", "keep")},
                      "indexes": set(), "properties": {},
                      "objects": {("ts_custom", "old"): {"id": 1}, ("ts_other", "keep"): {"id": 2}},
                      "relations": [{"source_schema": "ts_custom", "target_schema": "ts_other"},
                                    {"source_schema": "ts_other", "target_schema": "ts_custom"},
                                    {"source_schema": "ts_other", "target_schema": "ts_other"}]}
        self.calls = []
        self.commits = self.rollbacks = 0
        self.active = False
        self.fail_on = ""
        self.missing_metadata = False

    async def connect(self, **_kwargs):
        pass

    @asynccontextmanager
    async def session(self):
        before = deepcopy(self.state)
        self.active = True
        try:
            yield self
        except Exception:
            self.state.clear()
            self.state.update(before)
            self.rollbacks += 1
            raise
        else:
            self.commits += 1
        finally:
            self.active = False

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.calls.append((sql, deepcopy(params)))
        if self.business and "app.ontology_" in sql:
            raise AssertionError("Business queries must not read or write system metadata")
        if self.fail_on and sql.startswith(self.fail_on):
            raise RuntimeError("Fixture rejects transaction step")
        if self.missing_metadata and "app.ontology_" in sql:
            raise DBAPIError(sql, params, MissingMetadata("Ontology table does not exist"), False)
        if sql.startswith("SET LOCAL"):
            return Result()
        if sql.startswith("SELECT 1 FROM pg_namespace"):
            return Result(scalar=1 if params["schema"] in self.state["schemas"] else None)
        if sql.startswith("SELECT 1 FROM app.helio_objects"):
            return Result(scalar=1 if any(schema == params["schema"] and row.get("ddl_sql")
                for (schema, _table), row in self.state["objects"].items()) else None)
        if sql.startswith("SELECT to_regclass"):
            return Result(scalar="helio_" in str(params))
        if sql.startswith("SELECT pg_advisory_xact_lock"):
            return Result()
        if sql.startswith("SELECT p.name FROM app.helio_objects"):
            identity = (params["schema"], params["table"])
            metadata = self.state["objects"].get(identity)
            fields = self.state["properties"].get(identity, []) if metadata and metadata.get("is_active") else []
            return Result(rows=[{"name": name} for name in fields])
        if sql.startswith("SELECT a.attname AS name FROM pg_namespace"):
            identity = (params["schema"], params["table"])
            fields = self.state["properties"].get(identity, []) if identity in self.state["tables"] else []
            return Result(rows=[{"name": name} for name in fields])
        if sql.startswith("SELECT name, source_schema, source_table"):
            source = (params["schema"], params["table"])
            return Result(rows=[{**row, "columns": row["source_fields"], "referenced_columns": row["target_fields"]}
                for row in self.state["relations"] if row.get("name") and (row["name"] in params["names"]
                    or source in ((row["source_schema"], row["source_table"]), (row["target_schema"], row["target_table"])) )])
        if sql.startswith("CREATE TABLE"):
            identity = re.search(r'"([a-z_]+)"\."([a-z_]+)"', sql).groups()
            self.state["tables"].add(identity)
        elif sql.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX")):
            self.state["indexes"].add(re.search(r'INDEX "([a-z_]+)"', sql).group(1))
        elif sql.startswith("INSERT INTO app.helio_object_relations"):
            self.state["relations"].append(deepcopy(params))
        elif sql.startswith("COMMENT ON"):
            pass
        elif sql.startswith("ALTER SCHEMA"):
            source, target = re.findall(r'"([a-z_]+)"', sql)
            self.state["schemas"].remove(source)
            self.state["schemas"].add(target)
            self.state["tables"] = {(target if schema == source else schema, table)
                                    for schema, table in self.state["tables"]}
        elif sql.startswith("DROP SCHEMA"):
            self.state["schemas"].remove(re.findall(r'"([a-z_]+)"', sql)[0])
        elif sql.startswith("UPDATE app.helio_objects"):
            self.state["objects"] = {(params["target"] if schema == params["schema"] else schema, table):
                {**value, **{field: params[field] for field in ("layer", "table_role", "resource_group")}}
                if schema == params["schema"] else value
                                     for (schema, table), value in self.state["objects"].items()}
        elif sql.startswith("UPDATE app.helio_object_relations"):
            side = "source_schema" if "SET source_schema" in sql else "target_schema"
            for row in self.state["relations"]:
                if row[side] == params["schema"]:
                    row[side] = params["target"]
        elif sql.startswith("DELETE FROM app.helio_object_relations"):
            self.state["relations"] = [row for row in self.state["relations"]
                                       if params["schema"] not in (row["source_schema"], row["target_schema"])]
        elif sql.startswith("DELETE FROM app.helio_objects"):
            self.state["objects"] = {key: value for key, value in self.state["objects"].items()
                                     if key[0] != params["schema"]}
        else:
            raise AssertionError("Unexpected workspace SQL: " + sql)
        return Result()


def workspace():
    instance = object.__new__(EtlWorkspace)
    instance._pg = TransactionPg()
    instance._business_pg = TransactionPg(business=True)
    instance._business_pg.state = instance._pg.state
    instance._layers = type("Layers", (), {"get_table": AsyncMock(return_value={"name": "created"})})()
    return instance


PAYLOAD = {"name": "created", "dataType": "company", "description": "Created through Flow", "fields": [
    {"name": "id", "type": "BIGINT", "primary": True, "nullable": False},
    {"name": "name", "type": "TEXT", "comment": "Display text"}]}
RELATION = {"name": "created_keeps", "targetSchema": "ts_other", "targetTable": "keep",
    "sourceFields": ["id", "name"], "targetFields": ["id", "name"], "cardinality": "many_to_one", "description": "Linked records"}


class OntologyWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def create_with_relation_metadata(self, instance, payload):
        async def sync(session, schema, table, **_kwargs):
            session.state["objects"][(schema, table)] = {"id": 3, "is_active": True}
            session.state["properties"][(schema, table)] = [field["name"] for field in payload["fields"]]

        with patch("app.web.services.etl_workspace.sync_object_metadata", new=AsyncMock(side_effect=sync)):
            return await instance.create_table("custom", payload)

    def relation_workspace(self):
        instance = workspace()
        instance._pg.state["objects"][("ts_other", "keep")]["is_active"] = True
        instance._pg.state["properties"][("ts_other", "keep")] = ["id", "name"]
        return instance

    async def test_create_cross_schema_logical_relation_in_one_transaction_without_foreign_keys(self):
        instance = self.relation_workspace()
        await self.create_with_relation_metadata(instance, {**PAYLOAD, "relations": [RELATION]})
        row = instance._pg.state["relations"][-1]
        self.assertEqual((row["source_schema"], row["source_table"], row["target_schema"], row["target_table"]),
                         ("ts_custom", "created", "ts_other", "keep"))
        self.assertEqual((row["source_fields"], row["target_fields"], row["provenance"]),
                         (["id", "name"], ["id", "name"], "ontology:create_table"))
        self.assertEqual((instance._pg.commits, instance._pg.rollbacks), (1, 0))
        self.assertFalse(any("FOREIGN KEY" in sql or "SELECT * FROM" in sql for sql, _ in instance._pg.calls))

    async def test_target_must_be_physical_active_registered_object_with_registered_properties(self):
        for invalid in ("physical", "registration", "active", "property"):
            instance = self.relation_workspace()
            target = ("ts_other", "keep")
            if invalid == "physical":
                instance._pg.state["tables"].remove(target)
            elif invalid == "registration":
                instance._pg.state["objects"].pop(target)
            elif invalid == "active":
                instance._pg.state["objects"][target]["is_active"] = False
            else:
                instance._pg.state["properties"][target] = ["id"]
            before = deepcopy(instance._pg.state)
            with self.subTest(invalid=invalid), self.assertRaises(HTTPException) as error:
                await self.create_with_relation_metadata(instance, {**PAYLOAD, "relations": [RELATION]})
            self.assertEqual(error.exception.status_code, 422)
            self.assertEqual(instance._pg.state, before)
            self.assertEqual((instance._pg.commits, instance._pg.rollbacks), (0, 1))
            instance._layers.get_table.assert_not_awaited()

    async def test_existing_name_and_reverse_property_mapping_conflicts_roll_back_creation(self):
        for conflict in ("name", "mapping"):
            instance = self.relation_workspace()
            instance._pg.state["relations"].append({"name": RELATION["name"] if conflict == "name" else "reverse",
                "source_schema": "ts_other", "source_table": "keep", "source_fields": ["name", "id"],
                "target_schema": "ts_custom", "target_table": "other" if conflict == "name" else "created",
                "target_fields": ["name", "id"]})
            before = deepcopy(instance._pg.state)
            with self.subTest(conflict=conflict), self.assertRaises(HTTPException) as error:
                await self.create_with_relation_metadata(instance, {**PAYLOAD, "relations": [RELATION]})
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual(instance._pg.state, before)
            self.assertEqual((instance._pg.commits, instance._pg.rollbacks), (0, 1))

    async def test_second_relation_failure_rolls_back_first_relation_properties_indexes_and_table(self):
        instance = self.relation_workspace()
        payload = {**PAYLOAD, "indexes": [{"name": "composite", "fields": ["id", "name"]}], "relations": [
            RELATION, {**RELATION, "name": "second", "targetFields": ["name", "id"]}]}
        before = deepcopy(instance._pg.state)
        original_execute = instance._pg.execute
        inserts = 0

        async def fail_second_relation(statement, params=None):
            nonlocal inserts
            if str(statement).startswith("INSERT INTO app.helio_object_relations"):
                inserts += 1
                if inserts == 2:
                    raise RuntimeError("Second relation rejected")
            return await original_execute(statement, params)

        with patch.object(instance._pg, "execute", new=fail_second_relation), self.assertRaisesRegex(RuntimeError, "Second relation rejected"):
            await self.create_with_relation_metadata(instance, payload)
        self.assertEqual(inserts, 2)
        self.assertEqual(instance._pg.state, before)
        self.assertEqual((instance._pg.commits, instance._pg.rollbacks), (0, 1))
        instance._layers.get_table.assert_not_awaited()

    async def test_create_table_syncs_metadata_in_the_physical_ddl_transaction(self):
        instance = workspace()
        pg = instance._pg

        async def sync(session, schema, table, **kwargs):
            self.assertIs(session, pg)
            self.assertIs(kwargs["catalog_session"], instance._business_pg)
            self.assertTrue(pg.active)
            self.assertIn((schema, table), pg.state["tables"])
            self.assertEqual(kwargs["provenance"], "flow:create_table")
            self.assertEqual(kwargs["description"], PAYLOAD["description"])
            self.assertEqual(kwargs["data_type"], "company")
            self.assertEqual(kwargs["data_type_source"], "registration")
            self.assertIn('CREATE TABLE "ts_custom"."created"', kwargs["ddl_sql"])
            self.assertIn('"id" BIGINT NOT NULL', kwargs["ddl_sql"])
            pg.state["objects"][(schema, table)] = {"id": 3}

        with patch("app.web.services.etl_workspace.sync_object_metadata", new=AsyncMock(side_effect=sync)) as synchronize:
            self.assertEqual(await instance.create_table("custom", PAYLOAD), {"name": "created"})
        synchronize.assert_awaited_once()
        self.assertEqual((pg.commits, pg.rollbacks), (1, 0))
        self.assertFalse(pg.active)
        self.assertIn(("ts_custom", "created"), pg.state["objects"])
        instance._layers.get_table.assert_awaited_once_with("ts_custom", "created")

    async def test_create_table_rejects_missing_or_invalid_data_type_before_ddl(self):
        for value in [None, "", " ", "Company", "company-name", "a" * 64, 1, [], "company;DROP TABLE users"]:
            with self.subTest(value=value):
                instance = workspace()
                before = deepcopy(instance._pg.state)
                payload = {**PAYLOAD, "dataType": value}
                with patch("app.web.services.etl_workspace.sync_object_metadata", new=AsyncMock()) as synchronize:
                    with self.assertRaises(HTTPException) as error:
                        await instance.create_table("custom", payload)
                self.assertEqual(error.exception.status_code, 422)
                synchronize.assert_not_awaited()
                self.assertEqual(instance._pg.state, before)
                self.assertEqual((instance._pg.commits, instance._pg.rollbacks), (0, 0))
                instance._layers.get_table.assert_not_awaited()

    async def test_sync_failure_rolls_back_ddl_and_partial_metadata(self):
        instance = workspace()
        before = deepcopy(instance._pg.state)

        async def fail(session, schema, table, **_kwargs):
            session.state["objects"][(schema, table)] = {"id": 3}
            raise RuntimeError("Metadata synchronization failed")

        with patch("app.web.services.etl_workspace.sync_object_metadata", new=AsyncMock(side_effect=fail)):
            with self.assertRaisesRegex(RuntimeError, "synchronization failed"):
                await instance.create_table("custom", PAYLOAD)
        self.assertEqual(instance._pg.state, before)
        self.assertEqual((instance._pg.commits, instance._pg.rollbacks), (0, 1))
        instance._layers.get_table.assert_not_awaited()

    async def test_ddl_failure_never_attempts_metadata_sync(self):
        instance = workspace()
        before = deepcopy(instance._pg.state)
        instance._business_pg.fail_on = "CREATE TABLE"
        with patch("app.web.services.etl_workspace.sync_object_metadata", new=AsyncMock()) as synchronize:
            with self.assertRaisesRegex(RuntimeError, "transaction step"):
                await instance.create_table("custom", PAYLOAD)
        synchronize.assert_not_awaited()
        self.assertEqual(instance._pg.state, before)
        self.assertEqual(instance._pg.rollbacks, 1)
        self.assertEqual(instance._business_pg.rollbacks, 1)
        instance._layers.get_table.assert_not_awaited()

    async def test_schema_rename_updates_objects_and_both_relation_sides_atomically(self):
        instance = workspace()
        result = await instance.rename_schema("custom", {"name": "renamed", "alias": "Business domain"})
        self.assertEqual(result["name"], "ts_renamed")
        self.assertNotIn("ts_custom", instance._pg.state["schemas"])
        parameters = next(params for sql, params in instance._pg.calls if sql.startswith("UPDATE app.helio_objects"))
        self.assertEqual(parameters, {"schema": "ts_custom", "target": "ts_renamed", "layer": "renamed",
            "table_role": "current", "resource_group": "other"})
        self.assertEqual(instance._pg.state["objects"], {("ts_renamed", "old"): {"id": 1,
            "layer": "renamed", "table_role": "current", "resource_group": "other"},
                                                       ("ts_other", "keep"): {"id": 2}})
        self.assertEqual(instance._pg.state["relations"], [
            {"source_schema": "ts_renamed", "target_schema": "ts_other"},
            {"source_schema": "ts_other", "target_schema": "ts_renamed"},
            {"source_schema": "ts_other", "target_schema": "ts_other"}])
        self.assertEqual((instance._pg.commits, instance._pg.rollbacks), (1, 0))

    async def test_managed_ddl_blocks_schema_rename_before_any_mutation_and_preserves_alias(self):
        instance = workspace()
        instance._pg.state["objects"][("ts_custom", "old")]["ddl_sql"] = 'CREATE TABLE "ts_custom"."old" (id bigint);'
        before = deepcopy(instance._pg.state)
        with self.assertRaises(HTTPException) as error:
            await instance.rename_schema("custom", {"name": "renamed", "alias": "Business domain"})
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("alias", error.exception.detail)
        self.assertEqual(instance._pg.state, before)
        self.assertFalse(any(sql.startswith(("ALTER", "UPDATE", "COMMENT")) for sql, _ in instance._pg.calls))
        self.assertEqual((instance._pg.commits, instance._pg.rollbacks), (0, 1))
        await instance.rename_schema("custom", {"alias": "Business domain"})
        self.assertTrue(any(sql.startswith('COMMENT ON SCHEMA "ts_custom"') for sql, _ in instance._business_pg.calls))
        self.assertEqual(instance._pg.state, before)
        self.assertEqual((instance._pg.commits, instance._pg.rollbacks), (1, 1))

    async def test_metadata_only_rename_updates_resource_group_and_history_role(self):
        for target, expected_layer, expected_role, expected_group in [
            ("custom_hist", "custom", "history", "other"),
            ("ads", "ads", "current", "serving"),
        ]:
            instance = workspace()
            await instance.rename_schema("custom", {"name": target})
            row = instance._pg.state["objects"][(f"ts_{target}", "old")]
            self.assertEqual((row["layer"], row["table_role"], row["resource_group"]),
                (expected_layer, expected_role, expected_group))
            self.assertEqual(instance._pg.state["objects"][("ts_other", "keep")], {"id": 2})

    async def test_schema_rename_metadata_failure_restores_original_schema_and_rows(self):
        instance = workspace()
        before = deepcopy(instance._pg.state)
        instance._pg.fail_on = "UPDATE app.helio_object_relations"
        with self.assertRaisesRegex(RuntimeError, "transaction step"):
            await instance.rename_schema("custom", {"name": "renamed"})
        self.assertEqual(instance._pg.state, before)
        self.assertEqual((instance._pg.commits, instance._pg.rollbacks), (0, 1))

    async def test_drop_schema_removes_only_its_objects_and_incident_relations(self):
        instance = workspace()
        result = await instance.delete_schema("custom")
        self.assertTrue(result["deleted"])
        self.assertEqual(instance._pg.state["objects"], {("ts_other", "keep"): {"id": 2}})
        self.assertEqual(instance._pg.state["relations"], [{"source_schema": "ts_other", "target_schema": "ts_other"}])
        self.assertIn(("ts_other", "keep"), instance._pg.state["tables"])
        self.assertEqual((instance._pg.commits, instance._pg.rollbacks), (1, 0))

    async def test_drop_metadata_failure_restores_schema_and_all_its_metadata(self):
        instance = workspace()
        before = deepcopy(instance._pg.state)
        instance._pg.fail_on = "DELETE FROM app.helio_object_relations"
        with self.assertRaisesRegex(RuntimeError, "transaction step"):
            await instance.delete_schema("custom")
        self.assertEqual(instance._pg.state, before)
        self.assertEqual((instance._pg.commits, instance._pg.rollbacks), (0, 1))

    async def test_missing_metadata_during_rename_or_drop_returns_503_and_rolls_back(self):
        for operation in ("rename", "drop"):
            instance = workspace()
            before = deepcopy(instance._pg.state)
            instance._pg.missing_metadata = True
            with self.subTest(operation=operation), self.assertRaises(HTTPException) as error:
                if operation == "rename":
                    await instance.rename_schema("custom", {"name": "renamed"})
                else:
                    await instance.delete_schema("custom")
            self.assertEqual(error.exception.status_code, 503)
            self.assertEqual(instance._pg.state, before)
            self.assertEqual((instance._pg.commits, instance._pg.rollbacks), (0, 1))

    async def test_create_missing_ontology_returns_503_and_rolls_back_physical_table(self):
        instance = workspace()
        before = deepcopy(instance._pg.state)
        failure = DBAPIError("SELECT * FROM app.helio_objects", {}, MissingMetadata(), False)
        with patch("app.web.services.etl_workspace.sync_object_metadata", new=AsyncMock(side_effect=failure)):
            with self.assertRaises(HTTPException) as error:
                await instance.create_table("custom", PAYLOAD)
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(instance._pg.state, before)
        self.assertEqual((instance._pg.commits, instance._pg.rollbacks), (0, 1))
        instance._layers.get_table.assert_not_awaited()

    async def test_business_ddl_missing_relation_remains_422_instead_of_ontology_503(self):
        instance = workspace()
        before = deepcopy(instance._pg.state)
        original = instance._business_pg.execute

        async def fail_business_ddl(statement, params=None):
            if str(statement).startswith("CREATE TABLE"):
                raise DBAPIError(str(statement), params, MissingMetadata("Missing referenced business table"), False)
            return await original(statement, params)

        instance._business_pg.execute = fail_business_ddl
        with patch("app.web.services.etl_workspace.sync_object_metadata", new=AsyncMock()) as synchronize:
            with self.assertRaises(HTTPException) as error:
                await instance.create_table("custom", PAYLOAD)
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(instance._pg.state, before)
        self.assertEqual(instance._pg.rollbacks, 1)
        synchronize.assert_not_awaited()
