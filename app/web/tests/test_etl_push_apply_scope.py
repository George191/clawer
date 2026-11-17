"""Dedicated Push target stays outside all generic workspace entry points."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from ipaddress import ip_address
import os
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch
from uuid import uuid4

import asyncpg
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.web.api.models.push_inbox_apply import PUSH_APPLY_SCHEMA, PUSH_APPLY_TARGET
from app.web.core.config import settings
from app.web.services.postgres_sync_source import read_batch_sync_snapshot, review_batch_source
from app.web.services.data_connections import DataConnections
from app.web.services.etl_layers import EtlLayers, _PUSH_APPLY_BINDING, _PUSH_APPLY_RECEIPTS
from app.web.services.etl_workspace import EtlWorkspace

_URL = os.getenv("ONTOLOGY_PUSH_APPLY_SCOPE_TEST_URL")
_OTHER = "ts_push_inbox_sandbox_other"
_QUALIFIED = f"{PUSH_APPLY_SCHEMA}.{PUSH_APPLY_TARGET}"


class TestImmediatePushScope(IsolatedAsyncioTestCase):
    async def test_reserved_schema_aliases_and_inbound_rename_are_denied_before_io(self):
        layers, workspace = EtlLayers(), EtlWorkspace()
        alias = PUSH_APPLY_SCHEMA.removeprefix("ts_")

        @asynccontextmanager
        async def unexpected(*, repeatable_read=False):
            raise AssertionError("A prohibited generic action opened a database session")
            yield

        layers.session = workspace._session = unexpected
        for method, args in (
            (layers.tables, (alias,)),
            (layers.object_types, (alias,)),
            (layers.get_table, (alias, "another_fixture")),
            (layers.preview, (alias, PUSH_APPLY_TARGET)),
            (layers.evidence, (alias, PUSH_APPLY_TARGET)),
            (layers.columns, (alias, PUSH_APPLY_TARGET)),
            (layers.data, (alias, PUSH_APPLY_TARGET)),
            (layers.partitions, (alias, PUSH_APPLY_TARGET)),
            (workspace.patch_row, (alias, PUSH_APPLY_TARGET, {})),
            (workspace.delete_row, (alias, PUSH_APPLY_TARGET, {})),
            (workspace.create_table, (alias, {"name": PUSH_APPLY_TARGET})),
            (workspace.publications, (alias, PUSH_APPLY_TARGET)),
            (workspace.save_publication, (alias, PUSH_APPLY_TARGET, {})),
            (workspace.delete_publication, (alias, PUSH_APPLY_TARGET, "old")),
            (workspace.rename_schema, (alias, {"name": _OTHER})),
            (workspace.rename_schema, (_OTHER, {"name": alias})),
            (workspace.rename_schema, (alias, {"alias": "Looks ordinary"})),
            (workspace.delete_schema, (alias,)),
            (workspace.save_connection, ({"name": "test", "schemas": [alias]},)),
        ):
            with self.subTest(method=method.__name__, args=args), self.assertRaises(HTTPException) as error:
                await method(*args)
            self.assertEqual(error.exception.status_code, 404)

    async def test_batch_source_review_and_runtime_deny_before_connecting(self):
        revision = datetime.now(timezone.utc)
        with patch("app.web.services.postgres_sync_source._saved_loopback_source", side_effect=AssertionError):
            with self.assertRaises(HTTPException) as review:
                await review_batch_source(None, "test", revision, uuid4(), schema=PUSH_APPLY_SCHEMA, table=PUSH_APPLY_TARGET)
            self.assertEqual(review.exception.status_code, 422)
            with self.assertRaises(HTTPException) as runtime:
                await read_batch_sync_snapshot(
                    None, "test", expected_updated_at=revision, schema=PUSH_APPLY_SCHEMA, table=PUSH_APPLY_TARGET,
                    columns=["ordinal"], key_column="ordinal",
                )
            self.assertEqual(runtime.exception.status_code, 422)


@skipUnless(_URL, "Set ONTOLOGY_PUSH_APPLY_SCOPE_TEST_URL to a disposable PostgreSQL DB")
class TestStoredPushedTargetScope(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        url = make_url(_URL)
        if (url.drivername != "postgresql+asyncpg" or url.database != "ontology_push_apply_scope_regression"
            or url.query or not ip_address(url.host or "").is_loopback):
            self.fail("Use exact local disposable ontology_push_apply_scope_regression database")
        self.engine = create_async_engine(url)
        self.addAsyncCleanup(self.engine.dispose)
        conn = await asyncpg.connect(url.set(drivername="postgresql").render_as_string(hide_password=False))
        self.addAsyncCleanup(conn.close)
        self.conn = conn
        # No IF NOT EXISTS: fixture teardown cannot adopt an existing namespace.
        await self._write(
            f"CREATE SCHEMA {PUSH_APPLY_SCHEMA}", f"CREATE SCHEMA {_OTHER}",
            f"CREATE TABLE {_QUALIFIED} (ordinal bigint PRIMARY KEY)",
            f"CREATE TABLE {_OTHER}.{PUSH_APPLY_TARGET} (ordinal bigint PRIMARY KEY)",
            f"INSERT INTO {_QUALIFIED} VALUES (1)",
            """CREATE TABLE public.ontology_objects(schema_name text, table_name text, is_active bool,
                                                       data_type text, description text)""",
            """CREATE TABLE public.flow_publications(token text, schema_name text, table_name text,
                                                       scope jsonb, valid_from timestamptz, valid_until timestamptz)""",
            f"""INSERT INTO public.flow_publications VALUES ('legacy-link', '{PUSH_APPLY_SCHEMA}',
                '{PUSH_APPLY_TARGET}', '{{}}', now()-interval '1 hour', now()+interval '1 hour')""",
        )
        self.addAsyncCleanup(
            self._write, "DROP TABLE public.flow_publications",
            "DROP TABLE public.ontology_objects", f"DROP SCHEMA {PUSH_APPLY_SCHEMA} CASCADE", f"DROP SCHEMA {_OTHER} CASCADE",
        )
        owner = self

        class TestPostgres:
            async def connect(self, **kwargs):
                pass

            @asynccontextmanager
            async def session(self):
                async with AsyncSession(bind=owner.engine) as session:
                    async with session.begin():
                        yield session

        self.layers = EtlLayers()
        self.layers._pg = TestPostgres()
        self.workspace = EtlWorkspace()
        self.workspace._layers = self.layers
        self.workspace._session = self.layers._pg.session
        self.connections = DataConnections()

        @asynccontextmanager
        async def same_db(connection_id):
            yield self.conn

        self.connections._postgres_catalog = same_db

    async def _write(self, *statements):
        async with self.engine.begin() as connection:
            for statement in statements:
                await connection.execute(text(statement))

    async def test_catalog_redacts_reserved_schema_and_legacy_publication_is_unusable(self):
        schemas = {row["schemaName"] for row in await self.layers.layers()}
        self.assertNotIn(PUSH_APPLY_SCHEMA, schemas)
        self.assertIn(_OTHER, schemas)
        self.assertEqual([item["name"] for item in await self.layers.tables(_OTHER)], [PUSH_APPLY_TARGET])
        self.assertNotIn(PUSH_APPLY_SCHEMA, (await self.workspace.connections())[0]["schemas"])
        await self._write(
            "CREATE TABLE public.flow_connections(id text, name text, database_name text, schemas jsonb)",
            f"""INSERT INTO public.flow_connections VALUES ('old', 'old', current_database(),
                    '["{PUSH_APPLY_SCHEMA}","{_OTHER}"]')""",
        )
        self.addAsyncCleanup(self._write, "DROP TABLE public.flow_connections")
        self.assertEqual((await self.workspace.connections())[0]["schemas"], [_OTHER])
        self.assertNotIn(PUSH_APPLY_SCHEMA, (await self.connections.catalog("fixture", schema=None, search="", limit=5, offset=0)).schemas)
        with self.assertRaises(HTTPException) as error:
            await self.connections.catalog_columns("fixture", schema=PUSH_APPLY_SCHEMA, table=PUSH_APPLY_TARGET)
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(
            [column.name for column in (await self.connections.catalog_columns(
                "fixture", schema=_OTHER, table=PUSH_APPLY_TARGET,
            )).columns],
            ["ordinal"],
        )
        with patch.object(settings, "PUSH_INBOX_SANDBOX_ENABLED", False):
            with self.assertRaises(HTTPException) as direct:
                await self.layers.data(PUSH_APPLY_SCHEMA, PUSH_APPLY_TARGET)
            self.assertEqual(direct.exception.status_code, 404)
            with self.assertRaises(HTTPException) as token:
                await self.workspace.published_data("legacy-link", {"limit": 1})
            self.assertEqual(token.exception.status_code, 404)

    async def test_fixed_target_blocks_raw_even_without_acceptance_tables(self):
        with patch.object(settings, "PUSH_INBOX_SANDBOX_ENABLED", False):
            with self.assertRaises(HTTPException) as blocked:
                await self.layers.query("SELECT 1")
            self.assertEqual(blocked.exception.status_code, 403)
        await self._write(f"DROP TABLE {_QUALIFIED}")
        self.assertEqual((await self.layers.query("SELECT 1 AS result"))["rows"], [{"result": 1}])

    async def test_archived_binding_or_result_blocks_raw_without_target_and_acceptance_tables(self):
        await self._write(
            f"DROP TABLE {_QUALIFIED}",
            f"CREATE TABLE {_PUSH_APPLY_BINDING} (scope integer PRIMARY KEY)",
            f"CREATE TABLE {_PUSH_APPLY_RECEIPTS} (id integer PRIMARY KEY)",
        )
        self.addAsyncCleanup(self._write, f"DROP TABLE {_PUSH_APPLY_RECEIPTS}", f"DROP TABLE {_PUSH_APPLY_BINDING}")
        self.assertEqual((await self.layers.query("SELECT 1 AS result"))["rows"], [{"result": 1}])
        await self._write(f"INSERT INTO {_PUSH_APPLY_BINDING} VALUES (1)")
        with self.assertRaises(HTTPException) as binding:
            await self.layers.query("SELECT 1")
        self.assertEqual(binding.exception.status_code, 403)
        await self._write(f"DELETE FROM {_PUSH_APPLY_BINDING}", f"INSERT INTO {_PUSH_APPLY_RECEIPTS} VALUES (1)")
        with self.assertRaises(HTTPException) as receipt:
            await self.layers.query("SELECT 1")
        self.assertEqual(receipt.exception.status_code, 403)
