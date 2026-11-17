"""Exact reservation of the managed committed-batch fixture in generic ETL paths."""

from contextlib import asynccontextmanager
from ipaddress import ip_address
import os
from unittest import IsolatedAsyncioTestCase, skipUnless
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.web.services.etl_layers import EtlLayers, RESERVED_BATCH_TARGET, require_ordinary_table
from app.web.services.etl_workspace import EtlWorkspace

_URL = os.getenv("ONTOLOGY_COMMITTED_TARGET_TEST_URL")
_SCHEMA, _TARGET = RESERVED_BATCH_TARGET


class TestImmediateReservation(IsolatedAsyncioTestCase):
    async def test_exact_protected_calls_fail_before_generic_database_access(self):
        layers, workspace = EtlLayers(), EtlWorkspace()
        alias = _SCHEMA.removeprefix("ts_")

        @asynccontextmanager
        async def unexpected():
            raise AssertionError("A protected direct request opened a database session")
            yield

        layers.session = workspace._session = unexpected
        for method, args in (
            (layers.get_table, (alias, _TARGET)),
            (layers.columns, (alias, _TARGET)),
            (layers.partitions, (alias, _TARGET)),
            (layers.data, (alias, _TARGET)),
            (layers.preview, (alias, _TARGET)),
            (layers.evidence, (alias, _TARGET)),
            (workspace.patch_row, (alias, _TARGET, {})),
            (workspace.delete_row, (alias, _TARGET, {})),
            (workspace.create_table, (alias, {"name": _TARGET})),
            (workspace.publications, (alias, _TARGET)),
            (workspace.save_publication, (alias, _TARGET, {})),
            (workspace.delete_publication, (alias, _TARGET, str(uuid4()))),
            (workspace.set_object_favorite, (alias, _TARGET, False, str(uuid4()))),
        ):
            with self.subTest(fn=method.__name__), self.assertRaises(HTTPException) as error:
                await method(*args)
            self.assertEqual(error.exception.status_code, 404)
        require_ordinary_table(_SCHEMA, "regular_batch_target")
        require_ordinary_table("ts_other", _TARGET)


@skipUnless(_URL, "Set ONTOLOGY_COMMITTED_TARGET_TEST_URL to a disposable PostgreSQL database")
class TestCommittedTargetCatalog(IsolatedAsyncioTestCase):
    async def test_real_catalog_anonymous_token_freeform_and_schema_mutation(self):
        url = make_url(_URL)
        if (url.drivername != "postgresql+asyncpg" or url.database != "ontology_committed_target_regression"
            or url.query or not ip_address(url.host or "").is_loopback):
            self.fail("Use the exact disposable local ontology_committed_target_regression database")
        engine = create_async_engine(url)
        self.addAsyncCleanup(engine.dispose)
        async with engine.connect() as conn:
            async with conn.begin() as transaction:
                async with AsyncSession(bind=conn) as db:
                    for sql in (
                        "CREATE SCHEMA ts_batch_sync_sandbox",
                        "CREATE SCHEMA ts_other",
                        "CREATE TABLE ts_batch_sync_sandbox.committed_batch_fixture(id bigint PRIMARY KEY, value text NOT NULL)",
                        "CREATE TABLE ts_batch_sync_sandbox.regular_batch_target(id bigint PRIMARY KEY)",
                        "CREATE TABLE ts_other.committed_batch_fixture(id bigint PRIMARY KEY)",
                        """CREATE TABLE public.ontology_objects(schema_name text, table_name text, is_active bool,
                                                        data_type text, description text)""",
                        """CREATE TABLE public.flow_publications(token text, schema_name text, table_name text,
                                                        scope jsonb, valid_from timestamptz, valid_until timestamptz)""",
                        """INSERT INTO public.flow_publications VALUES ('old-token', 'ts_batch_sync_sandbox',
                            'committed_batch_fixture', '{}',now()-interval '1 hour',now()+interval '1 hour')""",
                    ):
                        await db.execute(text(sql))

                    @asynccontextmanager
                    async def configured(*, repeatable_read=False):
                        yield db

                    layers, workspace = EtlLayers(), EtlWorkspace()
                    layers.session = workspace._session = configured
                    workspace._layers = layers
                    self.assertEqual([row["name"] for row in await layers.tables(_SCHEMA)], ["regular_batch_target"])
                    self.assertEqual([row["name"] for row in await layers.tables("ts_other")], [_TARGET])
                    layers_list = {row["schemaName"]: row["tables"] for row in await layers.layers()}
                    self.assertEqual(layers_list[_SCHEMA], 1)
                    with self.assertRaises(HTTPException) as error:
                        await workspace.published_data("old-token", {"limit": 1})
                    self.assertEqual(error.exception.status_code, 404)
                    for method, args, code in (
                        (layers.query, ("SELECT 1",), 403),
                        (workspace.rename_schema, (_SCHEMA, {"name": "ts_new"}), 409),
                        (workspace.delete_schema, (_SCHEMA,), 409),
                    ):
                        with self.assertRaises(HTTPException) as error:
                            await method(*args)
                        self.assertEqual(error.exception.status_code, code)
                    await db.execute(text("DROP TABLE ts_batch_sync_sandbox.committed_batch_fixture"))
                    self.assertEqual((await layers.query("SELECT 1 AS result"))["rows"], [{"result": 1}])
                    # Test-only synthetic tables will all be rolled back.
                    await transaction.rollback()
