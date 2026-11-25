"""Connection identity, scope isolation and bounded source reads without external services."""

from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
import httpx
from pydantic import ValidationError

from app.web.api.models.storage_nodes import StorageNodeRef
from app.web.services import storage_nodes as sources


def fixture(engine="postgresql"):
    row = {"id": uuid4(), "name": "Saved source", "engine": engine, "paused_at": None,
           "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
           "config": {"host": "source.synthetic.test", "port": 5432, "database": "default_db",
                      "username": "reader", "auth": "password", "tls": False}}
    if engine == "elasticsearch":
        row["config"].update(database="", port=9200)
    session = object()
    @asynccontextmanager
    async def context():
        yield session
    registry = SimpleNamespace(_session=context, _stored=AsyncMock(return_value=row),
                               _credentials=AsyncMock(return_value={"password": "private-password"}))
    return sources.StorageNodes(registry), registry, row, session


class StorageNodeContracts(unittest.TestCase):
    def test_same_schema_on_different_connections_has_a_different_identity(self):
        first, second = uuid4(), uuid4()
        self.assertNotEqual(sources.node_id(first, "schema", "public"), sources.node_id(second, "schema", "public"))
        self.assertNotEqual(sources.node_id(first, "schema", "same"), sources.node_id(first, "database", "same"))
        self.assertEqual(sources.node_id(first, "schema", "研发 / sales"), sources.node_id(str(first), "schema", "研发 / sales"))

    def test_scope_accepts_real_names_without_a_ts_prefix_and_rejects_controls(self):
        reference = StorageNodeRef(connectionId=uuid4(), kind="database", name="Sales / 2026")
        self.assertEqual(reference.name, "Sales / 2026")
        with self.assertRaises(ValidationError):
            StorageNodeRef(connectionId=uuid4(), kind="index", name="logs\n_all")

    def test_identifier_and_index_paths_cannot_inject_a_statement_or_index_expression(self):
        self.assertEqual(sources._quoted('sales";DELETE FROM x;--'), '"sales"";DELETE FROM x;--"')
        self.assertEqual(sources._quoted('sales`;DELETE FROM x;--', '`'), '`sales``;DELETE FROM x;--`')
        for name in ["*", "_all", "logs,users", "logs/_search", ".security", "logs%2Fusers", "logs?x=1"]:
            with self.subTest(name=name), self.assertRaises(HTTPException):
                sources._index_path(name)


class StorageNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_revision_is_rejected_under_source_share_lock_before_connecting(self):
        service, registry, row, session = fixture()
        with self.assertRaises(HTTPException) as failure:
            await service.nodes(row["id"], expected_updated_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(failure.exception.status_code, 409)
        registry._stored.assert_awaited_once_with(str(row["id"]), session=session, lock=" FOR SHARE")
        registry._credentials.assert_not_awaited()

    async def test_pg_nodes_use_only_the_saved_connection_and_existing_private_scope_rules(self):
        service, registry, row, _ = fixture()
        connection = SimpleNamespace(fetch=AsyncMock(return_value=[{"name": "public"}, {"name": "Sales"}]))
        @asynccontextmanager
        async def catalog(stored):
            self.assertIs(stored, row)
            yield connection
        registry._postgres_catalog_row = catalog
        result = await service.nodes(row["id"])
        self.assertEqual([node.name for node in result.nodes], ["public", "Sales"])
        self.assertEqual(result.nodes[0].sourceUpdatedAt, row["updated_at"])
        self.assertFalse(result.hasMore)
        statement = connection.fetch.await_args.args[0]
        self.assertIn(sources._CATALOG_SCHEMA_SCOPE, statement)
        self.assertNotIn("private-password", result.model_dump_json())

    async def test_pg_inaccessible_relation_cannot_be_read_even_when_caller_names_it(self):
        service, registry, row, _ = fixture()
        connection = SimpleNamespace(fetchval=AsyncMock(return_value=None), fetch=AsyncMock())
        @asynccontextmanager
        async def catalog(stored):
            yield connection
        registry._postgres_catalog_row = catalog
        with self.assertRaises(HTTPException) as failure:
            await service.preview(row["id"], "schema", "handoff", "private_records")
        self.assertEqual(failure.exception.status_code, 404)
        self.assertIn(sources._CATALOG_RELATION_SCOPE, connection.fetchval.await_args.args[0])
        connection.fetch.assert_not_awaited()

    async def test_pg_primary_key_metadata_does_not_treat_include_columns_as_keys(self):
        service, registry, row, _ = fixture()
        connection = SimpleNamespace(fetchval=AsyncMock(return_value=42), fetch=AsyncMock(return_value=[
            {"name": "id", "type": "bigint", "nullable": False, "position": 1, "primaryKey": True},
            {"name": "value", "type": "text", "nullable": True, "position": 2, "primaryKey": False},
        ]))
        @asynccontextmanager
        async def catalog(stored):
            yield connection
        registry._postgres_catalog_row = catalog
        result = await service.columns(row["id"], "schema", "public", "records")
        self.assertEqual([column.name for column in result.columns if column.primaryKey], ["id"])
        statement = connection.fetch.await_args.args[0]
        self.assertIn("pk.ord<=i.indnkeyatts", statement)
        self.assertIn("i.indislive", statement)

    async def test_pg_preview_quotes_real_source_identifiers_and_reports_truncation(self):
        service, registry, row, _ = fixture()
        connection = SimpleNamespace(fetchval=AsyncMock(return_value=42),
                                     fetch=AsyncMock(side_effect=[[{"name": "id"}], [{"id": 1}, {"id": 2}]]))
        @asynccontextmanager
        async def catalog(stored):
            yield connection
        registry._postgres_catalog_row = catalog
        result = await service.preview(row["id"], "schema", 'Sales"2026', 'Order"Details', limit=1)
        self.assertEqual(result.rows, [{"id": 1}])
        self.assertTrue(result.hasMore)
        self.assertEqual(connection.fetch.await_args.args, ('SELECT "id" FROM "Sales""2026"."Order""Details" LIMIT $1', 2))
        self.assertIn("has_column_privilege", connection.fetch.await_args_list[0].args[0])

    async def test_preview_encodes_bson_decimal_uuid_binary_and_dates_without_changing_transfer_values(self):
        from bson import ObjectId
        from bson.decimal128 import Decimal128

        service, _, row, _ = fixture("mongodb")
        document = {"_id": ObjectId(), "amount": Decimal("12.30"), "uuid": uuid4(), "binary": b"\xff\x00",
                    "day": date(2026, 1, 1), "bsonDecimal": Decimal128("99.90")}
        service._rows = AsyncMock(return_value=[document])
        result = await service.preview(row["id"], "database", "sales", "orders")
        encoded = json.loads(result.model_dump_json())
        self.assertEqual(encoded["rows"][0]["amount"], {"$numberDecimal": "12.30"})
        self.assertEqual(encoded["rows"][0]["binary"]["$binary"]["base64"], "/wA=")
        native = await service.read_rows(row["id"], "database", "sales", "orders", expected_updated_at=row["updated_at"])
        self.assertIs(native[0]["_id"], document["_id"])
        self.assertIs(native[0]["amount"], document["amount"])

    async def test_transfer_read_rejects_incomplete_source_instead_of_returning_truncated_success(self):
        service, _, row, _ = fixture()
        service._rows = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
        with self.assertRaises(HTTPException) as failure:
            await service.read_rows(row["id"], "schema", "public", "records", limit=1,
                                    expected_updated_at=row["updated_at"])
        self.assertEqual(failure.exception.detail["code"], "source_row_limit_exceeded")

    async def test_transfer_read_requires_the_reviewed_connection_revision(self):
        service, registry, row, _ = fixture()
        with self.assertRaises(HTTPException) as failure:
            await service.read_rows(row["id"], "schema", "public", "records", expected_updated_at=None)
        self.assertEqual(failure.exception.status_code, 422)
        registry._stored.assert_not_awaited()

    async def test_kind_is_checked_before_using_an_engine_reader(self):
        service, _, row, _ = fixture("mysql")
        service._mysql = AsyncMock()
        with self.assertRaises(HTTPException) as failure:
            await service.relations(row["id"], "schema", "sales")
        self.assertEqual(failure.exception.status_code, 422)
        service._mysql.assert_not_awaited()

    async def test_mysql_discovers_multiple_databases_from_the_same_saved_connection(self):
        service, _, row, _ = fixture("mysql")
        connection, cursor = MagicMock(), MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [("sales",), ("research",), ("mysql",), ("information_schema",)]
        driver = SimpleNamespace(connect=MagicMock(return_value=connection))
        with patch.object(sources, "_driver", return_value=driver):
            result = await service.nodes(row["id"])
        self.assertEqual([(node.kind, node.name) for node in result.nodes], [("database", "research"), ("database", "sales")])
        self.assertEqual(driver.connect.call_args.kwargs["database"], "default_db")
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertIn("START TRANSACTION READ ONLY", statements)
        self.assertIn("SHOW DATABASES", statements)
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()

    async def test_mysql_data_read_selects_the_node_database_instead_of_the_saved_default(self):
        service, _, row, _ = fixture("mysql")
        connection, cursor = MagicMock(), MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.side_effect = [("sales",), ("orders",)]
        cursor.description = [("id",), ("value",)]
        cursor.fetchall.return_value = [(7, "sample")]
        with patch.object(sources, "_driver", return_value=SimpleNamespace(connect=MagicMock(return_value=connection))):
            result = await service.read_rows(row["id"], "database", "sales", "orders", expected_updated_at=row["updated_at"])
        self.assertEqual(result, [{"id": 7, "value": "sample"}])
        self.assertEqual(cursor.execute.call_args.args, ("SELECT * FROM `sales`.`orders` LIMIT %s", (101,)))
        connection.rollback.assert_called_once()

    async def test_node_search_finds_resources_beyond_the_first_catalog_page(self):
        service, _, row, _ = fixture("mysql")
        connection, cursor = MagicMock(), MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [(f"database_{index:03}",) for index in range(150)] + [("zzz_sales",)]
        with patch.object(sources, "_driver", return_value=SimpleNamespace(connect=MagicMock(return_value=connection))):
            initial = await service.nodes(row["id"])
            exact = await service.nodes(row["id"], search="zzz_sales")
        self.assertEqual(len(initial.nodes), 100)
        self.assertTrue(initial.hasMore)
        self.assertEqual([node.name for node in exact.nodes], ["zzz_sales"])
        self.assertFalse(exact.hasMore)

    async def test_mysql_does_not_silently_accept_a_tls_downgrade(self):
        service, _, row, _ = fixture("mysql")
        row["config"]["tls"] = True
        connection, cursor = MagicMock(), MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = ("Ssl_cipher", "")
        with patch.object(sources, "_driver", return_value=SimpleNamespace(connect=MagicMock(return_value=connection))):
            with self.assertRaises(HTTPException) as failure:
                await service.nodes(row["id"])
        self.assertEqual(failure.exception.status_code, 502)
        self.assertNotIn("private-password", str(failure.exception.detail))

    async def test_elasticsearch_nodes_are_concrete_indices_and_use_application_metadata_api(self):
        service, _, row, _ = fixture("elasticsearch")
        original_client = httpx.AsyncClient
        def respond(request):
            self.assertEqual(request.url.path, "/_resolve/index/*")
            self.assertEqual(request.url.params["expand_wildcards"], "open")
            return httpx.Response(200, json={"indices": [{"name": "filings"}, {"name": "patents"}, {"name": ".security"}],
                                           "data_streams": [{"name": "logs"}]})
        with patch.object(sources.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)):
            result = await service.nodes(row["id"])
        self.assertEqual([(node.kind, node.name) for node in result.nodes], [("index", "filings"), ("index", "patents")])

    async def test_elasticsearch_reads_exact_index_and_rejects_timed_out_partial_results(self):
        for timed_out, failed in [(True, 0), (False, 1)]:
            service, _, row, _ = fixture("elasticsearch")
            original_client = httpx.AsyncClient
            def respond(request):
                if request.url.path == "/filings/_mapping":
                    return httpx.Response(200, json={"filings": {"mappings": {}}})
                self.assertEqual(request.url.path, "/filings/_search")
                self.assertEqual(request.url.params["allow_partial_search_results"], "false")
                body = json.loads(request.content)
                self.assertEqual(body["size"], 21)
                return httpx.Response(200, json={"timed_out": timed_out, "_shards": {"failed": failed}, "hits": {"hits": []}})
            with self.subTest(timed_out=timed_out, failed=failed), patch.object(sources.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)):
                with self.assertRaises(HTTPException) as failure:
                    await service.preview(row["id"], "index", "filings", "filings")
                self.assertEqual(failure.exception.status_code, 502)

    async def test_elasticsearch_columns_and_rows_include_the_document_identity(self):
        service, _, row, _ = fixture("elasticsearch")
        service._http = AsyncMock(side_effect=[
            {"filings": {"mappings": {"properties": {"value": {"type": "long"}}}}},
            {"filings": {"mappings": {"properties": {"value": {"type": "long"}}}}},
            {"hits": {"hits": [{"_id": "doc-7", "_source": {"value": 9}}]}},
        ])
        columns = await service.columns(row["id"], "index", "filings", "filings")
        self.assertEqual([column.name for column in columns.columns], ["_id", "value"])
        self.assertTrue(columns.columns[0].primaryKey)
        records = await service.read_rows(row["id"], "index", "filings", "filings", expected_updated_at=row["updated_at"])
        self.assertEqual(records, [{"_id": "doc-7", "value": 9}])

    async def test_elasticsearch_alias_cannot_read_a_different_or_multiple_index_nodes(self):
        for indices in [{"real_index": {"mappings": {}}},
                        {"filings": {"mappings": {}}, "other_index": {"mappings": {}}}]:
            service, _, row, _ = fixture("elasticsearch")
            service._http = AsyncMock(return_value=indices)
            with self.subTest(indices=list(indices)), self.assertRaises(HTTPException) as failure:
                await service.preview(row["id"], "index", "filings", "filings")
            self.assertEqual(failure.exception.status_code, 422)
            self.assertEqual(service._http.await_count, 1)
            self.assertEqual(service._http.await_args.args[2], "/filings/_mapping")

    async def test_remote_failure_does_not_return_upstream_credentials_or_error_text(self):
        service, _, row, _ = fixture("mysql")
        service._mysql = AsyncMock(side_effect=RuntimeError("private-password @ source.synthetic.test"))
        with self.assertRaises(HTTPException) as failure:
            await service.nodes(row["id"])
        self.assertEqual(failure.exception.status_code, 502)
        self.assertNotIn("private-password", str(failure.exception.detail))


if __name__ == "__main__":
    unittest.main()
