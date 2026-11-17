"""Read-only object catalog contracts with local PostgreSQL catalog fixtures; no database/network."""

import unittest
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.web.api.dependencies.common import get_current_user
from app.web.api.routes import etl
from app.web.services.etl_layers import EtlLayers


class Result:
    def __init__(self, rows=(), scalar=None):
        self.rows, self.scalar = rows, scalar

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None

    def scalar_one_or_none(self):
        return self.scalar

    def scalar_one(self):
        return self.scalar


class CatalogSession:
    def __init__(self, *results, ontology_installed=True):
        self.results = list(results)
        self.calls = []
        self.allowed_prefixes = ("SELECT",)
        self.ontology_installed = ontology_installed

    async def execute(self, query, params=None):
        sql = str(query)
        self.calls.append((sql, deepcopy(params)))
        if not sql.lstrip().startswith(self.allowed_prefixes):
            raise AssertionError("The object metadata endpoints must only issue SELECT")
        if "to_regclass" in sql and "public.ontology_objects" in sql:
            return Result(scalar=self.ontology_installed)
        if not self.results:
            raise AssertionError("Unexpected catalog query: " + sql)
        return self.results.pop(0)


def service(*results, ontology_installed=True):
    instance = object.__new__(EtlLayers)
    session = CatalogSession(*results, ontology_installed=ontology_installed)

    @asynccontextmanager
    async def context():
        yield session

    instance.session = context
    return instance, session


def summary(schema="ts_ods", table="objects", **changes):
    return {"oid": 101, "schema": schema, "name": table, "description": "Actual PG table comment",
            "data_type": "catalog classification", "object_count": 23, "properties_count": 3, "visibility": "normal", **changes}


def column(name, type="text", primary=False, position=None, nullable=True, comment=None):
    return {"name": name, "type": type, "primary": primary, "primaryPosition": position,
            "nullable": nullable, "comment": comment}


def foreign(name="fk_owner", source_schema="ts_ods", source_table="objects",
            target_schema="ts_rds", target_table="owners", columns=None, referenced=None, **changes):
    return {"name": name, "source_schema": source_schema, "source_table": source_table,
            "target_schema": target_schema, "target_table": target_table,
            "columns": columns or ["owner_id"], "referenced_columns": referenced or ["id"],
            "source_visibility": "normal", "target_visibility": "normal", **changes}


class ObjectCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_pagination_and_facets_are_real_metadata_counts(self):
        instance, session = service(Result([
            {"schema": "ts_ods", "resource_group": "ingestion", "count": 5},
            {"schema": "ts_custom", "resource_group": "other", "count": 3},
        ]), Result([summary()]))
        result = await instance.object_types(limit=1, offset=2)
        self.assertEqual((result["total"], result["limit"], result["offset"]), (8, 1, 2))
        self.assertEqual(result["schemaCounts"], {"ts_ods": 5, "ts_custom": 3})
        self.assertEqual(result["groupCounts"], {"ingestion": 5, "processing": 0, "serving": 0, "other": 3})
        self.assertEqual(result["groupSchemaCounts"], {
            "ingestion": {"ts_ods": 5}, "processing": {}, "serving": {}, "other": {"ts_custom": 3},
        })
        item = result["items"][0]
        self.assertEqual(item["id"], "ts_ods.objects")
        self.assertEqual(item["displayName"], "objects")
        self.assertEqual(item["description"], "Actual PG table comment")
        self.assertEqual(item["objectCount"], 23)
        self.assertTrue(item["objectCountEstimated"])
        self.assertEqual(item["propertiesCount"], 3)
        self.assertIsNone(item["titleProperty"])
        self.assertEqual(item["visibility"], "normal")
        self.assertNotIn("oid", item)
        sql, params = session.calls[-1]
        self.assertEqual((params["limit"], params["offset"]), (1, 2))
        self.assertIn("pg_stat_get_live_tuples", sql)
        self.assertIn("WITH RECURSIVE descendants", sql)
        self.assertNotIn("SELECT t.*", sql)

    async def test_schema_search_ids_are_bound_and_group_does_not_change_facets(self):
        instance, session = service(Result([
            {"schema": "ts_ods_hist", "resource_group": "ingestion", "count": 4},
            {"schema": "ts_custom", "resource_group": "other", "count": 2},
        ]), Result([summary("ts_ods_hist", data_type=None)]))
        needle = "O'Reilly %_ ; DROP TABLE things"
        result = await instance.object_types("ods_hist", needle, "ingestion", 1, 0,
                                             "ts_ods_hist.objects,ts_custom.second")
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["groupCounts"]["other"], 0)
        self.assertEqual(result["schemaCounts"], {"ts_ods_hist": 4})
        self.assertEqual(result["groupSchemaCounts"]["other"], {"ts_custom": 2})
        self.assertEqual(result["items"][0]["dataType"], "")
        count_sql, params = next((sql, params) for sql, params in session.calls if params and "search" in params and "count(*)" in sql)
        self.assertNotIn(needle, count_sql)
        self.assertEqual(params["search"], needle)
        self.assertEqual(params["schema"], "ts_ods_hist")
        self.assertEqual(params["ids"], ["ts_ods_hist.objects", "ts_custom.second"])
        self.assertNotIn("group", params)
        self.assertNotIn("n.nspname = :schema", count_sql)
        self.assertIn("n.nspname = :schema", session.calls[-1][0])
        self.assertEqual(session.calls[-1][1]["group"], "ingestion")
        self.assertIn("o.data_type", session.calls[-1][0])

    async def test_empty_or_out_of_range_page_retains_counts_without_fetching_rows(self):
        instance, session = service(Result([{"schema": "ts_task", "resource_group": "processing", "count": 2}]))
        result = await instance.object_types(offset=2)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 2)
        self.assertEqual(sum("count(*) AS count" in sql for sql, _ in session.calls), 1)

    async def test_schema_navigation_facets_keep_other_schemas_when_current_group_is_empty(self):
        instance, session = service(Result([
            {"schema": "ts_ods", "resource_group": "ingestion", "count": 5},
            {"schema": "ts_dim", "resource_group": "serving", "count": 2},
            {"schema": "ts_custom", "resource_group": "ingestion", "count": 3},
        ]))
        result = await instance.object_types(schema="ts_ods", group="serving", limit=1)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["schemaCounts"], {"ts_ods": 5})
        self.assertEqual(result["groupCounts"], {"ingestion": 5, "processing": 0, "serving": 0, "other": 0})
        self.assertEqual(result["groupSchemaCounts"]["ingestion"], {"ts_ods": 5, "ts_custom": 3})
        self.assertEqual(result["groupSchemaCounts"]["serving"], {"ts_dim": 2})
        self.assertEqual(sum("count(*) AS count" in sql for sql, _ in session.calls), 1)

    async def test_catalog_and_preview_exclude_internal_schemas_and_all_child_tables(self):
        instance, session = service(Result([{ "schema": "ts_ods", "resource_group": "ingestion", "count": 1}]),
                                    Result([summary()]))
        await instance.object_types()
        scoped = [sql for sql, _ in session.calls if "FROM pg_class c" in sql]
        self.assertEqual(len(scoped), 2)
        for sql in scoped:
            self.assertIn("n.nspname <> 'ts_meta'", sql)
            self.assertIn("n.nspname <> 'ts_meta_hist'", sql)
            self.assertIn("n.nspname ~ '^ts_[a-z0-9_]+$'", sql)
            self.assertIn("NOT c.relispartition", sql)
            self.assertIn("child.inhrelid = c.oid", sql)

    async def test_invalid_schema_identifier_group_and_pagination_fail_before_querying(self):
        instance, session = service()
        inputs = [({"schema": "ts_meta"}, 404), ({"schema": "ts_meta_hist"}, 404),
                  ({"schema": "ods; drop table x"}, 400),
                  ({"group": "invented"}, 400), ({"limit": 0}, 400), ({"limit": 501}, 400),
                  ({"offset": -1}, 400), ({"offset": 1_000_001}, 400),
                  ({"ids": "public.objects"}, 400), ({"ids": "ts_ods.bad;query"}, 400),
                  ({"ids": ",".join(f"ts_ods.t{i}" for i in range(201))}, 400),
                  ({"search": "x" * 1001}, 400)]
        for kwargs, code in inputs:
            with self.subTest(kwargs=kwargs), self.assertRaises(HTTPException) as error:
                await instance.object_types(**kwargs)
            self.assertEqual(error.exception.status_code, code)
        self.assertEqual(session.calls, [])

    async def test_preview_properties_comments_types_and_composite_pk_use_catalog_truth(self):
        fields = [column("id", "bigint", True, 2, False, "Record identifier"),
                  column("tenant", "uuid", True, 1, False),
                  column("name", "character varying(80)", comment="Display text only")]
        instance, session = service(Result([summary()]), Result([]), Result(scalar=True), Result([]), Result(fields))
        result = await instance.preview("ods", "objects")
        self.assertEqual(result["primaryKey"], ["tenant", "id"])
        self.assertIsNone(result["titleProperty"])
        self.assertEqual(result["properties"][0], {"name": "id", "displayName": "id", "type": "bigint",
                         "nullable": False, "primaryKey": True, "comment": "Record identifier"})
        self.assertEqual(result["properties"][1]["comment"], "")
        self.assertEqual(result["properties"][2]["type"], "character varying(80)")
        self.assertIn("col_description", session.calls[-1][0])
        self.assertEqual(result["linkedObjectTypes"], [])

    async def test_linked_types_deduplicate_tables_keep_constraints_and_direction_and_column_order(self):
        links = [foreign(columns=["owner_a", "owner_b"], referenced=["key_b", "key_a"]),
                 foreign("fk_second"), foreign("fk_incoming", "ts_rds", "owners", "ts_ods", "objects"),
                 foreign("fk_self", "ts_ods", "objects", "ts_ods", "objects")]
        instance, _ = service(Result([summary(properties_count=0)]), Result(links), Result(scalar=True), Result([]))
        result = await instance.preview("ts_ods", "objects")
        linked = {item["id"]: item for item in result["linkedObjectTypes"]}
        self.assertEqual(len(linked), 2)
        owners = linked["ts_rds.owners"]
        self.assertEqual(owners["direction"], "both")
        self.assertEqual(len(owners["constraints"]), 3)
        self.assertEqual(owners["constraints"][0]["columns"], ["owner_a", "owner_b"])
        self.assertEqual(owners["constraints"][0]["referencedColumns"], ["key_b", "key_a"])
        self.assertEqual(linked["ts_ods.objects"]["direction"], "both")
        self.assertEqual([item["direction"] for item in linked["ts_ods.objects"]["constraints"]], ["outgoing", "incoming"])

    async def test_incoming_and_outgoing_fk_never_expose_non_ts_or_metadata_schema(self):
        links = [foreign("public_in", "public", "private", "ts_ods", "objects"),
                 foreign("meta_out", target_schema="ts_meta"),
                 foreign("meta_history_out", target_schema="ts_meta_hist"),
                 foreign("system_out", target_schema="pg_catalog"), foreign()]
        instance, session = service(Result([summary(properties_count=0)]), Result(links), Result(scalar=True), Result([]))
        result = await instance.preview("ts_ods", "objects")
        self.assertEqual([item["id"] for item in result["linkedObjectTypes"]], ["ts_rds.owners"])
        sql = next(sql for sql, _ in session.calls if "FROM pg_constraint" in sql)
        for side, table in (("sn", "sc"), ("tn", "tc")):
            self.assertIn(f"{side}.nspname <> 'ts_meta'", sql)
            self.assertIn(f"{side}.nspname <> 'ts_meta_hist'", sql)
            self.assertIn(f"{side}.nspname ~ '^ts_[a-z0-9_]+$'", sql)
            self.assertIn(f"NOT {table}.relispartition", sql)
            self.assertIn(f"child.inhrelid = {table}.oid", sql)
        self.assertIn("con.conparentid = 0", sql)

    async def test_missing_or_partition_child_table_returns_404_and_does_not_query_business_data(self):
        instance, session = service(Result([]))
        with self.assertRaises(HTTPException) as error:
            await instance.preview("ts_ods", "missing")
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(sum("FROM pg_class c" in sql for sql, _ in session.calls), 1)
        self.assertTrue(all('"ts_ods"."missing"' not in sql for sql, _ in session.calls))

    async def test_zero_property_table_is_valid_and_does_not_invent_a_title_or_primary_key(self):
        instance, session = service(Result([summary(properties_count=0)]), Result([]), Result(scalar=True), Result([]))
        result = await instance.preview("ts_ods", "objects")
        self.assertEqual(result["properties"], [])
        self.assertEqual(result["primaryKey"], [])
        self.assertEqual(sum("FROM pg_attribute a JOIN pg_class c" in sql for sql, _ in session.calls), 0)

    async def test_same_schema_metadata_groups_accumulate_facets_and_description_search(self):
        instance, session = service(Result([
            {"schema": "ts_custom", "resource_group": "ingestion", "count": 2},
            {"schema": "ts_custom", "resource_group": "serving", "count": 3},
        ]), Result([summary("ts_custom", description="Persisted metadata description",
                            resource_group="serving", data_type="organization",
                            data_type_source="registry", description_source="registry")]))
        result = await instance.object_types(search="metadata description", group="serving")
        self.assertEqual(result["schemaCounts"], {"ts_custom": 5})
        self.assertEqual(result["groupCounts"], {"ingestion": 2, "processing": 0, "serving": 3, "other": 0})
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["groupSchemaCounts"]["ingestion"], {"ts_custom": 2})
        self.assertEqual(result["groupSchemaCounts"]["serving"], {"ts_custom": 3})
        item = result["items"][0]
        self.assertEqual((item["description"], item["dataType"], item["resourceGroup"]),
                         ("Persisted metadata description", "organization", "serving"))
        self.assertEqual(item["descriptionSource"], "registry")
        queries = [(sql, params) for sql, params in session.calls if params and "search" in params]
        self.assertTrue(all("o.description" in sql for sql, _ in queries))
        self.assertTrue(all(params["search"] == "metadata description" for _, params in queries))
        self.assertNotIn("metadata description", queries[0][0])

    async def test_missing_ontology_is_503_for_preview_empty_and_out_of_range_catalog_without_writes(self):
        for operation in ("preview", "empty", "out_of_range"):
            counts = [] if operation != "out_of_range" else [
                {"schema": "ts_ods", "resource_group": "ingestion", "count": 1}]
            instance, session = service(Result(counts), ontology_installed=False)
            with self.subTest(operation=operation):
                with self.assertRaises(HTTPException) as error:
                    if operation == "preview":
                        await instance.preview("ods", "objects")
                    else:
                        await instance.object_types(offset=1 if operation == "out_of_range" else 0)
                self.assertEqual(error.exception.status_code, 503)
                self.assertIn("not initialized", error.exception.detail)
                self.assertTrue(all(sql.lstrip().startswith("SELECT") for sql, _ in session.calls))

    async def test_fk_peers_use_persisted_data_type_in_both_directions(self):
        links = [foreign(source_data_type="organization", target_data_type="company"),
                 foreign("fk_incoming", "ts_rds", "owners", "ts_ods", "objects",
                         source_data_type="company", target_data_type="organization")]
        instance, session = service(Result([summary(properties_count=0, data_type="organization")]),
                                    Result(links), Result(scalar=True), Result([]))
        result = await instance.preview("ods", "objects")
        self.assertEqual(result["dataType"], "organization")
        self.assertEqual(result["linkedObjectTypes"][0]["dataType"], "company")
        self.assertEqual(result["linkedObjectTypes"][0]["direction"], "both")
        self.assertEqual(len(result["linkedObjectTypes"][0]["constraints"]), 2)
        self.assertTrue(any("source_data_type" in sql and "target_data_type" in sql for sql, _ in session.calls))

    async def test_persisted_properties_preserve_ordinal_order_and_composite_primary_key_order(self):
        fields = [column("id", "bigint", True, 2, False, "Metadata identifier"),
                  column("tenant", "uuid", True, 1, False), column("name", comment="Metadata label")]
        instance, session = service(Result([summary(object_id=777)]), Result([]),
                                    Result(scalar=True), Result([]), Result(fields))
        instance.columns = AsyncMock(side_effect=AssertionError("No PG fallback for a registered object"))
        result = await instance.preview("ods", "objects")
        self.assertEqual([field["name"] for field in result["properties"]], ["id", "tenant", "name"])
        self.assertEqual(result["primaryKey"], ["tenant", "id"])
        self.assertEqual(result["properties"][0]["comment"], "Metadata identifier")
        self.assertEqual(session.calls[-1][1], {"object_id": 777, "oid": 101})
        self.assertIn("p.sql_type = format_type", session.calls[-1][0])
        self.assertIn("a.attname = p.name", session.calls[-1][0])
        self.assertIn("ontology_object_properties", session.calls[-1][0])
        self.assertIn("ORDER BY p.ordinal_position", session.calls[-1][0])
        instance.columns.assert_not_awaited()

    async def test_stale_registered_properties_report_conflict_instead_of_falling_back(self):
        instance, _ = service(Result([summary(object_id=777)]), Result([]),
                              Result(scalar=True), Result([]), Result([column("id")]))
        instance.columns = AsyncMock(side_effect=AssertionError("Do not hide stale metadata"))
        with self.assertRaises(HTTPException) as error:
            await instance.preview("ods", "objects")
        self.assertEqual(error.exception.status_code, 409)
        instance.columns.assert_not_awaited()

    async def test_existing_data_contract_remains_unchanged_after_column_comments_are_added(self):
        fields = [column("id", "bigint", True, 1, False, "Identifier comment"), column("name")]
        instance, session = service(Result(fields), Result(scalar=[{"Plan": {"Plan Rows": 25}}]),
                                    Result([{"id": 1, "name": "Fixture row"}]))
        session.allowed_prefixes = ("SELECT", "EXPLAIN")
        result = await instance.data("ods", "objects", {"limit": 1})
        self.assertEqual(result["columns"], ["id", "name"])
        self.assertEqual(result["columnTypes"], {"id": "bigint", "name": "text"})
        self.assertEqual(result["primaryKey"], ["id"])
        self.assertEqual(result["rows"], [{"id": 1, "name": "Fixture row"}])
        self.assertEqual(result["total"], 25)
        self.assertTrue(result["totalEstimated"])
        self.assertEqual(result["rowCount"], 1)
        self.assertNotIn("comment", result)
        self.assertNotIn("hasMore", result)
        self.assertNotIn("ORDER BY", session.calls[-1][0])
        self.assertEqual(session.calls[-1][1]["limit"], 1)

    async def test_legacy_range_keeps_its_count_and_does_not_append_primary_keys(self):
        fields = [column("id", "bigint", True, 1, False), column("name")]
        instance, session = service(Result(fields), Result(scalar=2), Result([{"id": 12}]))
        result = await instance.data("ods", "objects", {"limit": 1}, scope={
            "filters": [{"field": "name", "operator": "=", "value": "Scoped"}],
            "sorts": [{"field": "name", "direction": "desc"}],
        })
        self.assertEqual((result["total"], result["totalEstimated"], result["rowCount"]), (2, False, 1))
        self.assertNotIn("hasMore", result)
        self.assertIn("count(*)", session.calls[-2][0])
        sql, params = session.calls[-1]
        self.assertIn('t."name" = (jsonb_populate_record', sql)
        self.assertIn('ORDER BY t."name" desc LIMIT', sql)
        self.assertEqual(params["limit"], 1)

    async def test_preview_rows_share_filters_and_fetch_one_extra_row_without_counting(self):
        fields = [column("id", "bigint", True, 2, False), column("tenant", "uuid", True, 1, False), column("name")]
        rows = [{"id": 15}, {"id": 10}]
        instance, session = service(Result(fields), Result(rows))
        result = await instance.data("ods", "objects", {
            "limit": 1, "offset": 2000, "search": "term",
            "filters": [{"field": "name", "operator": "=", "value": "filter value"}],
            "sorts": [{"field": "name", "direction": "desc"}, {"field": "id", "direction": "desc"}],
        }, preview_rows=True)
        self.assertEqual(result["rows"], rows[:1])
        self.assertEqual(result["rowCount"], 1)
        self.assertEqual(result["primaryKey"], ["tenant", "id"])
        self.assertTrue(result["stableOrder"])
        self.assertTrue(result["hasMore"])
        self.assertFalse(result["canPageFurther"])
        self.assertNotIn("total", result)
        self.assertNotIn("totalEstimated", result)
        self.assertEqual(len(session.calls), 2)  # Catalog columns and exactly one business-row SELECT.
        sql, params = session.calls[-1]
        self.assertIn('ORDER BY t."name" desc, t."id" desc, t."tenant" asc', sql)
        self.assertIn('t."name" = (jsonb_populate_record', sql)
        self.assertIn("row_to_json", sql)
        self.assertNotIn("count(", sql.lower())
        self.assertNotIn("explain", sql.lower())
        self.assertNotIn("filter value", sql)
        self.assertEqual(params["limit"], 2)
        self.assertEqual(params["offset"], 2000)
        self.assertEqual(params["p2"], "%term%")

    async def test_preview_rows_default_order_and_partition_are_respected(self):
        fields = [column("id", "bigint", True, 2, False), column("tenant", "uuid", True, 1, False)]
        partitions = [{"name": "objects_p0", "schema": "ts_ods"}]
        instance, session = service(Result(fields), Result(fields), Result(partitions), Result([{"id": 1}, {"id": 2}]))
        session.allowed_prefixes = ("SELECT", "WITH")
        result = await instance.data("ods", "objects", {
            "limit": 1, "offset": 1999, "partition": "objects_p0",
        }, preview_rows=True)
        self.assertEqual(len(session.calls), 4)
        self.assertIn('FROM "ts_ods"."objects_p0" t ORDER BY t."tenant" asc, t."id" asc',
                      session.calls[-1][0])
        self.assertTrue(result["hasMore"])
        self.assertTrue(result["canPageFurther"])
        self.assertTrue(result["stableOrder"])

    async def test_preview_rows_without_primary_key_expose_unstable_order_and_actual_end(self):
        instance, session = service(Result([column("name")]), Result([{"name": "last row"}]))
        result = await instance.data("ods", "objects", {"limit": 1}, preview_rows=True)
        self.assertEqual(result["primaryKey"], [])
        self.assertFalse(result["stableOrder"])
        self.assertFalse(result["hasMore"])
        self.assertFalse(result["canPageFurther"])
        self.assertNotIn("ORDER BY", session.calls[-1][0])

    async def test_existing_partition_contract_remains_unchanged_after_column_comments_are_added(self):
        partitions = [{"name": "objects_p0", "schema": "ts_ods", "bounds": "FOR VALUES WITH (modulus 2, remainder 0)"}]
        instance, session = service(Result([column("id", comment="Identifier comment")]), Result(partitions))
        session.allowed_prefixes = ("SELECT", "WITH")
        self.assertEqual(await instance.partitions("ods", "objects"), partitions)
        self.assertEqual(session.calls[-1][1]["qualified"], '"ts_ods"."objects"')


class ObjectRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(etl.router, prefix="/api/v1")
        self.app.dependency_overrides[get_current_user] = lambda: {"id": "ordinary-user"}
        self.client = TestClient(self.app)

    def test_catalog_and_preview_routes_reuse_signed_in_policy_and_exact_service_contract(self):
        instance, _ = service(Result([]), Result([summary(properties_count=0)]), Result([]), Result(scalar=True), Result([]))
        with patch.object(etl, "get_etl_layers", return_value=instance):
            response = self.client.get("/api/v1/etl/object-types?limit=100&offset=0")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["total"], 0)
            response = self.client.get("/api/v1/etl/ts_ods/objects/preview")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["id"], "ts_ods.objects")
            self.assertEqual(response.json()["visibility"], "normal")

    def test_missing_ontology_is_explicit_503_on_both_get_routes(self):
        instance, session = service(Result([]), ontology_installed=False)
        with patch.object(etl, "get_etl_layers", return_value=instance):
            for path in ("/api/v1/etl/object-types", "/api/v1/etl/ts_ods/objects/preview"):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 503)
                    self.assertIn("not initialized", response.json()["detail"])
        self.assertTrue(all(sql.lstrip().startswith("SELECT") for sql, _ in session.calls))

    def test_columns_route_returns_physical_metadata_without_reading_rows(self):
        fields = [
            {**column("id", "bigint", True, 1, False, "Record identifier"), "ordinalPosition": 1},
            {**column("title"), "ordinalPosition": 3},
        ]
        instance, session = service(Result(fields), ontology_installed=False)
        with patch.object(etl, "get_etl_layers", return_value=instance):
            response = self.client.get("/api/v1/etl/ts_ods/objects/columns")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), fields)
        self.assertEqual(len(session.calls), 1)
        sql, params = session.calls[0]
        self.assertEqual(params, {"schema": "ts_ods", "table": "objects"})
        self.assertIn('a.attnum AS "ordinalPosition"', sql)
        self.assertIn("FROM pg_attribute", sql)
        self.assertIn("ORDER BY a.attnum", sql)
        self.assertNotIn("ontology_objects", sql)

    def test_columns_route_rejects_invalid_identifier_and_reports_missing_table(self):
        instance, session = service(Result([]))
        with patch.object(etl, "get_etl_layers", return_value=instance):
            self.assertEqual(self.client.get("/api/v1/etl/ods/bad%3Bquery/columns").status_code, 400)
            self.assertFalse(session.calls)
            self.assertEqual(self.client.get("/api/v1/etl/ods/missing/columns").status_code, 404)
        self.assertEqual(len(session.calls), 1)

    def test_fastapi_bounds_validate_before_any_service_or_database_call(self):
        with patch.object(etl, "get_etl_layers") as factory:
            for query in ("limit=0", "limit=501", "offset=-1", "offset=1000001", "search=" + "x" * 1001):
                self.assertEqual(self.client.get("/api/v1/etl/object-types?" + query).status_code, 422)
            factory.assert_not_called()

    def test_preview_rows_route_applies_its_own_pagination_budget_only(self):
        with patch.object(etl, "get_etl_layers", return_value=AsyncMock()) as factory:
            instance = factory.return_value
            instance.data.return_value = {}
            preview = "/api/v1/etl/ods/objects/preview-rows"
            for payload in ({"limit": 101}, {"offset": 2001}, {"limit": 0}, {"offset": -1}):
                self.assertEqual(self.client.post(preview, json=payload).status_code, 422)
            factory.assert_not_called()
            self.assertEqual(self.client.post(preview, json={"offset": 2000}).status_code, 200)
            self.assertTrue(instance.data.await_args.kwargs["preview_rows"])
            self.assertEqual(instance.data.await_args.args[2]["limit"], 50)
            self.assertEqual(self.client.post("/api/v1/etl/ods/objects/data",
                                              json={"limit": 101, "offset": 2001}).status_code, 200)
            self.assertFalse(instance.data.await_args.kwargs)

    def test_filter_index_coverage_request_never_accepts_values_or_sql(self):
        url = "/api/v1/etl/ts_ods/objects/preview-index-coverage"
        with patch.object(etl, "preview_index_coverage", new=AsyncMock(return_value={"state": "candidate"})) as advisory:
            for data in (
                {"field": "id", "operator": "eq", "value": 3},
                {"field": "id", "operator": "in"},
                {"field": "id OR TRUE", "operator": "eq"},
                {"field": "id", "operator": "eq", "sort": "ASC"},
            ):
                self.assertEqual(self.client.post(url, json=data).status_code, 422)
            self.assertEqual(self.client.post(url + "?value=3", json={"field": "id", "operator": "eq"}).status_code, 422)
            advisory.assert_not_called()
            response = self.client.post(url, json={"field": "id", "operator": "eq"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"state": "candidate"})
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_lineage_path_routes_require_exact_query_shape_before_service(self):
        root = "/api/v1/etl/ts_ods/objects"
        path = root + "/lineage-path"
        picker = root + "/lineage-path-targets"
        common = root + "/lineage-common-upstream"
        selected = "?targetSchema=ts_dwd&targetTable=related"
        with patch.object(etl, "get_etl_layers", return_value=AsyncMock()) as factory:
            for invalid in (path, path + selected + "&targetTable=other", path + selected + "&includeHidden=true",
                            common, common + selected + "&targetSchema=ts_ods",
                            picker + "?search=a&search=b", picker + "?total=true", picker + "?search=" + "x" * 101):
                self.assertEqual(self.client.get(invalid).status_code, 422)
            factory.assert_not_called()
            service = factory.return_value
            service.lineage_path.return_value = {"state": "found", "searchTruncated": False, "edges": [
                {"from": {"schema": "ts_ods", "table": "objects"}, "to": {"schema": "ts_dwd", "table": "related"},
                 "kinds": ["foreign_key"]},
            ]}
            result = self.client.get(path + selected)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.headers["Cache-Control"], "no-store")
            self.assertEqual(result.json()["edges"][0]["from"], {"schema": "ts_ods", "table": "objects"})
            service.lineage_path.assert_awaited_once_with("ts_ods", "objects", "ts_dwd", "related")
            service.lineage_common_upstream.return_value = {"state": "none_within_limit", "searchTruncated": False}
            result = self.client.get(common + selected)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.headers["Cache-Control"], "no-store")
            self.assertIsNone(result.json()["ancestor"])
            service.lineage_common_upstream.assert_awaited_once_with("ts_ods", "objects", "ts_dwd", "related")
            service.lineage_path_targets.return_value = {"items": [{"schema": "ts_dwd", "table": "related"}], "truncated": False}
            result = self.client.get(picker + "?search=related")
            self.assertEqual(result.json(), service.lineage_path_targets.return_value)
            service.lineage_path_targets.assert_awaited_once_with("ts_ods", "objects", "related")

    def test_rejected_authentication_prevents_metadata_routes(self):
        def reject():
            raise HTTPException(401, "Authentication required")
        self.app.dependency_overrides[get_current_user] = reject
        with patch.object(etl, "get_etl_layers") as factory:
            for path in ("/api/v1/etl/object-types", "/api/v1/etl/ts_ods/objects/preview",
                         "/api/v1/etl/ts_ods/objects/columns",
                         "/api/v1/etl/ts_ods/objects/lineage-path-targets",
                         "/api/v1/etl/ts_ods/objects/lineage-path?targetSchema=ts_ods&targetTable=other"):
                self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.post("/api/v1/etl/ts_ods/objects/preview-rows",
                                              json={}).status_code, 401)
            factory.assert_not_called()

    def test_preview_identifier_and_hidden_metadata_schema_are_rejected_before_session(self):
        instance, session = service()
        with patch.object(etl, "get_etl_layers", return_value=instance):
            self.assertEqual(self.client.get("/api/v1/etl/ts_meta/objects/preview").status_code, 404)
            self.assertEqual(self.client.get("/api/v1/etl/ts_ods/bad%3Bquery/preview").status_code, 400)
        self.assertEqual(session.calls, [])
