"""Connector contracts and driver calls; no external database is accessed."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import ssl
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
import httpx
from pydantic import ValidationError

from app.web.api.models.data_connections import DataConnectionPayload
from app.web.services import data_connections as sources


@asynccontextmanager
async def stub_registry_session():
    yield object()


def payload(engine="postgresql", **changes):
    config = {"host": "db.example.test", "port": 5432, "database": "analytics", "username": "reader", "auth": "password", "tls": True}
    credentials = {"password": "test-only-password"}
    if engine in {"dameng", "elasticsearch", "s3"}:
        config["database"] = ""
    if engine == "s3":
        config.update(port=443, auth="access_key", username="test-key-id", region="us-east-1", bucket="test-bucket")
        credentials = {"secretAccessKey": "test-only-secret-key"}
    if engine == "redis":
        config.update(port=6379, database="0")
    if engine == "dameng":
        config["tls"] = False
    config.update(changes)
    return DataConnectionPayload(name="Test source", engine=engine, config=config, credentials=credentials)


class DataConnectionContracts(unittest.TestCase):
    def test_engine_contracts_and_safe_ipv6_host(self):
        for engine in ("postgresql", "mysql", "oracle", "dameng", "elasticsearch", "milvus", "s3", "redis", "clickhouse", "mongodb"):
            with self.subTest(engine=engine):
                self.assertEqual(payload(engine).engine, engine)
        self.assertEqual(payload(host="2001:db8::1").config.host, "2001:db8::1")

    def test_mongodb_scope_and_authentication_database(self):
        self.assertEqual(payload("mongodb").config.model_dump()["authDatabase"], "admin")
        self.assertEqual(payload("mongodb", authDatabase="readers").config.authDatabase, "readers")
        self.assertNotIn("authDatabase", payload("mysql").config.model_dump())
        with self.assertRaises(ValidationError):
            payload("mysql", authDatabase="admin")
        for changes in ({"database": ""}, {"database": "bad/name"}, {"database": "文" * 22},
                        {"authDatabase": "bad.name"}, {"auth": "token", "username": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                payload("mongodb", **changes)

    def test_mongodb_is_a_document_connector_without_catalog_or_sync(self):
        with patch.object(sources, "_driver") as driver:
            item = next(item for item in sources.connector_types() if item["engine"] == "mongodb")
        self.assertEqual((item["category"], item["port"]), ("document", 27017))
        self.assertTrue(item["driverAvailable"])
        self.assertFalse(item["catalogAvailable"])
        driver.assert_any_call("mongodb")

    def test_cloud_provider_and_engine_specific_fields(self):
        self.assertNotIn("provider", payload(provider="self_hosted").config.model_dump())
        self.assertNotIn("bucket", payload().config.model_dump())
        self.assertNotIn("region", payload().config.model_dump())
        for provider in ("aws", "azure", "gcp", "aliyun", "tencent", "huawei", "oracle_cloud", "other"):
            self.assertEqual(payload(provider=provider).config.model_dump()["provider"], provider)
        for engine, changes in (
            ("postgresql", {"provider": "invalid"}), ("mysql", {"bucket": "test-bucket"}),
            ("clickhouse", {"region": "us-east-1"}), ("clickhouse", {"database": ""}), ("redis", {"addressingStyle": "virtual"}),
            ("redis", {"database": "1e2"}), ("redis", {"database": "-1"}), ("redis", {"database": "01"}),
            ("s3", {"auth": "password"}), ("s3", {"username": ""}), ("s3", {"bucket": None}),
            ("s3", {"bucket": "../logs"}), ("s3", {"region": None}), ("s3", {"database": "db"}),
        ):
            with self.subTest(engine=engine, changes=changes), self.assertRaises(ValidationError):
                payload(engine, **changes)
        with self.assertRaises(ValidationError):
            DataConnectionPayload.model_validate({**payload("s3").model_dump(), "credentials": {"password": "wrong-kind"}})
        self.assertNotIn("test-only-secret-key", repr(payload("s3")))

    def test_invalid_host_port_auth_and_unused_scope_are_rejected(self):
        for changes in ({"host": "https://user:password@example.test/path"}, {"host": "a\nb"}, {"port": "5432"}, {"port": 0}, {"auth": "token"}, {"database": ""}, {"username": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                payload(**changes)
        with self.assertRaises(ValidationError):
            payload("elasticsearch", database="not-a-database-scope")
        with self.assertRaises(ValidationError):
            payload("dameng", tls=True)

    def test_credentials_are_secret_values_and_mismatched_secret_is_rejected(self):
        body = payload()
        self.assertNotIn("test-only-password", repr(body))
        with self.assertRaises(ValidationError):
            DataConnectionPayload.model_validate({**body.model_dump(), "credentials": {"token": "wrong-credential-kind"}})

    def test_cipher_requires_explicit_secret_and_derives_stable_domain_key(self):
        for configured, secret in ((set(), "random-default"), ({"SECRET_KEY"}, "changethis"), ({"SECRET_KEY"}, "")):
            with patch.object(sources, "settings", SimpleNamespace(SECRET_KEY=secret, model_fields_set=configured)), self.assertRaises(HTTPException):
                sources.credential_cipher()
        stable = SimpleNamespace(SECRET_KEY="explicitly-configured-test-key", model_fields_set={"SECRET_KEY"})
        with patch.object(sources, "settings", stable):
            encrypted = sources.credential_cipher().encrypt(b"private value")
            self.assertEqual(sources.credential_cipher().decrypt(encrypted), b"private value")

    def test_missing_dameng_driver_reports_vendor_prerequisite(self):
        with patch.object(sources.importlib, "import_module", side_effect=ImportError("native-driver-not-installed")):
            with self.assertRaises(HTTPException) as failure:
                sources._driver("dameng")
            self.assertEqual(failure.exception.status_code, 503)
            self.assertIn("DPI", failure.exception.detail)
            self.assertNotIn("native-driver-not-installed", failure.exception.detail)


class DataConnectionProbes(unittest.IsolatedAsyncioTestCase):
    async def test_mongodb_tests_only_the_selected_database_metadata(self):
        client = MagicMock()
        session = client.__enter__.return_value
        driver = SimpleNamespace(MongoClient=MagicMock(return_value=client))
        with patch.object(sources, "_driver", return_value=driver):
            await sources._probe(
                payload("mongodb", host="2001:db8::1", port=27017, authDatabase="readers"),
                {"password": "reserved@chars:/?"},
            )
        driver.MongoClient.assert_called_once_with(
            host="[2001:db8::1]", port=27017, username="reader", password="reserved@chars:/?",
            authSource="readers", tls=True, directConnection=True, timeoutMS=5000,
            retryReads=False, retryWrites=False, connect=False,
        )
        session.__getitem__.assert_called_once_with("analytics")
        session["analytics"].command.assert_called_once_with({
            "listCollections": 1, "nameOnly": True, "filter": {"name": {"$in": []}},
        })
        client.__exit__.assert_called_once()

    async def test_mongodb_denied_probe_does_not_echo_the_password(self):
        from pymongo.errors import OperationFailure

        client = MagicMock()
        database = client.__enter__.return_value["analytics"]
        database.command.side_effect = OperationFailure("private secret in driver error", code=13)
        with patch.object(sources, "_driver", return_value=SimpleNamespace(MongoClient=MagicMock(return_value=client))):
            service = object.__new__(sources.DataConnections)
            result = await service.test(payload("mongodb"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "Authentication or read access was rejected.")
        client.__exit__.assert_called_once()

    async def test_s3_signed_read_only_bucket_probe(self):
        from botocore.awsrequest import AWSResponse
        from botocore.httpsession import URLLib3Session

        xml = b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Name>test-bucket</Name><KeyCount>0</KeyCount></ListBucketResult>'
        for style, status, response_body, ok in [
            (None, 200, xml, True), ("virtual", 200, xml, True), ("path", 200, b"<other/>", False),
            ("path", 403, b"<Error><Code>AccessDenied</Code></Error>", False),
        ]:
            def respond(session, req):
                self.assertEqual(req.method, "GET")
                expected_host = "test-bucket.db.example.test" if style == "virtual" else "db.example.test"
                self.assertEqual(httpx.URL(req.url).host, expected_host)
                self.assertEqual(httpx.URL(req.url).path, "/" if style == "virtual" else "/test-bucket")
                self.assertEqual(httpx.URL(req.url).params["max-keys"], "0")
                self.assertIn(b"Credential=test-key-id/", req.headers["Authorization"])
                self.assertIn(b"/us-east-1/s3/aws4_request", req.headers["Authorization"])
                return AWSResponse(req.url, status, {}, SimpleNamespace(stream=lambda: [response_body]))

            with self.subTest(status=status, ok=ok), patch.object(URLLib3Session, "send", respond):
                result = await object.__new__(sources.DataConnections).test(payload("s3", provider="aws", addressingStyle=style))
                self.assertEqual(result["ok"], ok, result)
                self.assertNotIn("test-only-secret-key", json.dumps(result))

    async def test_clickhouse_checks_database_with_authenticated_read_only_query(self):
        original_client = httpx.AsyncClient
        for status, response_body, ok in [(200, b"analytics\n", True), (200, b"default\n", False), (302, b"", False)]:
            def respond(request):
                self.assertEqual(request.method, "GET")
                self.assertEqual(request.url.params["database"], "analytics")
                self.assertEqual(request.url.params["query"], "SELECT currentDatabase() FORMAT TabSeparatedRaw")
                self.assertTrue(request.headers["Authorization"].startswith("Basic "))
                return httpx.Response(status, content=response_body)
            client = original_client(transport=httpx.MockTransport(respond))
            with patch.object(sources.httpx, "AsyncClient", return_value=client) as factory:
                result = await object.__new__(sources.DataConnections).test(payload("clickhouse", provider="other"))
                self.assertEqual(result["ok"], ok, result)
                self.assertEqual(factory.call_args.kwargs, dict(timeout=5, trust_env=False, follow_redirects=False))

    async def test_redis_authenticates_and_pings_selected_database(self):
        driver = MagicMock()
        client = driver.Redis.return_value.__aenter__.return_value
        client.ping = AsyncMock(return_value=True)
        with patch.object(sources, "_driver", return_value=driver):
            await sources._probe(payload("redis", database="2"), {"password": "private"})
        driver.Redis.assert_called_once_with(host="db.example.test", port=6379, db=2, username="reader", password="private",
                                            ssl=True, ssl_check_hostname=True, socket_connect_timeout=5, socket_timeout=5)
        client.ping.assert_awaited_once_with()
        driver.Redis.return_value.__aexit__.assert_awaited_once()

    async def test_postgres_performs_read_only_query_and_closes(self):
        connection = SimpleNamespace(fetchval=AsyncMock(return_value=1), close=AsyncMock())
        driver = SimpleNamespace(connect=AsyncMock(return_value=connection))
        with patch.object(sources, "_driver", return_value=driver):
            await sources._probe(payload(), {"password": "private"})
        connection.fetchval.assert_awaited_once_with("SELECT 1")
        connection.close.assert_awaited_once_with(timeout=5)
        self.assertIsInstance(driver.connect.call_args.kwargs["ssl"], ssl.SSLContext)

    async def test_mysql_requires_actual_tls_and_uses_real_driver_deadlines(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("Ssl_cipher", "TLS_AES_256_GCM_SHA384")
        driver = SimpleNamespace(connect=MagicMock(return_value=connection))
        with patch.object(sources, "_driver", return_value=driver):
            await sources._probe(payload("mysql"), {"password": "private"})
        self.assertEqual(cursor.execute.call_args_list[-1].args, ("SELECT 1",))
        self.assertEqual(driver.connect.call_args.kwargs["read_timeout"], 5)
        self.assertFalse(driver.connect.call_args.kwargs["ssl_disabled"])
        connection.close.assert_called_once()
        cursor.fetchone.return_value = ("Ssl_cipher", "")
        with patch.object(sources, "_driver", return_value=driver), self.assertRaises(ssl.SSLError):
            await sources._probe(payload("mysql"), {"password": "private"})

    async def test_oracle_thin_probe_uses_service_name_and_read_only_query(self):
        connection = MagicMock()
        connection.__aenter__ = AsyncMock(return_value=connection)
        connection.__aexit__ = AsyncMock(return_value=False)
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=(1,))
        driver = SimpleNamespace(ConnectParams=MagicMock(), connect_async=AsyncMock(return_value=connection))
        with patch.object(sources, "_driver", return_value=driver):
            await sources._probe(payload("oracle"), {"password": "private"})
        self.assertEqual(driver.ConnectParams.call_args.kwargs["service_name"], "analytics")
        self.assertEqual(driver.ConnectParams.call_args.kwargs["protocol"], "tcps")
        cursor.execute.assert_awaited_once_with("SELECT 1 FROM DUAL")
        self.assertEqual(connection.call_timeout, 5000)

    async def test_dameng_probe_uses_vendor_millisecond_login_timeout(self):
        connection = MagicMock()
        driver = SimpleNamespace(connect=MagicMock(return_value=connection))
        with patch.object(sources, "_driver", return_value=driver):
            await sources._probe(payload("dameng"), {"password": "private"})
        self.assertEqual(driver.connect.call_args.kwargs["login_timeout"], 5000)
        self.assertEqual(driver.connect.call_args.kwargs["connection_timeout"], 5)
        connection.cursor.return_value.execute.assert_called_once_with("SELECT 1 FROM DUAL")
        connection.close.assert_called_once()

    async def test_milvus_authenticates_and_lists_collections_without_creating_any(self):
        client = MagicMock()
        driver = SimpleNamespace(MilvusClient=MagicMock(return_value=client))
        with patch.object(sources, "_driver", return_value=driver):
            await sources._probe(payload("milvus"), {"password": "private"})
        client.list_collections.assert_called_once_with(timeout=5)
        client.create_collection.assert_not_called()
        client.close.assert_called_once()

    async def test_elasticsearch_probe_verifies_cluster_and_never_follows_redirects(self):
        original_client = httpx.AsyncClient
        for status, body, success in ((200, {"cluster_name": "test", "version": {"number": "8.0"}}, True), (200, {"unrelated": "service"}, False), (401, {"error": "private"}, False), (302, {}, False)):
            seen = []
            def handler(request):
                seen.append(request)
                return httpx.Response(status, json=body, headers={"location": "http://other-host/"})
            client = original_client(transport=httpx.MockTransport(handler))
            with patch.object(sources, "_driver", return_value=SimpleNamespace()), patch.object(sources.httpx, "AsyncClient", return_value=client) as factory:
                service = object.__new__(sources.DataConnections)
                result = await service.test(payload("elasticsearch"))
            self.assertEqual(result["ok"], success)
            self.assertEqual(len(seen), 1)
            self.assertFalse(factory.call_args.kwargs["follow_redirects"])
            self.assertFalse(factory.call_args.kwargs["trust_env"])
            self.assertNotIn("test-only-password", json.dumps(result))

    async def test_probe_failure_is_explicit_and_does_not_echo_driver_secret(self):
        service = object.__new__(sources.DataConnections)
        with patch.object(sources, "_probe", side_effect=RuntimeError("dsn with test-only-password")):
            result = await service.test(payload())
        self.assertFalse(result["ok"])
        self.assertNotIn("test-only-password", json.dumps(result))
        with patch.object(sources, "_probe", side_effect=TimeoutError("test-only-password")):
            timed_out = await service.test(payload())
        self.assertIn("timed out", timed_out["message"])


class StoredConnectionCredentialsTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_change_requires_explicit_credentials_before_probing(self):
        service = object.__new__(sources.DataConnections)
        original = payload()
        revision = datetime.now(timezone.utc)
        stored = {"engine": original.engine, "config": original.config.model_dump(), "updated_at": revision}
        changes = (
            {"host": "other-db.example.test"}, {"port": 6543},
            {"database": "other"}, {"username": "other"}, {"tls": False}, {"provider": "aws"},
        )
        with (patch.object(service, "_session", stub_registry_session),
              patch.object(service, "_stored", return_value=stored), patch.object(service, "_probe_result") as probe):
            for change in changes:
                candidate = payload(**change).model_copy(update={"credentials": type(original.credentials)()})
                with self.subTest(change=change), self.assertRaises(HTTPException) as failure:
                    await service.test(candidate, "saved", expected_updated_at=revision)
                self.assertEqual(failure.exception.status_code, 422)
            probe.assert_not_called()

    async def test_rename_reuses_same_configuration_and_new_secret_can_be_used_for_change(self):
        service = object.__new__(sources.DataConnections)
        original = payload()
        settings = SimpleNamespace(SECRET_KEY="test-key-for-connection-binding", model_fields_set={"SECRET_KEY"})
        with patch.object(sources, "settings", settings):
            encrypted = sources.credential_cipher().encrypt(b'{"password":"stored-demo"}').decode("ascii")
            stored = {"engine": original.engine, "config": json.dumps(original.config.model_dump()), "credentials_ciphertext": encrypted}
            renamed = original.model_copy(update={"name": "Renamed", "credentials": type(original.credentials)()})
            self.assertEqual(await service._credentials(renamed, stored), {"password": "stored-demo"})
            self.assertEqual(await service._credentials(payload(host="other-db.example.test"), stored), {"password": "test-only-password"})


class SourceCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_saved_credentials_read_only_transaction_and_bound_name(self):
        service = object.__new__(sources.DataConnections)
        stored = {"name": "Saved demo", "engine": "postgresql", "config": payload().config.model_dump()}
        connection = MagicMock()
        connection.fetch = AsyncMock(side_effect=[[{"name": "demo"}], [{"name": "events", "kind": "r"}]])
        connection.fetchval = AsyncMock(return_value=True)
        driver = SimpleNamespace(connect=AsyncMock(return_value=connection))
        with (
            patch.object(service, "_session", stub_registry_session),
            patch.object(service, "_stored", return_value=stored),
            patch.object(service, "_credentials", return_value={"password": "from-saved-record"}) as credentials,
            patch.object(sources, "_driver", return_value=driver),
        ):
            name = """schema'); DELETE FROM user_data; --"""
            result = await service.catalog("saved", schema=name, search="literal %", limit=1, offset=3)
        credentials.assert_awaited_once()
        self.assertIs(credentials.call_args.args[1], stored)
        self.assertEqual(driver.connect.call_args.kwargs["password"], "from-saved-record")
        self.assertEqual(driver.connect.call_args.kwargs["server_settings"]["default_transaction_read_only"], "on")
        self.assertIsInstance(driver.connect.call_args.kwargs["ssl"], ssl.SSLContext)
        connection.transaction.assert_called_once_with(readonly=True, isolation="repeatable_read")
        self.assertEqual(connection.fetch.call_args.args[1:], (name, "literal %", 2, 3))
        self.assertNotIn(name, connection.fetch.call_args.args[0])
        connection.terminate.assert_called_once()
        self.assertEqual(result.relations[0].name, "events")

    async def test_non_postgres_is_ineligible_and_upstream_failures_hide_credentials(self):
        service = object.__new__(sources.DataConnections)
        stored = {"name": "Saved demo", "engine": "postgresql", "config": payload().config.model_dump()}
        with (
            patch.object(service, "_session", stub_registry_session),
            patch.object(service, "_stored", return_value={**stored, "engine": "mysql"}),
            patch.object(sources, "_driver") as driver,
            self.assertRaises(HTTPException) as failure,
        ):
            await service.catalog("saved", schema=None, search="", limit=50, offset=0)
        self.assertEqual(failure.exception.status_code, 422)
        driver.assert_not_called()

        for driver_error, code in ((TimeoutError("private connection string"), 504), (OSError("private connection string"), 502)):
            driver = SimpleNamespace(connect=AsyncMock(side_effect=driver_error))
            with (
                patch.object(service, "_session", stub_registry_session),
                patch.object(service, "_stored", return_value=stored),
                patch.object(service, "_credentials", return_value={"password": "private connection string"}),
                patch.object(sources, "_driver", return_value=driver),
                self.assertRaises(HTTPException) as failure,
            ):
                await service.catalog("saved", schema=None, search="", limit=50, offset=0)
            self.assertEqual(failure.exception.status_code, code)
            self.assertNotIn("private connection string", failure.exception.detail)
