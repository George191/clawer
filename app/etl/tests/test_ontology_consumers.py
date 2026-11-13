"""ETL/CDC ontology-registry consumers; all SQL and external clients are local fakes."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.etl import base, ts_ods, ts_rds, ts_task
from app.etl.schema import registry
from app.syncer import postgres_cdc as cdc


class Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class Pg:
    def __init__(self, registered=(), covered=False):
        self.calls, self.events = [], []
        self.registered, self.covered = list(registered), covered
        self.fetch_one = AsyncMock(return_value={"relation": "ts_ods.ods_fixture"})
        self.init_schema = AsyncMock(side_effect=lambda blocks: self.events.append("ddl"))
        self.execute = AsyncMock()

    @asynccontextmanager
    async def session(self):
        self.events.append("begin")
        try:
            yield self
        except Exception:
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")

    async def _sql(self, query, params=None):
        sql = str(query)
        self.calls.append((sql, params))
        if "ddlregistry" in sql:
            raise AssertionError("A consumer queried the removed registry")
        if sql.strip() == cdc._ODS_CURRENT_SQL.strip():
            return Result(self.registered)
        if "FROM pg_publication_rel r JOIN pg_publication" in sql:
            return Result([{"exists": 1}] if self.covered else [])
        return Result()


def worker(kind=base.ETLBase, layer="ods"):
    instance = object.__new__(kind)
    instance._layer, instance._table_role = layer, "current"
    instance._table_name, instance._handlers = "fixture", {"fixture": AsyncMock()}
    instance._log_prefix, instance._pg = "[fixture]", Pg()
    instance._ready_types, instance._columns = set(), {}
    instance._create_partitions = AsyncMock()
    instance._ensure_cdc_identity = AsyncMock()
    return instance


RECORD = {"ddl_sql": "CREATE TABLE ts_ods.ods_fixture (id TEXT PRIMARY KEY);"}
IDENTITY = {"publication": "fixture_publication", "slot": "fixture_slot", "database": "fixture"}


class OntologyConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_ddl_reads_do_not_bootstrap_or_write(self):
        pg = Pg()
        pg.fetch_one.return_value = {"ddl_sql": "registered ddl"}
        with patch.object(registry, "ensure_ontology_tables", new_callable=AsyncMock) as ensure:
            self.assertEqual(await registry.get_registered_ddl(pg, "ods", "fixture"), "registered ddl")
            self.assertEqual(await registry.get_registered_ddl_record(pg, "ods", "fixture"), {"ddl_sql": "registered ddl"})
        ensure.assert_not_awaited()
        pg.execute.assert_not_awaited()
        pg.init_schema.assert_not_awaited()
        for args in pg.fetch_one.await_args_list:
            self.assertIn("FROM public.ontology_objects", args.args[0])

    async def test_base_initializes_new_ontology_api_before_registered_handlers(self):
        instance = worker(layer="dwd")
        instance._ensure_registered_table = AsyncMock()
        with patch.object(base, "ensure_ontology_tables", new_callable=AsyncMock) as ensure, \
             patch.object(base, "get_registered_ddl", new_callable=AsyncMock, return_value="ddl"):
            await instance._on_init_schema()
        ensure.assert_awaited_once_with(instance._pg)
        instance._pg.execute.assert_awaited_once_with("CREATE SCHEMA IF NOT EXISTS ts_dwd")
        instance._ensure_registered_table.assert_awaited_once_with("fixture", "current", create_partitions=False)

    async def test_rds_and_ods_startup_keep_table_scope_and_new_bootstrap(self):
        for module, kind, names in [
            (ts_rds, ts_rds.TsRds, ts_rds.RDS_CURRENT_BASELINE_DDLS),
            (ts_ods, ts_ods.TsOds, ts_ods.CATALOG_ODS_COLUMNS),
        ]:
            with self.subTest(layer=kind._layer):
                instance = worker(kind, kind._layer)
                instance._table_name = next(name for name in names if name != "orbital")
                instance._ensure_registered_table = AsyncMock()
                with patch.object(module, "ensure_ontology_tables", new_callable=AsyncMock) as ensure:
                    await instance._on_init_schema()
                ensure.assert_awaited_once_with(instance._pg)
                instance._ensure_registered_table.assert_awaited_once_with(
                    instance._table_name, "current", create_partitions=False,
                )

    async def test_task_registration_uses_same_get_save_contract_and_base_bootstrap(self):
        instance = worker(ts_task.TsTask, "task")
        instance._handlers = {"vulnerability": AsyncMock()}
        instance._ensure_registered_table = AsyncMock()
        order = []
        async def lookup(*args):
            self.assertEqual(order, ["ensure"])
            order.append("lookup")
            return None
        with patch.object(ts_task, "ensure_ontology_tables", new_callable=AsyncMock,
                          side_effect=lambda pg: order.append("ensure")) as task_ensure, \
             patch.object(ts_task, "get_registered_ddl", side_effect=lookup), \
             patch.object(ts_task, "save_registered_ddl", new_callable=AsyncMock) as save, \
             patch.object(base, "ensure_ontology_tables", new_callable=AsyncMock) as ensure, \
             patch.object(base, "get_registered_ddl", new_callable=AsyncMock, return_value="ddl"):
            await instance._on_init_schema()
        ensure.assert_awaited_once_with(instance._pg)
        task_ensure.assert_awaited_once_with(instance._pg)
        self.assertEqual(order, ["ensure", "lookup"])
        self.assertEqual(save.await_args.kwargs["ddl_sql"], ts_task.TASK_VULNERABILITY_DDL)
        self.assertEqual(save.await_args.kwargs["partition_type"], "none")
        instance._ensure_registered_table.assert_awaited_once_with("vulnerability", "current", create_partitions=False)

    async def test_new_physical_table_is_synchronized_before_cdc_or_partition_use(self):
        instance = worker()
        instance._pg.fetch_one.return_value = {"relation": None}
        async def sync(session, schema, table):
            self.assertIs(session, instance._pg)
            self.assertEqual((schema, table), ("ts_ods", "ods_fixture"))
            instance._pg.events.append("metadata")
        with patch.object(base, "get_registered_ddl_record", new_callable=AsyncMock, return_value=RECORD), \
             patch.object(base, "sync_object_metadata", side_effect=sync), \
             patch.object(base.settings, "etl_output_mode", "debezium"):
            await instance._ensure_registered_table("fixture", "current")
        instance._pg.init_schema.assert_awaited_once_with([RECORD["ddl_sql"]])
        self.assertEqual(instance._pg.events, ["ddl", "begin", "metadata", "commit"])
        instance._ensure_cdc_identity.assert_awaited_once_with("ts_ods.ods_fixture")
        instance._create_partitions.assert_awaited_once_with("fixture", table_role="current", ddl_record=RECORD)

    async def test_metadata_failure_retries_existing_table_without_caching_or_recreating_it(self):
        instance = worker(ts_rds.TsRds, "rds")
        with patch.object(ts_rds, "get_registered_ddl", new_callable=AsyncMock, return_value="ddl"), \
             patch.object(base, "get_registered_ddl_record", new_callable=AsyncMock, return_value=RECORD), \
             patch.object(base, "sync_object_metadata", new_callable=AsyncMock,
                          side_effect=[RuntimeError("metadata unavailable"), None]) as sync, \
             patch.object(base.settings, "etl_output_mode", "direct"):
            with self.assertRaisesRegex(RuntimeError, "metadata unavailable"):
                await instance._ensure_type("fixture")
            self.assertNotIn("fixture", instance._ready_types)
            await instance._ensure_type("fixture")
            self.assertIn("fixture", instance._ready_types)
            await instance._ensure_type("fixture")
        self.assertEqual(sync.await_count, 2)
        instance._pg.init_schema.assert_not_awaited()
        self.assertEqual(instance._pg.events, ["begin", "rollback", "begin", "commit"])

    async def test_unregistered_table_never_creates_metadata_or_ddl(self):
        instance = worker()
        instance._ddl_for_table = AsyncMock(return_value="")
        with patch.object(base, "get_registered_ddl_record", new_callable=AsyncMock, return_value=None), \
             patch.object(base, "sync_object_metadata", new_callable=AsyncMock) as sync:
            await instance._ensure_registered_table("fixture", "current")
        instance._pg.init_schema.assert_not_awaited()
        sync.assert_not_awaited()


class CdcOntologyConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_is_limited_to_active_registered_ods_current_parents(self):
        sql = cdc._ODS_CURRENT_SQL
        self.assertIn("FROM public.ontology_objects r", sql)
        for predicate in ["r.layer='ods'", "r.table_role='current'", "r.is_active", "NOT c.relispartition"]:
            self.assertIn(predicate, sql)
        self.assertEqual(cdc._ods_whitelist([
            {"nspname": "ts_ods", "relname": "ods_fixture"},
            {"nspname": "ts_ods", "relname": "ods_orbital"},
        ]), {("ts_ods", "ods_fixture")})

    async def test_prepare_grants_only_required_ontology_columns_and_preserves_publication_scope(self):
        pg = Pg(registered=[{"nspname": "ts_ods", "relname": "ods_fixture"}])
        pg.execute = pg._sql
        with patch.object(cdc, "postgres_cdc_identity", return_value=IDENTITY), \
             patch.object(cdc, "cdc_schemas", return_value=("ts_rds", "ts_ods", "ts_task")), \
             patch.object(cdc.settings, "pg_cdc_url", "postgresql+asyncpg://fixture_reader@fixture.invalid/fixture"):
            await cdc.prepare_database(pg)
        statements = [sql for sql, _ in pg.calls]
        self.assertIn('GRANT USAGE ON SCHEMA public TO "fixture_reader"', statements)
        self.assertIn('GRANT SELECT (layer,table_role,is_active,schema_name,table_name) ON public.ontology_objects TO "fixture_reader"', statements)
        publication = next(sql for sql in statements if sql.startswith("CREATE PUBLICATION"))
        self.assertIn('TABLES IN SCHEMA "ts_rds", "ts_task"', publication)
        self.assertIn('TABLE "ts_ods"."ods_fixture"', publication)
        self.assertNotIn('TABLES IN SCHEMA "ts_ods"', publication)
        self.assertTrue(any(sql.strip() == cdc._ODS_CURRENT_SQL.strip() for sql in statements))

    async def test_dynamic_publication_reads_new_registry_and_never_adds_unregistered_parent(self):
        for registered, covered in [(True, False), (True, True), (False, False)]:
            with self.subTest(registered=registered, covered=covered):
                pg = Pg([{"nspname": "ts_ods", "relname": "ods_fixture"}] if registered else [], covered)
                pg.execute = pg._sql
                with patch.object(cdc, "postgres_cdc_identity", return_value=IDENTITY):
                    if registered:
                        await cdc.ensure_table_publication(pg, "ts_ods.ods_fixture")
                    else:
                        with self.assertRaisesRegex(cdc.SetupError, "active registered current parent"):
                            await cdc.ensure_table_publication(pg, "ts_ods.ods_fixture")
                additions = [sql for sql, _ in pg.calls if sql.startswith("ALTER PUBLICATION")]
                self.assertEqual(len(additions), int(registered and not covered))

    async def test_non_ods_or_timescale_tables_skip_dynamic_publication(self):
        pg = Pg()
        pg.execute = pg._sql
        await cdc.ensure_table_publication(pg, "ts_rds.rds_fixture")
        await cdc.ensure_table_publication(pg, "ts_ods.ods_orbital")
        self.assertEqual(pg.calls, [])

    async def test_readonly_cdc_preflight_uses_ontology_scope_and_rejects_extra_ods_parents(self):
        row = {"nspname": "ts_ods", "relname": "ods_fixture"}
        for extra in (False, True):
            with self.subTest(extra_parent=extra):
                pg = Pg()
                pg.fetch_one = AsyncMock(side_effect=[
                    {"version": 160000, "wal_level": "logical", "replication": True},
                    {"pubviaroot": True, "puballtables": False}, None,
                ])
                pg.fetch_all = AsyncMock(side_effect=[
                    [row], [], [{"nspname": "ts_rds"}, {"nspname": "ts_task"}],
                    [row, {"nspname": "ts_ods", "relname": "ods_unregistered"}] if extra else [row], [],
                ])
                with patch.object(cdc, "postgres_cdc_identity", return_value=IDENTITY), \
                     patch.object(cdc, "cdc_schemas", return_value=("ts_rds", "ts_ods", "ts_task")):
                    if extra:
                        with self.assertRaisesRegex(cdc.SetupError, "ODS parents differ"):
                            await cdc.check_database(pg)
                    else:
                        await cdc.check_database(pg)
                self.assertEqual(pg.fetch_all.await_args_list[0].args[0], cdc._ODS_CURRENT_SQL)
                pg.execute.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
