"""Real PostgreSQL persistence and a real isolated PostgreSQL connection probe.

Other engines' network probes are explicitly mocked. Refuses every database except
the disposable ontology_creation_regression instance.
"""

import asyncio
from importlib import import_module
from datetime import datetime
import json
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from app.storage.postgres_client import PostgresClient
from app.web.api.dependencies.common import get_current_active_superuser
from app.web.api.models.data_connections import DataConnectionPauseChange, DataConnectionPayload
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
            await session.execute(text("DROP TABLE IF EXISTS app.helio_data_connections"))
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
        return await self.pg.fetch_one("SELECT * FROM app.helio_data_connections WHERE id=CAST(:id AS uuid)", {"id": identifier})

    async def test_mongodb_install_and_upgrade_preserve_saved_sources(self):
        maintenance = import_module("app.web.alembic.versions.ab69e2f48a0d_ontology_mongodb_source")

        def apply(session, action):
            with Operations.context(MigrationContext.configure(session.connection())):
                action()

        existing = await self.service.save(payload(), "admin")
        old = await self.stored(existing["id"])
        redis = await self.service.save(payload(
            engine="redis", config={**PAYLOAD["config"], "port": 6379, "database": "0"},
        ), "admin")
        old_redis = await self.stored(redis["id"])
        async with self.pg.session() as session:
            await session.run_sync(apply, maintenance.downgrade)
        self.assertEqual((await migrate(self.pg))["changes"], ["enable_mongodb_source"])
        self.assertFalse((await migrate(self.pg))["ready"])
        async with self.pg.session() as session:
            await session.run_sync(apply, maintenance.upgrade)
            await session.run_sync(apply, maintenance.upgrade)
        self.assertTrue((await migrate(self.pg))["ready"])
        self.assertEqual(old, await self.stored(existing["id"]))
        mongo = payload(engine="mongodb", config={**PAYLOAD["config"], "port": 27017})
        saved = await self.service.save(mongo, "admin")
        stored = await self.stored(saved["id"])
        self.assertEqual(saved["engine"], "mongodb")
        self.assertEqual(saved["config"]["authDatabase"], "admin")
        self.assertFalse(saved["catalogAvailable"])
        self.assertNotIn(PAYLOAD["credentials"]["password"], json.dumps(dict(stored), default=str))
        self.assertTrue((await self.service.test_saved(saved["id"]))["ok"])
        async with self.pg.session() as session:
            with self.assertRaisesRegex(RuntimeError, "while MongoDB sources are saved"):
                await session.run_sync(apply, maintenance.downgrade)
        await self.service.delete(saved["id"])
        async with self.pg.session() as session:
            await session.run_sync(apply, maintenance.downgrade)
        self.assertTrue((await migrate(self.pg, apply=True))["ready"])
        self.assertEqual(old, await self.stored(existing["id"]))
        self.assertEqual(old_redis, await self.stored(redis["id"]))

    async def test_migration_check_apply_and_repeat_preserve_other_schema_definitions(self):
        async with self.pg.session() as session:
            await session.execute(text("DROP TABLE app.helio_data_connections"))
        before = await self.pg.fetch_all("SELECT oid,nspname FROM pg_namespace ORDER BY oid")
        check = await migrate(self.pg)
        self.assertFalse(check["ready"])
        self.assertFalse((await self.pg.fetch_one("SELECT to_regclass('app.helio_data_connections') AS object"))["object"])
        applied = await migrate(self.pg, apply=True)
        first = await self.pg.fetch_one("SELECT 'app.helio_data_connections'::regclass::oid AS oid")
        repeated = await migrate(self.pg, apply=True)
        self.assertTrue(applied["ready"] and repeated["ready"])
        self.assertEqual(repeated["changes"], [])
        self.assertEqual(first, await self.pg.fetch_one("SELECT 'app.helio_data_connections'::regclass::oid AS oid"))
        self.assertEqual(before, await self.pg.fetch_all("SELECT oid,nspname FROM pg_namespace ORDER BY oid"))
        self.assertEqual(await self.service.list(), [])

    async def test_engine_upgrade_preserves_sources_and_accepts_new_bucket_credentials(self):
        original = await self.service.save(payload(), "admin")
        original_row = await self.stored(original["id"])
        migration = import_module("app.web.alembic.versions.18f214f739c5_ontology_source_engines")

        def apply(session, action):
            with Operations.context(MigrationContext.configure(session.connection())):
                action()

        async with self.pg.session() as session:
            await session.run_sync(apply, migration.downgrade)
        self.assertEqual((await migrate(self.pg))["changes"], ["expand_source_engines", "enable_mongodb_source"])
        self.assertTrue((await migrate(self.pg, apply=True))["ready"])
        async with self.pg.session() as session:
            await session.run_sync(apply, migration.upgrade)
        self.assertEqual((await migrate(self.pg, apply=True))["changes"], [])
        self.assertEqual(await self.stored(original["id"]), original_row)

        bucket_config = dict(host="objects.example.org", port=443, database="", auth="access_key",
                             bucket="analytics", region="us-east-1", username="reader", provider="aws", tls=True)
        new_source = payload(name="Archive", engine="s3", config=bucket_config,
                             credentials={"secretAccessKey": "isolated-storage-secret"})
        saved = await self.service.save(new_source, "admin")
        self.assertEqual(saved["config"], bucket_config)
        self.assertNotIn("isolated-storage-secret", json.dumps(saved))
        self.assertTrue((await self.service.test_saved(saved["id"]))["ok"])
        self.assertEqual(self.probe.await_args.args[0].config, new_source.config)
        self.assertEqual(self.probe.await_args.args[1], {"secretAccessKey": "isolated-storage-secret"})
        with self.assertRaises(DBAPIError):
            async with self.pg.session() as session:
                await session.run_sync(apply, migration.downgrade)
        self.assertTrue((await migrate(self.pg))["ready"])
        self.assertEqual((await self.stored(saved["id"]))["engine"], "s3")

    async def test_crud_encrypts_and_keeps_original_ciphertext_when_password_is_blank(self):
        saved = await self.service.save(payload(), "admin")
        self.assertTrue(await self.service.exists(UUID(saved["id"])))
        row = await self.stored(saved["id"])
        ciphertext = row["credentials_ciphertext"]
        self.assertNotIn(PAYLOAD["credentials"]["password"], json.dumps(dict(row), default=str))
        fresh_cipher = sources.credential_cipher()
        self.assertEqual(json.loads(fresh_cipher.decrypt(ciphertext.encode())), PAYLOAD["credentials"])
        listed = await self.service.list()
        self.assertEqual([{k: v for k, v in row.items() if k != "capabilities"} for row in listed], [saved])
        self.assertNotIn("capabilities", saved)
        self.assertNotIn("credentials", saved)
        self.assertNotIn("credentials_ciphertext", saved)
        renamed = await self.service.save(payload(name="Renamed source", credentials={}), "admin", saved["id"],
                                          expected_updated_at=datetime.fromisoformat(saved["updatedAt"]))
        self.assertEqual(renamed["name"], "Renamed source")
        self.assertEqual((await self.stored(saved["id"]))["credentials_ciphertext"], ciphertext)
        # A new service instance and cipher can decrypt after a process restart
        # because derivation uses the same explicitly configured stable secret.
        restarted = object.__new__(sources.DataConnections)
        restarted._pg = self.pg
        self.assertTrue((await restarted.test(payload(credentials={}), saved["id"],
                                                   expected_updated_at=datetime.fromisoformat(renamed["updatedAt"])))["ok"])
        self.probe.assert_awaited()
        await self.service.delete(saved["id"])
        self.assertFalse(await self.service.exists(UUID(saved["id"])))
        self.assertEqual(await self.service.list(), [])

    async def test_list_capabilities_use_local_metadata_not_saved_source_or_secrets(self):
        from app.web.services import batch_source_identity as identity

        local = {**PAYLOAD["config"], "host": "127.0.0.1"}
        saved = await self.service.save(payload(name="1 Reviewed source", config=local), "admin")
        await self.service.save(payload(name="2 Hostname source", config={**local, "host": "localhost"}), "admin")
        await self.service.save(payload(name="3 MySQL source", engine="mysql", config=local), "admin")
        marker = uuid4()
        configured = SimpleNamespace(
            SECRET_KEY=SETTINGS.SECRET_KEY, ENVIRONMENT="local",
            model_fields_set={"SECRET_KEY", "ENVIRONMENT"}, BATCH_SYNC_SANDBOX_ENABLED=True,
            BATCH_SYNC_SOURCE_IDENTITY_ENABLED=False, BATCH_SYNC_SOURCE_IDENTITY_MARKER=marker,
            BATCH_SYNC_INCREMENTAL_ENABLED=True, BATCH_SYNC_RANGE_SNAPSHOT_ENABLED=True,
            ONTOLOGY_POSTGRES_SOURCE_PAUSE_ENABLED=True,
        )
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_active_superuser] = lambda: SimpleNamespace(id="admin", is_superuser=True)
        allow = {"status": "available_to_try"}

        def deny(reason):
            return {"status": "unavailable", "reason": reason}

        async def set_proof(*, revision: str, fingerprint: str, is_current: bool = True):
            async with self.pg.session() as session:
                await session.execute(text("""
                    INSERT INTO app.helio_batch_source_identities
                        (connection_id, source_updated_at, fingerprint, is_current)
                    VALUES (CAST(:id AS uuid), CAST(CAST(:revision AS text) AS timestamptz), :fingerprint, :is_current)
                    ON CONFLICT (connection_id) DO UPDATE SET
                        source_updated_at=EXCLUDED.source_updated_at, fingerprint=EXCLUDED.fingerprint,
                        is_current=EXCLUDED.is_current
                """), dict(id=saved["id"], revision=revision, fingerprint=fingerprint, is_current=is_current))

        def import_driver(engine):
            if engine == "mysql":
                raise RuntimeError("optional module initialization failed")

        async with self.pg.session() as session:
            await session.execute(text("DROP TABLE IF EXISTS app.helio_batch_source_identities"))

        with (
            patch.object(sources, "settings", configured), patch.object(identity, "settings", configured),
            patch.object(sources, "credential_cipher", side_effect=AssertionError("secret read")),
            patch.object(sources, "_probe", side_effect=AssertionError("source dial")),
            patch("app.web.api.routes.etl.get_data_connections", return_value=self.service),
            patch.object(sources, "_driver", side_effect=import_driver) as driver,
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                async def metadata():
                    res = await client.get("/etl/data-connections")
                    self.assertEqual(res.status_code, 200, res.text)
                    for secret in (str(marker), "fingerprint", "is_current", "credentials_ciphertext",
                                   PAYLOAD["credentials"]["password"]):
                        self.assertNotIn(secret, res.text)
                    return res.json()

                rows = await metadata()
                items = [r["capabilities"] for r in rows]
                self.assertEqual(items[0]["snapshot"], deny("source_identity_registry_unavailable"))
                self.assertEqual(items[0]["catalog"], allow)
                self.assertTrue(rows[0]["catalogAvailable"])
                self.assertEqual(items[1]["snapshot"], deny("numeric_loopback_required"))
                self.assertEqual(items[2]["catalog"], deny("unsupported_engine"))
                self.assertEqual(items[2]["connectionTest"], deny("driver_missing"))
                self.assertEqual([call.args for call in driver.call_args_list], [("postgresql",), ("mysql",)])

                try:
                    async with self.pg.session() as session:
                        await session.execute(text("""
                            CREATE TABLE app.helio_batch_source_identities (
                                connection_id uuid PRIMARY KEY, source_updated_at timestamptz NOT NULL,
                                fingerprint varchar(64) NOT NULL, is_current bool NOT NULL,
                                verified_at timestamptz NOT NULL DEFAULT now())
                        """))
                    self.assertEqual((await metadata())[0]["capabilities"]["snapshot"], allow)
                    configured.BATCH_SYNC_SOURCE_IDENTITY_ENABLED = True
                    fingerprint = identity.expected_fingerprint()
                    before = (await metadata())[0]["capabilities"]
                    for name in ("snapshot", "committedBatch", "reviewedRange"):
                        self.assertEqual(before[name], deny("source_identity_unverified"))
                    for supplied, revision, current in (
                        ("1" * 64, saved["updatedAt"], True),
                        (fingerprint, "2020-01-01T00:00:00Z", True),
                        (fingerprint, saved["updatedAt"], False),
                    ):
                        await set_proof(revision=revision, fingerprint=supplied, is_current=current)
                        self.assertEqual((await metadata())[0]["capabilities"]["snapshot"], deny("source_identity_stale"))
                    await set_proof(revision=saved["updatedAt"], fingerprint=fingerprint)
                    self.assertTrue(all(capability == allow for capability in (await metadata())[0]["capabilities"].values()))
                    configured.BATCH_SYNC_RANGE_SNAPSHOT_ENABLED = False
                    self.assertEqual((await metadata())[0]["capabilities"]["reviewedRange"], deny("mode_disabled"))
                    configured.BATCH_SYNC_SOURCE_IDENTITY_MARKER = None
                    self.assertEqual((await metadata())[0]["capabilities"]["snapshot"], deny("source_identity_not_configured"))
                    configured.BATCH_SYNC_SOURCE_IDENTITY_ENABLED = False
                    sticky = (await metadata())[0]["capabilities"]
                    self.assertEqual(sticky["connectionTest"], allow)
                    self.assertEqual(sticky["catalog"], allow)
                    self.assertEqual(sticky["snapshot"], deny("source_identity_policy_disabled"))
                    await self.service.change_pause(saved["id"], DataConnectionPauseChange(
                        paused=True, expectedUpdatedAt=saved["updatedAt"],
                    ))
                    configured.ONTOLOGY_POSTGRES_SOURCE_PAUSE_ENABLED = False
                    paused = (await metadata())[0]
                    self.assertFalse(paused["catalogAvailable"])
                    self.assertTrue(all(cap == deny("source_paused") for cap in paused["capabilities"].values()))
                finally:
                    async with self.pg.session() as session:
                        await session.execute(text("DROP TABLE IF EXISTS app.helio_batch_source_identities"))

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

    async def test_real_catalog_is_acl_filtered_paged_and_never_queries_view_rows(self):
        # The explicit isolated fixture is provisioned by a disposable cluster owner.
        role = "catalog_" + uuid4().hex[:16]
        password = "isolated-catalog-role-password"
        async with self.pg.session() as session:
            for statement in (
                f"CREATE ROLE {role} LOGIN PASSWORD '{password}'",
                'CREATE SCHEMA "Catalog Test"',
                'CREATE SCHEMA "Catalog Unreadable"',
                'CREATE TABLE "Catalog Test"."Accounts" (id bigint, tenant integer, summary varchar(20), '
                'protected text, PRIMARY KEY (id, tenant) INCLUDE (summary))',
                'CREATE VIEW "Catalog Test"."Calculated View" AS SELECT (1 / 0) AS danger',
                'CREATE TABLE "Catalog Test"."Hidden Table" (id int)',
                f'GRANT USAGE ON SCHEMA "Catalog Test" TO {role}',
                f'GRANT SELECT(id, tenant, summary) ON "Catalog Test"."Accounts" TO {role}',
                f'GRANT SELECT ON "Catalog Test"."Calculated View" TO {role}',
            ):
                await session.execute(text(statement))

        async def drop_source():
            async with self.pg.session() as session:
                await session.execute(text('DROP SCHEMA "Catalog Test", "Catalog Unreadable" CASCADE'))
                await session.execute(text(f"DROP ROLE {role}"))

        self.addAsyncCleanup(self.pg.close)
        self.addAsyncCleanup(drop_source)
        url = make_url(URL)
        body = DataConnectionPayload(name="Catalog fixture", engine="postgresql",
            config={"host": url.host, "port": url.port or 5432, "database": DATABASE,
                    "username": role, "auth": "password", "tls": False},
            credentials={"password": password})
        with patch.object(sources, "_probe", self.real_probe):
            saved = await self.service.save(body, "isolated-admin")
        self.assertTrue(saved["catalogAvailable"])
        before = await self.stored(saved["id"])

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_active_superuser] = lambda: SimpleNamespace(id="admin", is_superuser=True)
        with patch("app.web.api.routes.etl.get_data_connections", return_value=self.service):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                path = f"/etl/data-connections/{saved['id']}/catalog"
                listed = await client.get(path)
                self.assertEqual(listed.status_code, 200, listed.text)
                self.assertIn("Catalog Test", listed.json()["schemas"])
                self.assertTrue({"Catalog Unreadable", "information_schema", "pg_catalog"}.isdisjoint(listed.json()["schemas"]))
                self.assertEqual(listed.json()["relations"], [])
                page = await client.get(path, params={"schema": "Catalog Test", "limit": 1})
                self.assertEqual(page.json()["relations"], [{"name": "Accounts", "kind": "table"}])
                self.assertTrue(page.json()["hasMore"])
                next_page = await client.get(path, params={"schema": "Catalog Test", "limit": 1, "offset": 1})
                self.assertEqual(next_page.json()["relations"], [{"name": "Calculated View", "kind": "view"}])
                self.assertFalse(next_page.json()["hasMore"])
                search = await client.get(path, params={"schema": "Catalog Test", "search": "calculatED"})
                self.assertEqual(search.json()["relations"], next_page.json()["relations"])
                literal = await client.get(path, params={"schema": "Catalog Test", "search": "%"})
                self.assertEqual(literal.json()["relations"], [])  # Search is a literal, not a LIKE expression.
                columns = await client.get(path + "/columns", params={"schema": "Catalog Test", "table": "Accounts"})
                self.assertEqual(columns.status_code, 200, columns.text)
                self.assertEqual([(col["name"], col["primaryKey"], col["position"]) for col in columns.json()["columns"]],
                                 [("id", True, 1), ("tenant", True, 2), ("summary", False, 3)])
                self.assertEqual(columns.json()["columns"][-1]["type"], "character varying(20)")
                view = await client.get(path + "/columns", params={"schema": "Catalog Test", "table": "Calculated View"})
                self.assertEqual(view.status_code, 200, view.text)  # Evaluating the view would divide by zero.
                self.assertEqual(view.json()["columns"][0]["name"], "danger")
                denied = await client.get(path + "/columns", params={"schema": "Catalog Test", "table": "Hidden Table"})
                self.assertEqual(denied.status_code, 404)
                denied = await client.get(path, params={"schema": "Catalog Unreadable"})
                self.assertEqual(denied.status_code, 404)
                self.assertEqual((await client.get(path, params={"limit": 101})).status_code, 422)
                self.assertNotIn(password, columns.text)
        after = await self.stored(saved["id"])
        self.assertEqual(before, after)

    async def test_source_catalog_references_prove_order_acl_and_visible_bound(self):
        role = "source_fk_" + uuid4().hex[:12]
        password = "isolated-catalog-fk-password"
        async with self.pg.session() as session:
            for sql in (
                f"CREATE ROLE {role} LOGIN PASSWORD '{password}'",
                "CREATE SCHEMA source_refs",
                "CREATE SCHEMA source_refs_hidden",
                "CREATE TABLE source_refs.parent (x bigint, y bigint, PRIMARY KEY (x,y))",
                "CREATE TABLE source_refs.child (a bigint, b bigint, CONSTRAINT private_constraint "
                "FOREIGN KEY (b,a) REFERENCES source_refs.parent (x,y), "
                "FOREIGN KEY (b,a) REFERENCES source_refs.parent (x,y))",
                "CREATE VIEW source_refs.child_view AS SELECT 1/0",
                "CREATE TABLE source_refs.derived () INHERITS (source_refs.child)",
                "CREATE TABLE source_refs.aaa_invalid (a bigint, b bigint)",
                "ALTER TABLE source_refs.aaa_invalid ADD FOREIGN KEY (b,a) "
                "REFERENCES source_refs.parent (x,y) NOT VALID",
                "CREATE TABLE source_refs_hidden.hidden_schema (a bigint, b bigint, "
                "FOREIGN KEY (b,a) REFERENCES source_refs.parent (x,y))",
                f"GRANT USAGE ON SCHEMA source_refs TO {role}",
                f"GRANT SELECT(x,y) ON source_refs.parent TO {role}",
                f"GRANT SELECT(a,b) ON source_refs.child, source_refs.aaa_invalid, source_refs.derived TO {role}",
                f"GRANT SELECT(a,b) ON source_refs_hidden.hidden_schema TO {role}",
                f"GRANT SELECT ON source_refs.child_view TO {role}",
            ):
                await session.execute(text(sql))

        # The ordinary child is initially an inheritance parent, not eligible. Remove the child.
        async def drop_source():
            async with self.pg.session() as session:
                await session.execute(text("DROP SCHEMA source_refs_hidden, source_refs CASCADE"))
                await session.execute(text(f"DROP ROLE {role}"))

        self.addAsyncCleanup(self.pg.close)
        self.addAsyncCleanup(drop_source)
        url = make_url(URL)
        body = DataConnectionPayload(name="FK fixture", engine="postgresql",
            config={"host": url.host, "port": url.port or 5432, "database": DATABASE,
                    "username": role, "tls": False}, credentials={"password": password})
        saved = await self.service.save(body, "isolated-admin")
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_current_active_superuser] = lambda: SimpleNamespace(id="admin", is_superuser=True)
        with patch("app.web.api.routes.etl.get_data_connections", return_value=self.service):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                path = f"/etl/data-connections/{saved['id']}/catalog"
                params = dict(schema="source_refs", table="child", direction="outgoing",
                              expectedUpdatedAt=saved["updatedAt"])
                stale = {**params, "expectedUpdatedAt": "2025-01-01T00:00:00Z"}
                with patch.object(self.service, "_credentials", side_effect=AssertionError("unexpected dial")):
                    self.assertEqual((await client.get(path + "/references", params=stale)).json()["detail"]["code"],
                                     "source_revision_changed")
                    self.assertEqual((await client.get(path + "/references", params={**params, "extra": "no"})).status_code, 422)
                for table in ("child", "derived", "child_view", "missing"):
                    blocked = await client.get(path + "/references", params={**params, "table": table})
                    self.assertEqual(blocked.status_code, 404)
                    self.assertEqual(blocked.json(), {"detail": "Source relation is unavailable or not readable"})
                async with self.pg.session() as session:
                    await session.execute(text("DROP TABLE source_refs.derived"))
                columns = await client.get(path + "/columns", params={"schema": "source_refs", "table": "child"})
                self.assertIs(columns.json()["referencesAvailable"], True)
                view = await client.get(path + "/columns", params={"schema": "source_refs", "table": "child_view"})
                self.assertNotIn("referencesAvailable", view.json())
                outgoing = await client.get(path + "/references", params=params)
                self.assertEqual(outgoing.headers["cache-control"], "private, no-store")
                pairs = [{"fromColumn": "b", "toColumn": "x"}, {"fromColumn": "a", "toColumn": "y"}]
                self.assertEqual(outgoing.json(), {"direction": "outgoing", "references": [
                    {"schema": "source_refs", "table": "parent", "pairs": pairs}], "hasMore": False})
                for hidden in ("private_constraint", password):
                    self.assertNotIn(hidden, outgoing.text)
                incoming = await client.get(path + "/references", params={**params, "table": "parent", "direction": "incoming"})
                self.assertEqual(incoming.json(), {"direction": "incoming", "references": [
                    {"schema": "source_refs", "table": "child", "pairs": pairs}], "hasMore": False})
                async with self.pg.session() as session:
                    await session.execute(text(f"REVOKE SELECT(a) ON source_refs.child FROM {role}"))
                    for index in range(13):
                        name = f"visible_{index:02}"
                        await session.execute(text(f"CREATE TABLE source_refs.{name} (a bigint, b bigint, "
                                                   "FOREIGN KEY (b,a) REFERENCES source_refs.parent (x,y))"))
                        await session.execute(text(f"GRANT SELECT(a,b) ON source_refs.{name} TO {role}"))
                outgoing = await client.get(path + "/references", params=params)
                self.assertEqual(outgoing.json(), {"direction": "outgoing", "references": [], "hasMore": False})
                incoming = await client.get(path + "/references", params={**params, "table": "parent", "direction": "incoming"})
                self.assertEqual([ref["table"] for ref in incoming.json()["references"]], [
                    f"visible_{index:02}" for index in range(12)])
                self.assertTrue(incoming.json()["hasMore"])
                async with self.pg.session() as session:
                    await session.execute(text(f"REVOKE SELECT(y) ON source_refs.parent FROM {role}"))
                incoming = await client.get(path + "/references", params={**params, "table": "parent", "direction": "incoming"})
                self.assertEqual(incoming.json(), {"direction": "incoming", "references": [], "hasMore": False})

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

    async def test_draft_http_cas_blocks_credential_and_probe_access_before_replacement(self):
        saved = await self.service.save(payload(), "admin")
        previous = datetime.fromisoformat(saved["updatedAt"])
        rotated = await self.service.save(payload(name="Current test source"), "admin", saved["id"],
                                          expected_updated_at=previous)
        current = await self.stored(saved["id"])
        # The framework's real validation handler would otherwise expose user-supplied locations.
        app = FastAPI()
        app.add_exception_handler(RequestValidationError, request_exception_handler)
        app.include_router(router)
        app.dependency_overrides[get_current_active_superuser] = lambda: SimpleNamespace(id=uuid4(), is_superuser=True)
        original_credentials = self.service._credentials
        with patch.object(sources, "get_data_connections", return_value=self.service), patch(
            "app.web.api.routes.etl.get_data_connections", return_value=self.service,
        ):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://localfixture") as client:
                test_url = f"/etl/data-connections/test?connectionId={saved['id']}"
                put_url = f"/etl/data-connections/{saved['id']}"
                for body in (PAYLOAD, {**PAYLOAD, "expectedUpdatedAt": saved["updatedAt"]}):
                    with patch.object(self.service, "_credentials", side_effect=AssertionError("stale credentials reached")) as lookup:
                        self.probe.reset_mock()
                        for response in (await client.post(test_url, json=body), await client.put(put_url, json=body)):
                            self.assertEqual(response.status_code, 409, response.text)
                            self.assertEqual(response.json()["detail"]["code"], "source_revision_changed")
                        lookup.assert_not_awaited()
                        self.probe.assert_not_awaited()
                self.assertEqual(await self.stored(saved["id"]), current)
                secret_key = "fixture-unrecognized-key-never-return"
                invalid = await client.put(put_url, json={**PAYLOAD, "credentials": {secret_key: "fixture-value-never-return"}})
                self.assertEqual(invalid.status_code, 422)
                self.assertNotIn(secret_key, invalid.text)
                self.assertNotIn("fixture-value-never-return", invalid.text)
                body = {**PAYLOAD, "expectedUpdatedAt": rotated["updatedAt"],
                        "credentials": {"password": "fixture-new-draft-password"}}
                self.probe.side_effect = RuntimeError("fixture-new-draft-password")
                denied = await client.put(put_url, json=body)
                self.assertEqual(denied.status_code, 422, denied.text)
                self.assertNotIn("fixture-new-draft-password", denied.text)
                self.assertEqual(await self.stored(saved["id"]), current)
                self.probe.side_effect = None
                self.assertTrue((await client.post(test_url, json=body)).json()["ok"])
                self.assertEqual(await self.stored(saved["id"]), current)
                changed = await client.put(put_url, json=body)
                self.assertEqual(changed.status_code, 200, changed.text)
                self.assertNotEqual(changed.json()["updatedAt"], rotated["updatedAt"])
                self.assertNotIn("fixture-new-draft-password", changed.text)
                stored = await self.stored(saved["id"])
                self.assertEqual(stored["config"], current["config"])
                self.assertNotEqual(stored["credentials_ciphertext"], current["credentials_ciphertext"])
                self.assertEqual(await original_credentials(payload(credentials={}), stored), body["credentials"])
                self.assertEqual((await client.post(test_url, json=body)).status_code, 409)
                self.assertTrue((await client.post(f"/etl/data-connections/{saved['id']}/test")).json()["ok"])

    async def test_changed_secret_fails_explicitly_until_credentials_are_replaced(self):
        saved = await self.service.save(payload(), "admin")
        with patch.object(sources, "settings", SimpleNamespace(SECRET_KEY="a-different-explicitly-configured-secret", model_fields_set={"SECRET_KEY"})):
            with self.assertRaises(HTTPException) as failure:
                await self.service.test(payload(credentials={}), saved["id"],
                                        expected_updated_at=datetime.fromisoformat(saved["updatedAt"]))
            self.assertEqual(failure.exception.status_code, 503)
            self.assertIn("cannot be decrypted", failure.exception.detail)
            replaced = await self.service.save(
                payload(credentials={"password": "replacement-test-only-password"}), "admin", saved["id"],
                expected_updated_at=datetime.fromisoformat(saved["updatedAt"]),
            )
            self.assertEqual(replaced["id"], saved["id"])
            self.assertTrue((await self.service.test(payload(credentials={}), saved["id"],
                                                       expected_updated_at=datetime.fromisoformat(replaced["updatedAt"])))["ok"])

    async def test_one_credential_snapshot_is_probed_and_concurrent_edits_are_serialized(self):
        saved = await self.service.save(payload(), "admin")
        original_stored = self.service._stored
        self.probe.reset_mock()
        with patch.object(self.service, "_stored", wraps=original_stored) as read:
            snapshot = await self.service.save(
                payload(name="Snapshot source", credentials={}), "admin", saved["id"],
                expected_updated_at=datetime.fromisoformat(saved["updatedAt"]),
            )
            self.assertEqual(read.await_count, 1)
        self.probe.assert_awaited_once()
        self.assertEqual(self.probe.call_args.args[1], PAYLOAD["credentials"])

        async def edit_during_probe(body, credentials):
            self.assertEqual(credentials, PAYLOAD["credentials"])
            with self.assertRaises(DBAPIError) as cannot_edit:
                async with self.pg.session() as session:
                    await session.execute(text("SET LOCAL lock_timeout = '100ms'"))
                    await session.execute(text("""
                        UPDATE app.helio_data_connections SET name='Concurrent edit', updated_at=clock_timestamp()
                        WHERE id=CAST(:id AS uuid)
                    """), {"id": saved["id"]})
            self.assertEqual(cannot_edit.exception.orig.sqlstate, "55P03")

        self.probe.side_effect = edit_during_probe
        result = await self.service.save(
            payload(name="Settled edit", credentials={}), "admin", saved["id"],
            expected_updated_at=datetime.fromisoformat(snapshot["updatedAt"]),
        )
        self.assertEqual(result["name"], "Settled edit")

    async def test_postgres_pause_waits_for_saved_probe_then_stays_effective_when_switch_is_off(self):
        enabled = SimpleNamespace(SECRET_KEY=SETTINGS.SECRET_KEY, ENVIRONMENT="local",
                                  ONTOLOGY_POSTGRES_SOURCE_PAUSE_ENABLED=True,
                                  model_fields_set={"SECRET_KEY", "ENVIRONMENT"})
        saved = await self.service.save(payload(), "admin")
        original = await self.stored(saved["id"])
        entered, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def held_probe(body, credentials):
            entered.set()
            try:
                await release.wait()
            finally:
                completed.set()

        self.probe.side_effect = held_probe
        with patch.object(sources, "settings", enabled):
            req = DataConnectionPauseChange(paused=True, expectedUpdatedAt=saved["updatedAt"])
            probed = asyncio.create_task(self.service.test_saved(saved["id"]))
            await asyncio.wait_for(entered.wait(), timeout=3)
            pending = asyncio.create_task(self.service.change_pause(saved["id"], req))
            try:
                await asyncio.sleep(0.15)
                self.assertFalse(pending.done())
            finally:
                release.set()
            self.assertTrue((await probed)["ok"])
            paused = await pending
            self.assertTrue(completed.is_set())
            self.assertTrue(paused["pausedAt"])
            self.assertTrue(paused["pauseAvailable"])
            self.assertIsNone(paused["testedAt"])
            self.assertNotEqual(paused["updatedAt"], saved["updatedAt"])
            persisted = await self.stored(saved["id"])
            self.assertEqual(persisted["credentials_ciphertext"], original["credentials_ciphertext"])
            self.assertEqual(persisted["config"], original["config"])
            with self.assertRaises(HTTPException) as stale:
                await self.service.change_pause(saved["id"], req)
            self.assertEqual(stale.exception.detail["code"], "source_revision_changed")

            with patch.object(enabled, "ONTOLOGY_POSTGRES_SOURCE_PAUSE_ENABLED", False), patch.object(
                self.service, "_credentials", side_effect=AssertionError("paused source decrypted")
            ) as decrypt, patch.object(sources, "_driver", side_effect=AssertionError("paused source loaded driver")):
                self.assertFalse((await self.service.list())[0]["pauseAvailable"])
                actions = (
                    self.service.test_saved(saved["id"]), self.service.test(payload(), saved["id"]),
                    self.service.save(payload(), "admin", saved["id"]),
                    self.service.catalog(saved["id"], schema=None, search="", limit=10, offset=0),
                    self.service.catalog_columns(saved["id"], schema="public", table="records"),
                )
                for action in actions:
                    with self.assertRaises(HTTPException) as denied:
                        await action
                    self.assertEqual(denied.exception.detail["code"], "source_paused")
                decrypt.assert_not_called()
                with self.assertRaises(HTTPException) as flag_off:
                    await self.service.change_pause(saved["id"], DataConnectionPauseChange(
                        paused=False, expectedUpdatedAt=paused["updatedAt"],
                    ))
                self.assertEqual(flag_off.exception.status_code, 403)

            resumed = await self.service.change_pause(saved["id"], DataConnectionPauseChange(
                paused=False, expectedUpdatedAt=paused["updatedAt"],
            ))
            self.assertIsNone(resumed["pausedAt"])
            self.assertIsNone(resumed["testedAt"])
            self.assertNotEqual(resumed["updatedAt"], paused["updatedAt"])
            self.assertEqual((await self.stored(saved["id"]))["credentials_ciphertext"], original["credentials_ciphertext"])

    async def test_legacy_registry_upgrade_and_guarded_downgrade(self):
        saved = await self.service.save(payload(), "admin")
        migration = import_module("app.web.alembic.versions.b66fbad735c2_ontology_pg_source_pause")

        def apply(session, action):
            with Operations.context(MigrationContext.configure(session.connection())):
                action()

        async with self.pg.session() as session:
            await session.run_sync(apply, migration.downgrade)
        self.assertEqual({k: v for k, v in (await self.service.list())[0].items() if k != "capabilities"}, saved)
        self.assertFalse((await migrate(self.pg))["ready"])
        self.assertTrue((await migrate(self.pg, apply=True))["ready"])
        self.assertEqual({k: v for k, v in (await self.service.list())[0].items() if k != "capabilities"}, saved)
        enabled = SimpleNamespace(SECRET_KEY=SETTINGS.SECRET_KEY, ENVIRONMENT="local",
                                  ONTOLOGY_POSTGRES_SOURCE_PAUSE_ENABLED=True,
                                  model_fields_set={"SECRET_KEY", "ENVIRONMENT"})
        with patch.object(sources, "settings", enabled):
            paused = await self.service.change_pause(saved["id"], DataConnectionPauseChange(
                paused=True, expectedUpdatedAt=saved["updatedAt"],
            ))
        with self.assertRaisesRegex(RuntimeError, "source pause or untested"):
            async with self.pg.session() as session:
                await session.run_sync(apply, migration.downgrade)
        self.assertEqual((await self.service.list())[0]["pausedAt"], paused["pausedAt"])
