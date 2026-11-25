"""Dedicated Push target stays outside all generic workspace entry points."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib import import_module
from ipaddress import ip_address
import os
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch
from uuid import uuid4

import asyncpg
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.web.api.models.batch_syncs import TARGET_SCHEMA as BATCH_RANGE_TARGET_SCHEMA
from app.web.api.models.push_inbox_apply import PUSH_APPLY_SCHEMA, PUSH_APPLY_TARGET
from app.web.core.config import settings
from app.web.services.batch_snapshot_range_scope import BATCH_RANGE_SOURCE_SCHEMA, BATCH_RANGE_TARGET
from app.web.services.pipeline_handoff_scope import HANDOFF_PRIVATE_SCHEMA, HANDOFF_PROTECTED_REGCLASSES
from app.web.services.snapshot_intent_scope import (
    SNAPSHOT_INTENT_DELIVERY_REGCLASS, SNAPSHOT_INTENT_FROZEN_REGCLASSES,
    SNAPSHOT_INTENT_PRIVATE_SCHEMA, SNAPSHOT_INTENT_PROTECTED_REGCLASSES,
)
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

    async def test_handoff_schema_rejects_generic_operations_before_io(self):
        layers, workspace = EtlLayers(), EtlWorkspace()
        private = HANDOFF_PRIVATE_SCHEMA

        @asynccontextmanager
        async def unexpected(*, repeatable_read=False):
            raise AssertionError("A prohibited generic action opened a database session")
            yield

        layers.session = workspace._session = unexpected
        for method, args in (
            (layers.tables, (private,)), (layers.columns, (private, "copy_rows")),
            (layers.data, (private, "copy_rows")), (layers.object_types, (private,)),
            (workspace.create_table, (private, {"name": "copy_rows"})),
            (workspace.patch_row, (private, "copy_rows", {})),
            (workspace.publications, (private, "copy_rows")),
            (workspace.rename_schema, (_OTHER, {"name": private})),
            (workspace.rename_schema, (private, {"name": _OTHER})),
            (workspace.delete_schema, (private,)),
        ):
            with self.subTest(method=method.__name__, args=args), self.assertRaises(HTTPException) as error:
                await method(*args)
            self.assertEqual(error.exception.status_code, 404)
        with patch("app.web.services.postgres_sync_source._saved_loopback_source", side_effect=AssertionError):
            with self.assertRaises(HTTPException) as error:
                await review_batch_source(None, "test", datetime.now(timezone.utc), uuid4(),
                                          schema=private, table="copy_rows")
            self.assertEqual(error.exception.status_code, 422)

    async def test_snapshot_intent_schema_rejects_all_generic_operations_before_io(self):
        layers, workspace = EtlLayers(), EtlWorkspace()
        private = SNAPSHOT_INTENT_PRIVATE_SCHEMA

        @asynccontextmanager
        async def unexpected(*, repeatable_read=False):
            raise AssertionError("A prohibited generic operation opened a database session")
            yield

        layers.session = workspace._session = unexpected
        for method, args in (
            (layers.tables, (private,)), (layers.object_types, (private,)), (layers.preview, (private, "records")),
            (layers.data, (private, "records")), (layers.partitions, (private, "records")),
            (workspace.save_connection, ({"name": "test", "schemas": [private]},)),
            (workspace.create_table, (private, {"name": "records"})),
            (workspace.patch_row, (private, "records", {})), (workspace.delete_row, (private, "records", {})),
            (workspace.publications, (private, "records")), (workspace.save_publication, (private, "records", {})),
            (workspace.delete_publication, (private, "records", "old")),
            (workspace.rename_schema, (_OTHER, {"name": private})),
            (workspace.rename_schema, (private, {"name": _OTHER})), (workspace.delete_schema, (private,)),
        ):
            with self.subTest(method=method.__name__), self.assertRaises(HTTPException) as error:
                await method(*args)
            self.assertEqual(error.exception.status_code, 404)
        with patch("app.web.services.postgres_sync_source._saved_loopback_source", side_effect=AssertionError):
            for method in (
                review_batch_source(None, "test", datetime.now(timezone.utc), uuid4(), schema=private, table="records"),
                read_batch_sync_snapshot(None, "test", expected_updated_at=datetime.now(timezone.utc),
                                         schema=private, table="records", columns=["id"], key_column="id"),
            ):
                with self.assertRaises(HTTPException) as denied:
                    await method
                self.assertEqual(denied.exception.status_code, 422)

    async def test_range_snapshot_is_not_a_generic_snapshot_or_workspace_table(self):
        workspace = EtlWorkspace()
        layers = EtlLayers()
        revision = datetime.now(timezone.utc)

        @asynccontextmanager
        async def unexpected(*, repeatable_read=False):
            raise AssertionError("A prohibited generic action opened a database session")
            yield

        layers.session = workspace._session = unexpected
        for method, args in (
            (layers.data, (BATCH_RANGE_TARGET_SCHEMA, BATCH_RANGE_TARGET)),
            (layers.preview, (BATCH_RANGE_TARGET_SCHEMA, BATCH_RANGE_TARGET)),
            (workspace.create_table, (BATCH_RANGE_TARGET_SCHEMA, {"name": BATCH_RANGE_TARGET})),
            (workspace.patch_row, (BATCH_RANGE_TARGET_SCHEMA, BATCH_RANGE_TARGET, {})),
            (workspace.publications, (BATCH_RANGE_TARGET_SCHEMA, BATCH_RANGE_TARGET)),
            (workspace.rename_schema, (_OTHER, {"name": BATCH_RANGE_SOURCE_SCHEMA})),
        ):
            with self.subTest(method=method.__name__), self.assertRaises(HTTPException) as error:
                await method(*args)
            self.assertEqual(error.exception.status_code, 404)
        with patch("app.web.services.postgres_sync_source._saved_loopback_source", side_effect=AssertionError):
            with self.assertRaises(HTTPException) as error:
                await review_batch_source(None, "test", revision, uuid4(), schema=BATCH_RANGE_SOURCE_SCHEMA, table="items")
            self.assertEqual(error.exception.status_code, 422)
            with self.assertRaises(HTTPException) as error:
                await read_batch_sync_snapshot(
                    None, "test", expected_updated_at=revision, schema=BATCH_RANGE_SOURCE_SCHEMA, table="items",
                    columns=["id"], key_column="id",
                )
            self.assertEqual(error.exception.status_code, 422)

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
            """CREATE TABLE app.ontology_objects(schema_name text, table_name text, is_active bool,
                                                       data_type text, description text)""",
            """CREATE TABLE app.flow_publications(token text, schema_name text, table_name text,
                                                       scope jsonb, valid_from timestamptz, valid_until timestamptz)""",
            f"""INSERT INTO app.flow_publications VALUES ('legacy-link', '{PUSH_APPLY_SCHEMA}',
                '{PUSH_APPLY_TARGET}', '{{}}', now()-interval '1 hour', now()+interval '1 hour')""",
        )
        self.addAsyncCleanup(
            self._write, "DROP TABLE app.flow_publications",
            "DROP TABLE app.ontology_objects", f"DROP SCHEMA {PUSH_APPLY_SCHEMA} CASCADE", f"DROP SCHEMA {_OTHER} CASCADE",
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
            "CREATE TABLE app.flow_connections(id text, name text, database_name text, schemas jsonb)",
            f"""INSERT INTO app.flow_connections VALUES ('old', 'old', current_database(),
                    '["{PUSH_APPLY_SCHEMA}","{_OTHER}"]')""",
        )
        self.addAsyncCleanup(self._write, "DROP TABLE app.flow_connections")
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

    async def test_range_catalog_and_sticky_raw_gate_are_exact(self):
        target = f"{BATCH_RANGE_TARGET_SCHEMA}.{BATCH_RANGE_TARGET}"
        await self._write(
            f"DROP TABLE {_QUALIFIED}", f"CREATE SCHEMA {BATCH_RANGE_TARGET_SCHEMA}",
            f"CREATE SCHEMA {BATCH_RANGE_SOURCE_SCHEMA}",
            f"CREATE TABLE {target} (id bigint PRIMARY KEY)",
            f"CREATE TABLE {BATCH_RANGE_TARGET_SCHEMA}.unrelated (id bigint PRIMARY KEY)",
            f"CREATE TABLE {_OTHER}.{BATCH_RANGE_TARGET} (id bigint PRIMARY KEY)",
            "CREATE TABLE app.ontology_batch_sync_receipts(target_table text NOT NULL, range_min bigint)",
            f"""INSERT INTO app.flow_publications VALUES ('range-old', '{BATCH_RANGE_TARGET_SCHEMA}',
                '{BATCH_RANGE_TARGET}', '{{}}', now()-interval '1 hour', now()+interval '1 hour')""",
        )
        self.addAsyncCleanup(
            self._write, "DROP TABLE app.ontology_batch_sync_receipts",
            f"DROP SCHEMA {BATCH_RANGE_TARGET_SCHEMA} CASCADE", f"DROP SCHEMA IF EXISTS {BATCH_RANGE_SOURCE_SCHEMA} CASCADE",
        )
        self.assertEqual([item["name"] for item in await self.layers.tables(BATCH_RANGE_TARGET_SCHEMA)], ["unrelated"])
        sources = await self.connections.catalog("fixture", schema=BATCH_RANGE_TARGET_SCHEMA, search="", limit=5, offset=0)
        self.assertNotIn(BATCH_RANGE_SOURCE_SCHEMA, sources.schemas)
        self.assertEqual([relation.name for relation in sources.relations], ["unrelated"])
        same_name = await self.connections.catalog_columns("fixture", schema=_OTHER, table=BATCH_RANGE_TARGET)
        self.assertEqual([column.name for column in same_name.columns], ["id"])
        with self.assertRaises(HTTPException) as token:
            await self.workspace.published_data("range-old", {})
        self.assertEqual(token.exception.status_code, 404)
        for statements in (
            (),
            (f"DROP SCHEMA {BATCH_RANGE_SOURCE_SCHEMA}",),
            (f"DROP TABLE {target}", f"INSERT INTO app.ontology_batch_sync_receipts VALUES ('{BATCH_RANGE_TARGET}', 0)"),
        ):
            if statements:
                await self._write(*statements)
            with self.assertRaises(HTTPException) as raw:
                await self.layers.query("SELECT 1")
            self.assertEqual(raw.exception.status_code, 403)
        await self._write("UPDATE app.ontology_batch_sync_receipts SET range_min = NULL")
        self.assertEqual((await self.layers.query("SELECT 1 AS result"))["rows"], [{"result": 1}])

    async def test_handoff_never_catalogued_or_public_and_raw_depends_on_durable_evidence(self):
        private = HANDOFF_PRIVATE_SCHEMA
        await self._write(
            f"DROP TABLE {_QUALIFIED}", f"CREATE SCHEMA {private}",
            *(f"CREATE TABLE {relation} (id int PRIMARY KEY)" for relation in HANDOFF_PROTECTED_REGCLASSES),
            f"""INSERT INTO app.flow_publications VALUES ('handoff-old', '{private}',
                'copy_rows', '{{}}', now()-interval '1 hour', now()+interval '1 hour')""",
        )
        self.addAsyncCleanup(self._write, f"DROP SCHEMA {private} CASCADE")
        self.assertNotIn(private, (await self.connections.catalog("fixture", schema=None, search="", limit=5, offset=0)).schemas)
        with self.assertRaises(HTTPException) as denied:
            await self.connections.catalog_columns("fixture", schema=private, table="copy_rows")
        self.assertEqual(denied.exception.status_code, 404)
        with self.assertRaises(HTTPException) as token:
            await self.workspace.published_data("handoff-old", {"limit": 1})
        self.assertEqual(token.exception.status_code, 404)
        self.assertEqual((await self.layers.query("SELECT 1 AS result"))["rows"], [{"result": 1}])
        # Isolate each gate. Surviving evidence blocks raw SQL without the
        # provision marker or siblings, and irrespective of feature settings.
        for relation in HANDOFF_PROTECTED_REGCLASSES:
            await self._write(f"INSERT INTO {relation} VALUES (1)")
            with self.subTest(relation=relation), self.assertRaises(HTTPException) as raw:
                await self.layers.query("SELECT 1")
            self.assertEqual(raw.exception.status_code, 403)
            await self._write(f"DROP TABLE {relation}")
        self.assertEqual((await self.layers.query("SELECT 1 AS result"))["rows"], [{"result": 1}])

    async def test_snapshot_intent_never_catalogued_or_public_and_raw_gates_any_surviving_record(self):
        private = SNAPSHOT_INTENT_PRIVATE_SCHEMA
        await self._write(
            f"DROP TABLE {_QUALIFIED}", f"CREATE SCHEMA {private}",
            *(f"CREATE TABLE {relation} (id int PRIMARY KEY)" for relation in SNAPSHOT_INTENT_PROTECTED_REGCLASSES),
            "CREATE TABLE app.flow_connections(id text, name text, database_name text, schemas jsonb)",
            f"""INSERT INTO app.flow_connections VALUES ('old', 'old', current_database(),
                    '["{private}","{_OTHER}"]')""",
            f"""INSERT INTO app.flow_publications VALUES ('intent-old', '{private}',
                'records', '{{}}', now()-interval '1 hour', now()+interval '1 hour')""",
        )
        self.addAsyncCleanup(self._write, f"DROP SCHEMA {private} CASCADE", "DROP TABLE app.flow_connections")
        self.assertEqual((await self.workspace.connections())[0]["schemas"], [_OTHER])
        self.assertNotIn(private, (await self.connections.catalog("fixture", schema=None, search="", limit=5, offset=0)).schemas)
        with self.assertRaises(HTTPException) as denied:
            await self.connections.catalog_columns("fixture", schema=private, table="records")
        self.assertEqual(denied.exception.status_code, 404)
        with self.assertRaises(HTTPException) as token:
            await self.workspace.published_data("intent-old", {"limit": 1})
        self.assertEqual(token.exception.status_code, 404)
        self.assertEqual((await self.layers.query("SELECT 1 AS result"))["rows"], [{"result": 1}])
        # No application flag is enabled. Losing a sibling relation or marker
        # never exposes an extant private header / frozen row to raw SQL.
        for relation in SNAPSHOT_INTENT_PROTECTED_REGCLASSES:
            await self._write(f"INSERT INTO {relation} VALUES (1)")
            with self.subTest(relation=relation), self.assertRaises(HTTPException) as raw:
                await self.layers.query(f"SELECT * FROM {relation}")
            self.assertEqual(raw.exception.status_code, 403)
            await self._write(f"DROP TABLE {relation}")
        self.assertEqual((await self.layers.query("SELECT 1 AS result"))["rows"], [{"result": 1}])

    async def test_snapshot_intent_upgrade_empty_and_downgrade_preserves_provisioned_or_populated(self):
        migration = import_module("app.web.alembic.versions.fb804e491f6d_ontology_private_snapshot_intent")
        private = SNAPSHOT_INTENT_PRIVATE_SCHEMA

        async def migrate(direction):
            async with self.engine.begin() as connection:
                def perform(sync):
                    with Operations.context(MigrationContext.configure(sync)):
                        direction()
                await connection.run_sync(perform)

        await migrate(migration.upgrade)
        self.addAsyncCleanup(self._write, f"DROP SCHEMA IF EXISTS {private} CASCADE")
        for name in SNAPSHOT_INTENT_FROZEN_REGCLASSES:
            self.assertEqual(await self.conn.fetchval(f"SELECT count(*) FROM {name}"), 0)
        await migrate(migration.downgrade)
        self.assertIsNone(await self.conn.fetchval("SELECT to_regnamespace($1)::text", private))
        await migrate(migration.upgrade)
        await self._write(f"INSERT INTO {private}.identity VALUES(true, gen_random_uuid())")
        with self.assertRaisesRegex(RuntimeError, "Refusing"):
            await migrate(migration.downgrade)
        self.assertEqual(await self.conn.fetchval(f"SELECT count(*) FROM {private}.identity"), 1)
        await self._write(
            f"DELETE FROM {private}.identity",
            f"""INSERT INTO {private}.intents VALUES (
                gen_random_uuid(),gen_random_uuid(),gen_random_uuid(),0,gen_random_uuid(),
                repeat('a',64),repeat('b',64),1,clock_timestamp())""",
            f"INSERT INTO {private}.records SELECT id, 1, 42, 'private synthetic' FROM {private}.intents",
        )
        with self.assertRaisesRegex(RuntimeError, "Refusing"):
            await migrate(migration.downgrade)
        self.assertEqual(await self.conn.fetchval(f"SELECT count(*) FROM {private}.records"), 1)

    async def test_delivery_admission_or_mirrored_receipt_blocks_downgrade(self):
        ledger = import_module("app.web.alembic.versions.e57b4216c9a8_ontology_snapshot_intent_delivery_ledger")
        private = SNAPSHOT_INTENT_PRIVATE_SCHEMA
        qualified = SNAPSHOT_INTENT_DELIVERY_REGCLASS
        await self._write(f"CREATE SCHEMA {private}")
        self.addAsyncCleanup(self._write, f"DROP SCHEMA {private} CASCADE")

        async def migrate(direction):
            async with self.engine.begin() as connection:
                def perform(sync):
                    with Operations.context(MigrationContext.configure(sync)):
                        direction()
                await connection.run_sync(perform)

        await migrate(ledger.upgrade)
        await migrate(ledger.downgrade)
        self.assertIsNone(await self.conn.fetchval("SELECT to_regclass($1)::text", qualified))
        await migrate(ledger.upgrade)
        await self._write(f"DROP TABLE {_QUALIFIED}")
        self.assertEqual((await self.layers.query("SELECT 1 AS result"))["rows"], [{"result": 1}])
        await self._write(f"""
            INSERT INTO {qualified} (
              intent_id,request_id,scope_digest,payload_hash,expected_previous_generation,
              delivery_config_revision,execution_digest,row_count,admitted_at
            ) VALUES (
              gen_random_uuid(),gen_random_uuid(),repeat('a',64),repeat('b',64),3,
              gen_random_uuid(),repeat('c',64),2,clock_timestamp()
            )
        """)
        with self.assertRaisesRegex(RuntimeError, "Refusing"):
            await migrate(ledger.downgrade)
        with self.assertRaises(HTTPException) as raw:
            await self.layers.query("SELECT 1")
        self.assertEqual(raw.exception.status_code, 403)
        for invalid in (
            "receiver_generation=4",
            "receiver_recorded_at=clock_timestamp()",
            "receiver_row_count=2",
            "receiver_generation=5, receiver_recorded_at=clock_timestamp(), receiver_row_count=2",
            "receiver_generation=4, receiver_recorded_at='infinity', receiver_row_count=2",
            "receiver_generation=4, receiver_recorded_at=clock_timestamp(), receiver_row_count=1",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(asyncpg.CheckViolationError):
                await self.conn.execute(f"UPDATE {qualified} SET {invalid}")
        await self.conn.execute(f"""
            UPDATE {qualified}
            SET receiver_generation=4, receiver_recorded_at=clock_timestamp(), receiver_row_count=2
        """)
        with self.assertRaisesRegex(RuntimeError, "Refusing"):
            await migrate(ledger.downgrade)
        self.assertEqual(await self.conn.fetchval(f"SELECT receiver_generation FROM {qualified}"), 4)

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
