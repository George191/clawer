"""Real PostgreSQL persistence and a real isolated PostgreSQL connection probe.

Other engines' network probes are explicitly mocked. Refuses every database except
the disposable ontology_creation_regression instance.
"""

import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.storage.postgres_client import PostgresClient
from app.web.api.dependencies.common import get_current_active_superuser
from app.web.api.models.data_connections import DataConnectionPayload
from app.web.api.routes.etl import router
from app.web.core.exceptions import request_exception_handler
from app.web.services import data_connections as sources
from tools.ontology_data_connections import migrate


URL = os.environ.get("ONTOLOGY_TEST_DATABASE_URL")
DATABASE = "ontology_creation_regression"
PAYLOAD = {"name": "External test source", "engine": "postgresql",
           "config": {"host": "external.test", "port": 5432, "database": "analytics", "username": "reader", "auth": "password", "tls": True},
           "credentials": {"password": "isolated-secret-must-never-be-returned"}}
SETTINGS = SimpleNamespace(SECRET_KEY="isolated-test-only-secret-32-bytes", model_fields_set={"SECRET_KEY"})


def payload(**changes):
    return DataConnectionPayload.model_validate({**PAYLOAD, **changes})


@unittest.skipUnless(URL, "Requires an isolated ontology_creation_regression PostgreSQL instance")
class DataConnectionsIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if make_url(URL).database != DATABASE:
            raise RuntimeError("Data source tests refuse every database except ontology_creation_regression")
        self.pg = PostgresClient(URL)
        await self.pg.connect(max_retries=1)
        if (await self.pg.fetch_one("SELECT current_database() AS name"))["name"] != DATABASE:
            await self.pg.close()
            raise RuntimeError("Connected database is not ontology_creation_regression")
        async with self.pg.session() as session:
            await session.execute(text("DROP TABLE IF EXISTS public.ontology_data_connections"))
        await migrate(self.pg, apply=True)
        self.service = object.__new__(sources.DataConnections)
        self.service._pg = self.pg
        self.settings_patch = patch.object(sources, "settings", SETTINGS)
        self.settings_patch.start()
        self.real_probe = sources._probe
        # Only the external network probe is replaced. Encryption, SQL, transactions,
        # migrations and API serialization below use their real implementation.
        self.probe_patch = patch.object(sources, "_probe", new_callable=AsyncMock)
        self.probe = self.probe_patch.start()

    async def asyncTearDown(self):
        self.probe_patch.stop()
        self.settings_patch.stop()
        await self.pg.close()

    async def stored(self, identifier):
        return await self.pg.fetch_one("SELECT * FROM public.ontology_data_connections WHERE id=CAST(:id AS uuid)", {"id": identifier})

    async def test_migration_check_apply_and_repeat_preserve_other_schema_definitions(self):
        async with self.pg.session() as session:
            await session.execute(text("DROP TABLE public.ontology_data_connections"))
        before = await self.pg.fetch_all("SELECT oid,nspname FROM pg_namespace ORDER BY oid")
        check = await migrate(self.pg)
        self.assertFalse(check["ready"])
        self.assertFalse((await self.pg.fetch_one("SELECT to_regclass('public.ontology_data_connections') AS object"))["object"])
        applied = await migrate(self.pg, apply=True)
        first = await self.pg.fetch_one("SELECT 'public.ontology_data_connections'::regclass::oid AS oid")
        repeated = await migrate(self.pg, apply=True)
        self.assertTrue(applied["ready"] and repeated["ready"])
        self.assertEqual(repeated["changes"], [])
        self.assertEqual(first, await self.pg.fetch_one("SELECT 'public.ontology_data_connections'::regclass::oid AS oid"))
        self.assertEqual(before, await self.pg.fetch_all("SELECT oid,nspname FROM pg_namespace ORDER BY oid"))
        self.assertEqual(await self.service.list(), [])

    async def test_crud_encrypts_and_keeps_original_ciphertext_when_password_is_blank(self):
        saved = await self.service.save(payload(), "admin")
        row = await self.stored(saved["id"])
        ciphertext = row["credentials_ciphertext"]
        self.assertNotIn(PAYLOAD["credentials"]["password"], json.dumps(dict(row), default=str))
        fresh_cipher = sources.credential_cipher()
        self.assertEqual(json.loads(fresh_cipher.decrypt(ciphertext.encode())), PAYLOAD["credentials"])
        listed = await self.service.list()
        self.assertEqual(listed, [saved])
        self.assertNotIn("credentials", saved)
        self.assertNotIn("credentials_ciphertext", saved)
        renamed = await self.service.save(payload(name="Renamed source", credentials={}), "admin", saved["id"])
        self.assertEqual(renamed["name"], "Renamed source")
        self.assertEqual((await self.stored(saved["id"]))["credentials_ciphertext"], ciphertext)
        # A new service instance and cipher can decrypt after a process restart
        # because derivation uses the same explicitly configured stable secret.
        restarted = object.__new__(sources.DataConnections)
        restarted._pg = self.pg
        self.assertTrue((await restarted.test(payload(credentials={}), saved["id"]))["ok"])
        self.probe.assert_awaited()
        await self.service.delete(saved["id"])
        self.assertEqual(await self.service.list(), [])

    async def test_real_postgresql_probe_authenticates_and_persists_in_isolated_database(self):
        url = make_url(URL)
        body = DataConnectionPayload(name="Actual isolated PostgreSQL", engine="postgresql",
            config={"host": url.host, "port": url.port or 5432, "database": DATABASE,
                    "username": url.username, "auth": "password", "tls": False},
            credentials={"password": url.password})
        with patch.object(sources, "_probe", self.real_probe):
            saved = await self.service.save(body, "isolated-admin")
            self.assertTrue((await self.service.test(body))["ok"])
            bad = body.model_copy(update={"credentials": body.credentials.model_copy(update={"password": type(body.credentials.password)("deliberately-wrong-isolated-password")})})
            self.assertFalse((await self.service.test(bad))["ok"])
            with self.assertRaises(HTTPException):
                await self.service.save(bad.model_copy(update={"name": "Must not be stored"}), "isolated-admin")
            self.assertEqual([source["id"] for source in await self.service.list()], [saved["id"]])
            await self.service.delete(saved["id"])

    async def test_api_serialization_and_validation_never_return_secret_input(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_active_superuser] = lambda: SimpleNamespace(id="admin", is_superuser=True)
        app.add_exception_handler(RequestValidationError, request_exception_handler)
        with patch("app.web.api.routes.etl.get_data_connections", return_value=self.service):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/etl/data-connections", json=PAYLOAD)
                self.assertEqual(response.status_code, 200, response.text)
                identifier = response.json()["id"]
                listed = await client.get("/etl/data-connections")
                self.assertEqual(listed.status_code, 200)
                for result in (response, listed):
                    self.assertNotIn(PAYLOAD["credentials"]["password"], result.text)
                    self.assertNotIn('"credentials":', result.text)
                    self.assertNotIn("credentials_ciphertext", result.text)
                invalid = await client.post("/etl/data-connections", json={**PAYLOAD, "config": {**PAYLOAD["config"], "host": "https://invalid.test/"}})
                self.assertEqual(invalid.status_code, 422)
                self.assertNotIn(PAYLOAD["credentials"]["password"], invalid.text)
                self.assertNotIn('"input"', invalid.text)
                deleted = await client.delete(f"/etl/data-connections/{identifier}")
                self.assertEqual(deleted.status_code, 200)

    async def test_save_performs_its_own_probe_and_failed_probe_never_writes(self):
        self.probe.side_effect = RuntimeError("remote driver echoed " + PAYLOAD["credentials"]["password"])
        failed = await self.service.test(payload())
        self.assertFalse(failed["ok"])
        self.assertNotIn(PAYLOAD["credentials"]["password"], json.dumps(failed))
        with self.assertRaises(HTTPException) as failure:
            await self.service.save(payload(), "admin")
        self.assertEqual(failure.exception.status_code, 422)
        self.assertEqual(self.probe.await_count, 2)
        self.assertEqual(await self.service.list(), [])

    async def test_changed_secret_fails_explicitly_until_credentials_are_replaced(self):
        saved = await self.service.save(payload(), "admin")
        with patch.object(sources, "settings", SimpleNamespace(SECRET_KEY="a-different-explicitly-configured-secret", model_fields_set={"SECRET_KEY"})):
            with self.assertRaises(HTTPException) as failure:
                await self.service.test(payload(credentials={}), saved["id"])
            self.assertEqual(failure.exception.status_code, 503)
            self.assertIn("cannot be decrypted", failure.exception.detail)
            replaced = await self.service.save(payload(credentials={"password": "replacement-test-only-password"}), "admin", saved["id"])
            self.assertEqual(replaced["id"], saved["id"])
            self.assertTrue((await self.service.test(payload(credentials={}), saved["id"]))["ok"])

    async def test_one_credential_snapshot_is_probed_and_concurrent_edits_are_rejected(self):
        saved = await self.service.save(payload(), "admin")
        original_stored = self.service._stored
        self.probe.reset_mock()
        with patch.object(self.service, "_stored", wraps=original_stored) as read:
            await self.service.save(payload(name="Snapshot source", credentials={}), "admin", saved["id"])
            self.assertEqual(read.await_count, 1)
        self.probe.assert_awaited_once()
        self.assertEqual(self.probe.call_args.args[1], PAYLOAD["credentials"])

        async def edit_during_probe(body, credentials):
            self.assertEqual(credentials, PAYLOAD["credentials"])
            async with self.pg.session() as session:
                await session.execute(text("UPDATE public.ontology_data_connections SET name='Concurrent edit', updated_at=clock_timestamp() WHERE id=CAST(:id AS uuid)"), {"id": saved["id"]})

        self.probe.side_effect = edit_during_probe
        with self.assertRaises(HTTPException) as failure:
            await self.service.save(payload(name="Stale save", credentials={}), "admin", saved["id"])
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual((await self.stored(saved["id"]))["name"], "Concurrent edit")
