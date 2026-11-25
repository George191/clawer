"""System/business connection isolation with saved, encrypted credentials."""

from datetime import datetime, timezone
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.engine import make_url

from app.storage import business_database as business
from app.storage.connection_credentials import connection_cipher
from app.storage import postgres_client
from app.web.services import data_connections


SECRET = "explicit-test-connection-key"


def source(**changes):
    row = {"id": uuid4(), "name": "Business source", "engine": "postgresql", "paused_at": None,
           "updated_at": datetime.now(timezone.utc),
           "config": {"host": "business.example.test", "port": 5432, "database": "analytics",
                      "username": "etl", "auth": "password", "tls": False},
           "credentials_ciphertext": connection_cipher(SECRET).encrypt(
               json.dumps({"password": "test-only:reserved@password"}).encode()).decode()}
    return {**row, **changes}


class BusinessDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_unselected_connection_never_uses_the_system_url(self):
        registry = SimpleNamespace(connect=AsyncMock(), fetch_one=AsyncMock(return_value=None))
        with patch.object(business, "get_pg_client", return_value=registry), \
             patch.object(postgres_client.PostgresClient, "connect", new_callable=AsyncMock) as connect:
            with self.assertRaisesRegex(business.BusinessDatabaseNotConfigured, "Select a PostgreSQL business database"):
                await business.BusinessPostgresClient().connect(max_retries=1)
        connect.assert_not_awaited()
        self.assertIn("app.etl_business_connection", registry.fetch_one.await_args.args[0])

    async def test_worker_uses_saved_business_credentials_and_its_transport(self):
        row = source()
        with patch.object(business.settings, "connection_secret_key", SECRET), \
             patch.object(business, "selected_business_connection", new_callable=AsyncMock, return_value=row), \
             patch.object(postgres_client.PostgresClient, "connect", new_callable=AsyncMock) as connect:
            client = business.BusinessPostgresClient()
            await client.connect(max_retries=1)
        url = make_url(client._url)
        self.assertEqual((url.host, url.database, url.username, url.password),
                         ("business.example.test", "analytics", "etl", "test-only:reserved@password"))
        self.assertEqual(client.connection_id, str(row["id"]))
        self.assertIsNone(client._schema)  # Preserve the business account's configured search path.
        self.assertEqual(client.sslmode, "disable")
        self.assertIs(client._connect_args["ssl"], False)
        connect.assert_awaited_once_with(max_retries=1)

    async def test_paused_or_non_postgres_selection_is_rejected_before_connecting(self):
        for changes in ({"paused_at": datetime.now(timezone.utc)}, {"engine": "mysql"}):
            registry = SimpleNamespace(connect=AsyncMock(), fetch_one=AsyncMock(return_value=source(**changes)))
            with self.subTest(changes=changes), patch.object(business, "get_pg_client", return_value=registry):
                with self.assertRaises(business.BusinessDatabaseNotConfigured):
                    await business.selected_business_connection()

    async def test_selection_checks_revision_before_probing_or_binding(self):
        row = source()
        session = SimpleNamespace(execute=AsyncMock())
        @asynccontextmanager
        async def context():
            yield session
        service = object.__new__(data_connections.DataConnections)
        service._session = context
        service._stored = AsyncMock(return_value=row)
        service._probe_result = AsyncMock()
        with self.assertRaises(HTTPException) as failure:
            await service.select_business_database(str(row["id"]), datetime(2020, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(failure.exception.status_code, 409)
        service._probe_result.assert_not_awaited()
        self.assertFalse(any("INSERT" in str(call.args[0]) for call in session.execute.await_args_list))

    async def test_selected_source_cannot_be_deleted_or_changed_in_place(self):
        session = SimpleNamespace(scalar=AsyncMock(return_value=True))
        with self.assertRaises(HTTPException) as failure:
            await data_connections.DataConnections._require_not_business_connection(session, uuid4())
        self.assertEqual(failure.exception.status_code, 409)

    async def test_selection_probes_saved_connection_and_commits_binding_before_reset(self):
        row = source()
        events = []
        session = SimpleNamespace(execute=AsyncMock())
        @asynccontextmanager
        async def context():
            yield session
            events.append("committed")
        async def close():
            events.append("closed")
        service = object.__new__(data_connections.DataConnections)
        service._session = context
        service._stored = AsyncMock(return_value=row)
        service._credentials = AsyncMock(return_value={"password": "test-password"})
        service._probe_result = AsyncMock(return_value={"ok": True})
        service.describe_warehouse = AsyncMock(return_value={"connectionId": str(row["id"])})
        with patch.object(business.settings, "connection_secret_key", SECRET), \
             patch.object(data_connections, "get_business_pg_client", return_value=SimpleNamespace(close=close)):
            result = await service.select_business_database(str(row["id"]), row["updated_at"])
        self.assertEqual(result["connectionId"], str(row["id"]))
        self.assertEqual(events, ["committed", "closed"])
        service._probe_result.assert_awaited_once()
        binding = session.execute.await_args_list[-1]
        self.assertIn("INSERT INTO app.etl_business_connection", str(binding.args[0]))
        self.assertEqual(binding.args[1], {"id": row["id"]})


class BusinessDatabaseConfigurationTests(unittest.TestCase):
    def test_connection_encryption_key_is_not_serialized(self):
        self.assertFalse("connection_secret_key" in business.settings.model_dump())
        self.assertFalse("SECRET_KEY" in business.settings.model_dump(by_alias=True))

    def test_system_database_cannot_be_selected_for_business_writes(self):
        row = source()
        row["config"]["database"] = "www.astral.io"
        with patch.object(business.settings, "pg_url", "postgresql+asyncpg://system@localhost/www.astral.io"):
            with self.assertRaisesRegex(business.BusinessDatabaseNotConfigured, "reserved for app tables"):
                business.business_connection_options(row)

    def test_corrupt_credentials_and_missing_secret_fail_explicitly(self):
        for key, row in [("", source()), (SECRET, source(credentials_ciphertext="corrupt"))]:
            with self.subTest(key_configured=bool(key)), patch.object(business.settings, "connection_secret_key", key):
                with self.assertRaises(business.BusinessDatabaseNotConfigured):
                    business.business_connection_options(row)

    def test_tls_selection_keeps_certificate_verification(self):
        row = source()
        row["config"]["tls"] = True
        with patch.object(business.settings, "connection_secret_key", SECRET):
            _, options = business.business_connection_options(row)
        self.assertTrue(options["ssl"].check_hostname)

    def test_system_url_requires_an_explicit_database_and_keeps_app_schema(self):
        self.assertEqual(postgres_client.PostgresClient()._schema, "app")
        with patch.object(postgres_client.settings, "pg_url", "postgresql+asyncpg://system@localhost"):
            with self.assertRaisesRegex(ValueError, "include the system database name"):
                postgres_client._resolved_pg_url()
