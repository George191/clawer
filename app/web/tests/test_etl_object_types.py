"""Read-only object catalog contracts with local PostgreSQL catalog fixtures; no database/network."""

import unittest
from contextlib import asynccontextmanager
from copy import deepcopy
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.web.api.dependencies.common import get_current_user
from app.web.api.routes import etl
from app.web.services.etl_layers import EtlLayers


class Result:
    def __init__(self, rows=(), scalar=None):
        self.rows, self.scalar = rows, scalar

    def mappings(self):
        return iter(self.rows)

    def scalar_one(self):
        return self.scalar


class CatalogSession:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []
        self.allowed_prefixes = ("SELECT",)

    async def execute(self, query, params=None):
        sql = str(query)
        self.calls.append((sql, deepcopy(params)))
        if not sql.lstrip().startswith(self.allowed_prefixes):
            raise AssertionError("The object metadata endpoints must only issue SELECT")
        if not self.results:
            raise AssertionError("Unexpected catalog query: " + sql)
        return self.results.pop(0)


def service(*results):
    instance = object.__new__(EtlLayers)
    session = CatalogSession(*results)

    @asynccontextmanager
    async def context():
        yield session

    instance.session = context
    return instance, session


def summary(schema="ts_ods", table="objects", **changes):
    return {"oid": 101, "schema": schema, "name": table, "description": "Actual PG table comment",
            "data_type": "catalog classification", "object_count": 23, "properties_count": 3, **changes}


def column(name, type="text", primary=False, position=None, nullable=True, comment=None):
    return {"name": name, "type": type, "primary": primary, "primaryPosition": position,
            "nullable": nullable, "comment": comment}


def foreign(name="fk_owner", source_schema="ts_ods", source_table="objects",
            target_schema="ts_rds", target_table="owners", columns=None, referenced=None):
    return {"name": name, "source_schema": source_schema, "source_table": source_table,
            "target_schema": target_schema, "target_table": target_table,
            "columns": columns or ["owner_id"], "referenced_columns": referenced or ["id"]}


class ObjectCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_pagination_and_facets_are_real_metadata_counts(self):
        instance, session = service(Result([
            {"schema": "ts_ods", "resource_group": "ingestion", "count": 5},
            {"schema": "ts_custom", "resource_group": "other", "count": 3},
        ]), Result(scalar=True), Result([summary()]))
        result = await instance.object_types(limit=1, offset=2)
        self.assertEqual((result["total"], result["limit"], result["offset"]), (8, 1, 2))
        self.assertEqual(result["schemaCounts"], {"ts_ods": 5, "ts_custom": 3})
        self.assertEqual(result["groupCounts"], {"ingestion": 5, "processing": 0, "serving": 0, "other": 3})
        item = result["items"][0]
        self.assertEqual(item["id"], "ts_ods.objects")
        self.assertEqual(item["displayName"], "objects")
        self.assertEqual(item["description"], "Actual PG table comment")
        self.assertEqual(item["objectCount"], 23)
        self.assertTrue(item["objectCountEstimated"])
        self.assertEqual(item["propertiesCount"], 3)
        self.assertIsNone(item["titleProperty"])
        self.assertEqual(item["visibility"], "not_configured")
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
        ]), Result(scalar=False), Result([summary("ts_ods_hist", data_type=None)]))
        needle = "O'Reilly %_ ; DROP TABLE things"
        result = await instance.object_types("ods_hist", needle, "ingestion", 1, 0,
                                             "ts_ods_hist.objects,ts_custom.second")
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["groupCounts"]["other"], 2)
        self.assertEqual(result["items"][0]["dataType"], "")
        count_sql, params = session.calls[0]
        self.assertNotIn(needle, count_sql)
        self.assertEqual(params["search"], needle)
        self.assertEqual(params["schema"], "ts_ods_hist")
        self.assertEqual(params["ids"], ["ts_ods_hist.objects", "ts_custom.second"])
        self.assertNotIn("group", params)
        self.assertEqual(session.calls[-1][1]["group"], "ingestion")
        self.assertIn("NULL::text AS data_type", session.calls[-1][0])

    async def test_empty_or_out_of_range_page_retains_counts_without_fetching_rows(self):
        instance, session = service(Result([{"schema": "ts_task", "resource_group": "processing", "count": 2}]))
        result = await instance.object_types(offset=2)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(session.calls), 1)

    async def test_catalog_and_preview_exclude_internal_schemas_and_all_child_tables(self):
        instance, session = service(Result([{ "schema": "ts_ods", "resource_group": "ingestion", "count": 1}]),
                                    Result(scalar=False), Result([summary()]))
        await instance.object_types()
        for sql, _ in (session.calls[0], session.calls[-1]):
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
        instance, session = service(Result(scalar=False), Result([summary()]), Result([]), Result(fields))
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
        instance, _ = service(Result(scalar=True), Result([summary(properties_count=0)]), Result(links))
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
        instance, session = service(Result(scalar=False), Result([summary(properties_count=0)]), Result(links))
        result = await instance.preview("ts_ods", "objects")
        self.assertEqual([item["id"] for item in result["linkedObjectTypes"]], ["ts_rds.owners"])
        sql = session.calls[-1][0]
        for side, table in (("sn", "sc"), ("tn", "tc")):
            self.assertIn(f"{side}.nspname <> 'ts_meta'", sql)
            self.assertIn(f"{side}.nspname <> 'ts_meta_hist'", sql)
            self.assertIn(f"{side}.nspname ~ '^ts_[a-z0-9_]+$'", sql)
            self.assertIn(f"NOT {table}.relispartition", sql)
            self.assertIn(f"child.inhrelid = {table}.oid", sql)
        self.assertIn("con.conparentid = 0", sql)

    async def test_missing_or_partition_child_table_returns_404_and_does_not_query_business_data(self):
        instance, session = service(Result(scalar=False), Result([]))
        with self.assertRaises(HTTPException) as error:
            await instance.preview("ts_ods", "missing")
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(all('"ts_ods"."missing"' not in sql for sql, _ in session.calls))

    async def test_zero_property_table_is_valid_and_does_not_invent_a_title_or_primary_key(self):
        instance, session = service(Result(scalar=False), Result([summary(properties_count=0)]), Result([]))
        result = await instance.preview("ts_ods", "objects")
        self.assertEqual(result["properties"], [])
        self.assertEqual(result["primaryKey"], [])
        self.assertEqual(len(session.calls), 3)

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
        self.assertEqual(session.calls[-1][1]["limit"], 1)

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
        instance, _ = service(Result([]), Result(scalar=False), Result([summary(properties_count=0)]), Result([]))
        with patch.object(etl, "get_etl_layers", return_value=instance):
            response = self.client.get("/api/v1/etl/object-types?limit=100&offset=0")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["total"], 0)
            response = self.client.get("/api/v1/etl/ts_ods/objects/preview")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["id"], "ts_ods.objects")
            self.assertEqual(response.json()["visibility"], "not_configured")

    def test_fastapi_bounds_validate_before_any_service_or_database_call(self):
        with patch.object(etl, "get_etl_layers") as factory:
            for query in ("limit=0", "limit=501", "offset=-1", "offset=1000001", "search=" + "x" * 1001):
                self.assertEqual(self.client.get("/api/v1/etl/object-types?" + query).status_code, 422)
            factory.assert_not_called()

    def test_rejected_authentication_prevents_both_metadata_routes(self):
        def reject():
            raise HTTPException(401, "Authentication required")
        self.app.dependency_overrides[get_current_user] = reject
        with patch.object(etl, "get_etl_layers") as factory:
            for path in ("/api/v1/etl/object-types", "/api/v1/etl/ts_ods/objects/preview"):
                self.assertEqual(self.client.get(path).status_code, 401)
            factory.assert_not_called()

    def test_preview_identifier_and_hidden_metadata_schema_are_rejected_before_session(self):
        instance, session = service()
        with patch.object(etl, "get_etl_layers", return_value=instance):
            self.assertEqual(self.client.get("/api/v1/etl/ts_meta/objects/preview").status_code, 404)
            self.assertEqual(self.client.get("/api/v1/etl/ts_ods/bad%3Bquery/preview").status_code, 400)
        self.assertEqual(session.calls, [])
