"""Ontology metadata contracts; all PostgreSQL I/O uses local catalog fixtures."""

from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from app.etl.schema import ontology, registry
from app.etl.schema.catalogs import TABLE_DEFINITIONS
from app.etl.schema.ddl import ddl_checksum
from app.web.api.models.ontology_registry import Source
from app.web.services import ontology_registry
from tools import ontology_etl_bindings


class Result:
    def __init__(self, rows=(), scalar=None):
        self.rows, self.scalar = list(rows), scalar

    def mappings(self):
        return self

    def first(self):
        return deepcopy(self.rows[0]) if self.rows else None

    def scalar_one(self):
        return self.scalar

    def __iter__(self):
        return iter(dict(row) for row in self.rows)


def property_row(name="id", **changes):
    return {"name": name, "sql_type": "bigint", "ordinal_position": 1,
        "default_expression": None, "is_nullable": False, "is_primary_key": True,
        "primary_key_position": 1, "identity_kind": b"a", "generated_kind": b"",
        "generation_expression": None, "comment": "", **changes}


class MetadataPg:
    def __init__(self, schema="ts_custom", table="items", physical=True):
        self.catalog = {(schema, table): {"oid": 100, "comment": None, "partstrat": None,
            "partition_fields": [], "partition_count": 0}} if physical else {}
        self.columns = {100: [property_row()]}
        self.objects, self.properties, self.calls = {}, {}, []
        self.commits, self.rollbacks, self.bootstrap_count = 0, 0, 0
        self.fail_properties = False

    @asynccontextmanager
    async def session(self):
        snapshot = deepcopy((self.catalog, self.objects, self.properties))
        try:
            yield self
        except Exception:
            self.catalog, self.objects, self.properties = snapshot
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    @asynccontextmanager
    async def locked_transaction(self, key):
        self.lock_key = key
        async with self.session():
            yield self

    async def init_schema(self, statements):
        self.bootstrap_count += 1
        self.bootstrap_statements = list(statements)

    async def fetch_one(self, sql, params):
        if sql.startswith("SELECT to_regclass"):
            return {"table_name": "app.ontology_objects"}
        result = await self.execute(sql, params)
        return result.first()

    async def fetch_all(self, sql, params=None):
        return list((await self.execute(sql, params)).mappings())

    def register(self, schema, table, *, layer, data_type, role="current"):
        record = {"id": len(self.objects) + 1, "layer": layer, "table_role": role,
            "schema_name": schema, "table_name": table, "is_active": True,
            "data_type": data_type, "data_type_source": "registration",
            "version": 0, "checksum": "", "ddl_sql": "", "description": None,
            "description_source": "not_configured", "partition_type": "none",
            "partition_column": None, "partition_granularity": None, "partition_count": None}
        self.objects[(schema, table)] = record
        return record

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.calls.append((sql, deepcopy(params)))
        sql = sql.replace("app.ontology_", "ontology_")
        params = params or {}
        if sql.startswith("SELECT c.oid"):
            row = self.catalog.get((params["schema"], params["table"]))
            return Result([row] if row else [])
        if sql.startswith("SELECT a.attname"):
            return Result(self.columns.get(params["oid"], []))
        if sql.startswith("SELECT") and "FROM ontology_objects" in sql:
            rows = list(self.objects.values())
            schema, table = params.get("schema", params.get("schema_name")), params.get("table", params.get("table_name"))
            if schema is not None:
                rows = [row for row in rows if (row["schema_name"], row["table_name"]) == (schema, table)]
            if "is_active" in sql:
                rows = [row for row in rows if row.get("is_active", True)]
            if "ddl_sql <> ''" in sql:
                rows = [row for row in rows if row.get("ddl_sql")]
            if params.get("layer"):
                rows = [row for row in rows if row["layer"] == params["layer"]]
            role = params.get("table_role", params.get("role"))
            if role:
                rows = [row for row in rows if row["table_role"] == role]
            if params.get("data_type"):
                rows = [row for row in rows if row["data_type"] == params["data_type"]]
            return Result(rows)
        if sql.startswith("INSERT INTO ontology_objects"):
            schema, table = params.get("schema", params.get("schema_name")), params.get("table", params.get("table_name"))
            identity = (schema, table)
            previous = self.objects.get(identity, {})
            record = {"data_type": "", "data_type_source": "not_configured", "is_active": True, **previous, **params,
                "id": previous.get("id", len(self.objects) + 1), "schema_name": schema,
                "table_name": table, "table_role": params.get("role", params.get("table_role"))}
            if "COALESCE(EXCLUDED.description" in sql and params.get("description") is None:
                record["description"] = previous.get("description")
                record["description_source"] = previous.get("description_source", "not_configured")
            self.objects[identity] = record
            return Result(scalar=record["id"])
        if sql.startswith("DELETE FROM ontology_object_properties"):
            self.properties[params["object_id"]] = []
        elif sql.startswith("INSERT INTO ontology_object_properties"):
            for row in params:
                self.properties.setdefault(row["object_id"], []).append(deepcopy(row))
                if self.fail_properties:
                    raise RuntimeError("Fixture rejects property insertion")
        elif sql.startswith("UPDATE ontology_objects"):
            for record in self.objects.values():
                if record["id"] == params["id"]:
                    record.update(ddl_sql=params["ddl_sql"], version=params["version"],
                                  checksum=params["checksum"], partition_type=params["kind"],
                                  partition_column=params["column"], partition_granularity=params["granularity"],
                                  partition_count=params["count"], updated_by=params["updated_by"])
        elif not sql.startswith("CREATE EXTENSION IF NOT EXISTS postgis"):
            raise AssertionError("Unexpected or business SQL: " + sql)
        return Result()


class OntologyMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_semantic_draft_looks_up_backing_connection_separately_from_upstream(self):
        storage_id, upstream_id = uuid4(), uuid4()
        source = Source(mode="existing", schema="Backing.Data", table="Events",
                        connectionId=storage_id, upstreamConnectionId=upstream_id)
        opened = []

        class Layers:
            @asynccontextmanager
            async def session(self, **scope):
                opened.append(scope)
                yield self

        with patch.object(ontology_registry, "get_etl_layers", return_value=Layers()), \
             patch.object(ontology_registry, "_warehouse_table", new_callable=AsyncMock,
                          return_value="warehouse table"):
            self.assertEqual(await ontology_registry.warehouse_table(source), "warehouse table")
            with self.assertRaises(HTTPException) as error:
                await ontology_registry.warehouse_table(source, connection_id=str(upstream_id))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(opened, [{"schema": "Backing.Data", "table": "Events", "connection_id": str(storage_id)}])

    async def sync(self, pg, **kwargs):
        async with pg.session() as session:
            return await ontology.sync_object_metadata(session, "ts_custom", "items", catalog_session=session,
                                                       layer="custom", table_role="current", **kwargs)

    async def test_separate_catalog_session_keeps_metadata_on_the_system_session(self):
        catalog = MetadataPg()
        system = MetadataPg(physical=False)
        async with system.session() as session:
            result = await ontology.sync_object_metadata(session, "ts_custom", "items", catalog_session=catalog,
                                                         layer="custom", table_role="current")

        self.assertEqual(result["properties_count"], 1)
        self.assertEqual(len(catalog.calls), 2)
        self.assertEqual(catalog.objects, {})
        self.assertIn(("ts_custom", "items"), system.objects)

    async def test_catalog_properties_preserve_real_type_primary_order_and_generated_identity(self):
        pg = MetadataPg()
        pg.columns[100] = [property_row("a", ordinal_position=2, primary_key_position=2,
                sql_type="numeric(18,4)", comment="Amount"),
            property_row("b", ordinal_position=5, primary_key_position=1, identity_kind=memoryview(b""),
                generated_kind=bytearray(b"s"), generation_expression="(a * 2)", default_expression=None)]
        result = await self.sync(pg)
        self.assertEqual(result["properties_count"], 2)
        properties = pg.properties[result["id"]]
        self.assertEqual([(row["name"], row["ordinal_position"], row["primary_key_position"]) for row in properties], [("a", 2, 2), ("b", 5, 1)])
        self.assertEqual((properties[0]["sql_type"], properties[0]["identity_kind"]), ("numeric(18,4)", "a"))
        self.assertEqual((properties[1]["generated_kind"], properties[1]["generation_expression"]), ("s", "(a * 2)"))
        self.assertEqual(properties[0]["provenance"], "pg_catalog")
        self.assertEqual(pg.objects[("ts_custom", "items")]["resource_group"], "other")
        self.assertEqual((result["version"], result["checksum"]), (0, ""))

    async def test_internal_char_empty_null_bytes_are_normalized_before_text_writes(self):
        for empty in (b"\x00", bytearray(b"\x00"), memoryview(b"\x00")):
            pg = MetadataPg()
            pg.catalog[("ts_custom", "items")]["partstrat"] = b"\x00"
            pg.columns[100][0].update(identity_kind=empty, generated_kind=empty)
            self.assertEqual(ontology._catalog_text(empty), "")
            result = await self.sync(pg, ddl_sql="CREATE TABLE ts_custom.items (id bigint);")
            self.assertEqual(pg.properties[result["id"]][0]["identity_kind"], "")
            self.assertEqual(pg.properties[result["id"]][0]["generated_kind"], "")
            self.assertEqual(pg.objects[("ts_custom", "items")]["partition_type"], "none")
            metadata_sql = "\n".join(sql for sql, _ in pg.calls)
            self.assertIn("p.partstrat::text AS partstrat", metadata_sql)
            self.assertIn("a.attidentity::text AS identity_kind", metadata_sql)
            self.assertIn("a.attgenerated::text AS generated_kind", metadata_sql)

    async def test_defaults_recomputed_on_change_removal_and_non_literal_expression(self):
        pg = MetadataPg()
        field = property_row("data_type", default_expression="'organization'::text")
        pg.columns[100] = [field]
        await self.sync(pg)
        record = pg.objects[("ts_custom", "items")]
        self.assertEqual((record["data_type"], record["data_type_source"]), ("organization", "column_default"))
        for expression, expected in [("'filing'::text", "filing"), (None, ""), ("lower('filing'::text)", ""), ("nextval('x'::regclass)", "")]:
            field["default_expression"] = expression
            await self.sync(pg)
            record = pg.objects[("ts_custom", "items")]
            self.assertEqual(record["data_type"], expected)
            self.assertEqual(record["data_type_source"], "column_default" if expected else "not_configured")

    async def test_explicit_type_and_description_are_distinguished_from_physical_metadata(self):
        pg = MetadataPg()
        await self.sync(pg, data_type="organization", description="Declared meaning")
        await self.sync(pg)
        record = pg.objects[("ts_custom", "items")]
        self.assertEqual((record["data_type"], record["description"]), ("organization", "Declared meaning"))
        self.assertEqual((record["data_type_source"], record["description_source"]), ("registration", "registration"))
        pg.catalog[("ts_custom", "items")]["comment"] = "Actual table comment"
        await self.sync(pg)
        self.assertEqual(pg.objects[("ts_custom", "items")]["description_source"], "table_comment")
        pg.catalog[("ts_custom", "items")]["comment"] = None
        await self.sync(pg)
        self.assertEqual(pg.objects[("ts_custom", "items")]["description"], "")
        self.assertEqual(pg.objects[("ts_custom", "items")]["description_source"], "not_configured")

    async def test_metadata_only_sync_preserves_ddl_version_checksum_and_layout(self):
        pg = MetadataPg()
        await self.sync(pg, ddl_sql="CREATE TABLE ts_custom.items (id bigint);")
        record = pg.objects[("ts_custom", "items")]
        managed = {key: record[key] for key in ("ddl_sql", "version", "checksum", "partition_type", "partition_column", "partition_granularity", "partition_count")}
        pg.columns[100].append(property_row("note", sql_type="text", ordinal_position=2,
            is_nullable=True, is_primary_key=False, primary_key_position=None, identity_kind=b""))
        await self.sync(pg)
        record = pg.objects[("ts_custom", "items")]
        self.assertEqual({key: record[key] for key in managed}, managed)
        self.assertEqual(len(pg.properties[record["id"]]), 2)

    async def test_ui_ddl_version_changes_once_and_uses_real_partition_layout(self):
        pg = MetadataPg()
        pg.catalog[("ts_custom", "items")].update(partstrat=b"h", partition_fields=["id"], partition_count=8)
        ddl = "CREATE TABLE ts_custom.items (id bigint) PARTITION BY HASH(id);"
        first = await self.sync(pg, ddl_sql=ddl)
        second = await self.sync(pg, ddl_sql=ddl)
        third = await self.sync(pg, ddl_sql=ddl + "\nCOMMENT ON TABLE ts_custom.items IS 'Items';")
        self.assertEqual([first["version"], second["version"], third["version"]], [1, 1, 2])
        self.assertEqual(first["checksum"], ddl_checksum(ddl, table_role="current", partition_type="hash",
            partition_column="id", partition_granularity=None, partition_count=8))

    async def test_property_failure_rolls_back_objects_and_callers_ddl_transaction(self):
        pg = MetadataPg(physical=False)
        pg.fail_properties = True
        with self.assertRaisesRegex(RuntimeError, "property insertion"):
            async with pg.session() as session:
                # Stand-in for the caller's transactional CREATE; no real database is used.
                pg.catalog[("ts_custom", "items")] = {"oid": 100, "comment": None,
                    "partstrat": None, "partition_fields": [], "partition_count": 0}
                await ontology.sync_object_metadata(
                    session, "ts_custom", "items", catalog_session=session,
                    layer="custom", table_role="current", ddl_sql="CREATE TABLE ts_custom.items (id bigint);",
                )
        self.assertEqual((pg.catalog, pg.objects, pg.properties), ({}, {}, {}))
        self.assertEqual((pg.commits, pg.rollbacks), (0, 1))

    async def test_property_refresh_failure_restores_previous_properties(self):
        pg = MetadataPg()
        await self.sync(pg)
        before = deepcopy((pg.objects, pg.properties))
        pg.fail_properties = True
        pg.columns[100][0]["comment"] = "Changed"
        with self.assertRaises(RuntimeError):
            await self.sync(pg)
        self.assertEqual((pg.objects, pg.properties), before)

    async def test_invalid_or_missing_objects_do_not_write(self):
        for schema, table in [("app", "x"), ("ts_meta", "x"), ("ts_meta_hist", "x"), ("ts_ods;drop", "x"), ("ts_ods", 'x"')]:
            pg = MetadataPg(physical=False)
            with self.assertRaises(ValueError):
                await ontology.sync_object_metadata(pg, schema, table, catalog_session=pg)
            self.assertEqual(pg.calls, [])
        pg = MetadataPg(physical=False)
        self.assertIsNone(await ontology.sync_object_metadata(
            pg, "ts_custom", "missing", catalog_session=pg, allow_missing=True,
        ))
        with self.assertRaises(ValueError):
            await ontology.sync_object_metadata(pg, "ts_custom", "missing", catalog_session=pg)
        self.assertEqual(pg.objects, {})

    async def test_unsupported_partition_layout_cannot_store_incorrect_checksum(self):
        for strategy, fields, count in [(b"l", ["id"], 1), (b"h", [], 8), (b"h", ["id"], 0), (b"r", ["id"], 2)]:
            pg = MetadataPg()
            pg.catalog[("ts_custom", "items")].update(partstrat=strategy, partition_fields=fields, partition_count=count)
            with self.assertRaises(ValueError):
                await self.sync(pg, ddl_sql="CREATE TABLE ts_custom.items (id bigint);")
            self.assertEqual(pg.objects, {})

    async def test_explicit_uncreated_binding_stores_declared_columns(self):
        pg = MetadataPg("warehouse", "business_orgs", physical=False)
        record = pg.register("warehouse", "business_orgs", layer="ods", data_type="organization")
        ddl = "CREATE TABLE warehouse.business_orgs (record_id text);"
        first = await registry.save_registered_ddl(pg, layer="ods", table="organization", ddl_sql=ddl, catalog_pg=pg)
        self.assertEqual(record["data_type_source"], "registration")
        self.assertEqual(record["data_type"], "organization")
        expected = TABLE_DEFINITIONS["ods"]["organization"]
        self.assertEqual(len(pg.properties[record["id"]]), len(expected["columns"]))
        self.assertTrue(all(row["provenance"] == "table_definition" for row in pg.properties[record["id"]]))
        actual_keys = sorted((row["primary_key_position"], row["name"]) for row in pg.properties[record["id"]] if row["is_primary_key"])
        self.assertEqual([name for _, name in actual_keys], list(expected["primary_key"]))
        second = await registry.save_registered_ddl(pg, layer="ods", table="organization", ddl_sql=ddl, catalog_pg=pg)
        self.assertEqual((first["changed"], second["changed"], second["version"]), (True, False, 1))
        self.assertEqual(pg.lock_key, "ontology:ods:current:warehouse:business_orgs")

    async def test_unknown_or_history_declarations_are_not_guessed(self):
        pg = MetadataPg(physical=False)
        self.assertFalse(await ontology.sync_declared_object_properties(pg, 1, "ods", "unknown"))
        self.assertFalse(await ontology.sync_declared_object_properties(pg, 1, "ods", "organization", "history"))
        self.assertEqual(pg.calls, [])

    async def test_legacy_physical_names_do_not_create_semantic_bindings(self):
        pg = MetadataPg("ts_rds", "rds_organization")
        await ontology.sync_object_metadata(pg, "ts_rds", "rds_organization", catalog_session=pg,
                                             layer="rds", table_role="current")
        self.assertEqual(pg.objects[("ts_rds", "rds_organization")]["data_type"], "")
        with self.assertRaisesRegex(ValueError, "No active Ontology binding"):
            await registry.save_registered_ddl(pg, layer="rds", table="organization", catalog_pg=pg,
                                               ddl_sql="CREATE TABLE ts_rds.rds_organization (id bigint);")
        self.assertEqual(pg.objects[("ts_rds", "rds_organization")]["ddl_sql"], "")

    async def test_history_keeps_independent_identity_and_uses_real_history_properties(self):
        pg = MetadataPg("warehouse", "old_org_versions")
        pg.register("warehouse", "old_org_versions", layer="ods", data_type="organization", role="history")
        ddl = "CREATE TABLE warehouse.old_org_versions (id bigint);"
        await registry.save_registered_ddl(pg, layer="ods", table="organization", table_role="history",
                                           ddl_sql=ddl, partition_type="none", catalog_pg=pg)
        record = pg.objects[("warehouse", "old_org_versions")]
        self.assertEqual((record["table_role"], record["resource_group"], record["data_type"]), ("history", "ingestion", "organization"))
        self.assertEqual([row["name"] for row in pg.properties[record["id"]]], ["id"])
        self.assertEqual(await registry.get_registered_ddl(pg, "ods", "organization", "history"), ddl)
        self.assertIsNone(await registry.get_registered_ddl(pg, "ods", "organization", "current"))

    async def test_registry_save_is_atomic_and_review_rejection_preserves_record(self):
        pg = MetadataPg(physical=False)
        catalog = MetadataPg()
        pg.register("ts_custom", "items", layer="custom", data_type="items")
        base_ddl = "CREATE TABLE ts_custom.items (id bigint);"
        next_ddl = "CREATE TABLE ts_custom.items (id bigint, body text);"
        await registry.save_registered_ddl(pg, layer="custom", table="items", ddl_sql=base_ddl, catalog_pg=catalog)
        before = deepcopy((pg.objects, pg.properties))
        with self.assertRaisesRegex(ValueError, "explicit review"):
            await registry.save_registered_ddl(pg, layer="custom", table="items", ddl_sql=next_ddl, catalog_pg=catalog, allow_update=False)
        self.assertEqual((pg.objects, pg.properties), before)
        pg.fail_properties = True
        with self.assertRaises(RuntimeError):
            await registry.save_registered_ddl(pg, layer="custom", table="items", ddl_sql=next_ddl, catalog_pg=catalog)
        self.assertEqual((pg.objects, pg.properties), before)
        self.assertEqual(pg.rollbacks, 2)

    async def test_catalog_only_record_accepts_first_managed_ddl_without_review(self):
        pg = MetadataPg("warehouse", "registered_items")
        pg.register("warehouse", "registered_items", layer="custom", data_type="items")
        result = await registry.save_registered_ddl(pg, layer="custom", table="items", catalog_pg=pg,
            ddl_sql="CREATE TABLE warehouse.registered_items (id bigint);", allow_update=False)
        self.assertEqual((len(pg.objects), result["version"], result["changed"]), (1, 1, True))

    async def test_reads_never_bootstrap_or_return_catalog_only_and_inactive_rows(self):
        pg = MetadataPg()
        pg.register("ts_custom", "items", layer="custom", data_type="items")
        self.assertIsNone(await registry.get_registered_ddl(pg, "custom", "items"))
        self.assertIsNone(await registry.get_registered_ddl_record(pg, "custom", "items"))
        self.assertEqual(await registry.list_registered_ddl_records(pg), [])
        ddl = "CREATE TABLE ts_custom.items (id bigint);"
        await registry.save_registered_ddl(pg, layer="custom", table="items", ddl_sql=ddl, catalog_pg=pg)
        self.assertEqual(await registry.get_registered_ddl(pg, "custom", "items"), ddl)
        pg.objects[("ts_custom", "items")]["is_active"] = False
        self.assertIsNone(await registry.get_registered_ddl(pg, "custom", "items"))
        self.assertEqual(await registry.list_registered_ddl_records(pg), [])
        self.assertEqual(pg.bootstrap_count, 0)
        self.assertFalse(any(sql.startswith("CREATE") for sql, _ in pg.calls))

    async def test_startup_checks_registry_without_seeding_objects(self):
        pg = MetadataPg()
        pg.register("ts_custom", "items", layer="custom", data_type="items")
        before = deepcopy(pg.objects)
        await registry.ensure_ontology_tables(pg)
        self.assertEqual(pg.objects, before)
        self.assertEqual(pg.bootstrap_count, 0)

    async def test_legacy_binding_backfill_preserves_ids_and_stops_on_ambiguity(self):
        known = {"id": 14, "layer": "rds", "table_role": "current", "data_type": "",
                 "schema_name": "ts_rds", "table_name": "rds_patent", "updated_by": "system:rds",
                 "ddl_sql": "CREATE TABLE ts_rds.rds_patent (id BIGINT)"}
        ordinary = {**known, "id": 15, "schema_name": "ordinary", "table_name": "rows",
                    "updated_by": "user:42", "ddl_sql": ""}
        required = [{**known, "id": index, "layer": layer, "data_type": kind}
                    for index, ((layer, _, _), (kind, _)) in enumerate(
                        ontology_etl_bindings._FIXED_LEGACY.items(), start=100)]
        rows = [known, ordinary, *required]
        updates = []

        class Metadata:
            @asynccontextmanager
            async def session(self):
                yield self

            async def execute(self, query, params=None):
                sql = str(query)
                if "SELECT id, layer" in sql:
                    return Result(rows)
                if "UPDATE app.ontology_objects" in sql:
                    updates.append(params)
                    return Result(scalar=params["id"])
                return Result()

        warehouse = MetadataPg()
        warehouse.close = AsyncMock()
        warehouse.fetch_all = AsyncMock(return_value=[
            {"name": name} for name in ontology_etl_bindings._REQUIRED_FIELDS["rds"]
        ])
        with patch.object(ontology_etl_bindings, "get_layer_pg_client", return_value=warehouse):
            preview = await ontology_etl_bindings.backfill(Metadata())
            self.assertEqual([row["id"] for row in preview["updates"]], [14])
            self.assertEqual(updates, [])
            self.assertTrue((await ontology_etl_bindings.backfill(Metadata(), apply=True))["applied"])
            self.assertEqual([row["id"] for row in updates], [14])
            rows.append({**known, "id": 22, "data_type": "patent", "schema_name": "active"})
            blocked = await ontology_etl_bindings.backfill(Metadata(), apply=True)
        self.assertTrue(blocked["blocked"])
        self.assertEqual(blocked["conflicts"],
                         [{"layer": "rds", "table_role": "current", "data_type": "patent", "count": 2}])
        self.assertEqual([row["id"] for row in updates], [14])

    def test_new_ddl_has_unique_physical_identity_and_valid_group_constraint(self):
        self.assertIn("(layer, table_role, schema_name, table_name)", "\n".join(ontology.ONTOLOGY_INDEX_DDL_STATEMENTS))
        self.assertIn("REFERENCES app.ontology_objects(id) ON DELETE CASCADE", ontology.ONTOLOGY_PROPERTIES_DDL)
        self.assertIn("ontology_objects_resource_group_check", ontology.ONTOLOGY_OBJECTS_DDL)
        self.assertIn("CHECK (resource_group IN", "\n".join(ontology.ONTOLOGY_OBJECT_COLUMNS_ADD_STATEMENTS))
        self.assertEqual({ontology.resource_group(layer) for layer in ["ods", "task", "ads", "unknown"]}, {"ingestion", "processing", "serving", "other"})
        source = Path(ontology.__file__).read_text(encoding="utf-8") + Path(registry.__file__).read_text(encoding="utf-8")
        self.assertNotIn("ddlregistry", source)


if __name__ == "__main__":
    unittest.main()
