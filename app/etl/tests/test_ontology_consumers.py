"""ETL/CDC ontology-registry consumers; all SQL and external clients are local fakes."""

from contextlib import asynccontextmanager
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiokafka import TopicPartition
from aiokafka.structs import OffsetAndMetadata

from app.etl import base, ts_ods, ts_rds, ts_task
from app.etl.cdc import committed_row, row_outputs
from app.etl.schema import registry
from app.etl.schema.ddl import build_current_ddl, ddl_creates_table
from app.etl.schema.layout import is_schema_identifier, qualified_table_name
from app.syncer import postgres_cdc as cdc
from app.syncer.es import worker as es_worker
from app.syncer.es.config import SyncConfig, TableSyncConfig
from app.syncer import postgres_worker


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
    def __init__(self):
        self.calls, self.events = [], []
        self.connection_id = "source-a"
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

def worker(kind=base.ETLBase, layer="ods"):
    instance = object.__new__(kind)
    instance._layer, instance._table_role = layer, "current"
    instance._table_name, instance._handlers = "fixture", {"fixture": AsyncMock()}
    instance._log_prefix, instance._pg = "[fixture]", Pg()
    instance._metadata_pg = Pg()
    instance._ready_types, instance._columns, instance._table_records = set(), {}, {}
    instance._create_partitions = AsyncMock()
    instance._ensure_cdc_identity = AsyncMock()
    return instance


RECORD = {"id": 7, "schema_name": "RawInbox.Events", "table_name": "NormalizedRecords",
          "data_type": "fixture", "data_type_source": "registration",
          "ddl_sql": 'CREATE TABLE "RawInbox.Events"."NormalizedRecords" (id TEXT PRIMARY KEY);'}
IDENTITY = {"publication": "fixture_publication", "slot": "fixture_slot", "database": "fixture",
            "prefix": "fixture-pg", "connection_id": "source-a"}


class OntologyConsumerTests(unittest.IsolatedAsyncioTestCase):
    def test_relation_ddl_preserves_quoted_schema_as_a_single_identifier(self):
        schema, table = "RawInbox.Events", "NormalizedRecords"
        self.assertEqual(qualified_table_name(schema, table), '"RawInbox.Events"."NormalizedRecords"')
        self.assertTrue(ddl_creates_table(f'CREATE TABLE "{schema}" . "{table}" (id bigint);', schema, table))
        self.assertFalse(ddl_creates_table(f'CREATE TABLE {schema}."{table}" (id bigint);', schema, table))
        self.assertFalse(ddl_creates_table(f'CREATE TABLE "{schema}".{table} (id bigint);', schema, table))
        for invalid in (".RawInbox", "RawInbox.", "RawInbox..Events", "RawInbox.9Events", "Raw Inbox.Events", "x" * 64):
            self.assertFalse(is_schema_identifier(invalid))
        with self.assertRaises(ValueError):
            qualified_table_name(schema, "Normalized.Records")
        rebuilt = build_current_ddl(layer="ods", logical_table="fixture", current_ddl_sql=RECORD["ddl_sql"],
                                    partition_type="none", partition_column=None)
        self.assertIn('ALTER TABLE "RawInbox.Events"."NormalizedRecords" REPLICA IDENTITY FULL', rebuilt)

    async def test_binding_reads_use_logical_layer_role_and_type(self):
        pg = Pg()
        pg.fetch_all = AsyncMock(return_value=[RECORD])
        with patch.object(registry, "ensure_ontology_tables", new_callable=AsyncMock) as ensure:
            self.assertEqual(await registry.get_registered_ddl(pg, "ods", "fixture"), RECORD["ddl_sql"])
            self.assertEqual(await registry.require_registered_object(pg, "ods", "fixture", "history"), RECORD)
        ensure.assert_not_awaited()
        pg.execute.assert_not_awaited()
        pg.init_schema.assert_not_awaited()
        for args in pg.fetch_all.await_args_list:
            self.assertIn("FROM app.helio_objects", args.args[0])
            self.assertNotIn("schema_name = :", args.args[0])
        self.assertEqual(pg.fetch_all.await_args.args[1], {
            "layer": "ods", "table_role": "history", "data_type": "fixture",
        })
        pg.fetch_all.return_value = [RECORD, {**RECORD, "table_name": "duplicate"}]
        with self.assertRaisesRegex(ValueError, "Ambiguous Ontology binding"):
            await registry.get_registered_object(pg, "ods", "fixture")

    async def test_base_checks_registry_before_ensuring_registered_handlers(self):
        instance = worker(layer="dwd")
        instance._ensure_registered_table = AsyncMock()
        with patch.object(base, "ensure_ontology_tables", new_callable=AsyncMock) as ensure:
            await instance._on_init_schema()
        ensure.assert_awaited_once_with(instance._metadata_pg)
        instance._pg.execute.assert_not_awaited()
        instance._ensure_registered_table.assert_awaited_once_with("fixture", "current", create_partitions=False)

    async def test_rds_and_ods_startup_uses_registered_types_and_respects_scope(self):
        for kind in (ts_rds.TsRds, ts_ods.TsOds):
            for scoped in (True, False):
                with self.subTest(layer=kind._layer, scoped=scoped):
                    instance = worker(kind, kind._layer)
                    instance._table_name = "fixture" if scoped else ""
                    instance._ensure_registered_table = AsyncMock()
                    with patch.object(base, "ensure_ontology_tables", new_callable=AsyncMock) as ensure, \
                         patch.object(base, "list_registered_data_types", new_callable=AsyncMock,
                                      return_value=["registered_type"]) as list_types:
                        await instance._on_init_schema()
                    ensure.assert_awaited_once_with(instance._metadata_pg)
                    if scoped:
                        list_types.assert_not_awaited()
                    else:
                        list_types.assert_awaited_once_with(instance._metadata_pg, kind._layer, "current")
                    instance._ensure_registered_table.assert_awaited_once_with(
                        "fixture" if scoped else "registered_type", "current", create_partitions=False,
                    )

    async def test_new_physical_table_is_synchronized_before_cdc_or_partition_use(self):
        instance = worker()
        instance._pg.fetch_one.return_value = {"relation": None}
        async def sync(session, schema, table, *, catalog_session, layer, table_role, data_type, data_type_source):
            self.assertIs(session, instance._metadata_pg)
            self.assertIs(catalog_session, instance._pg)
            self.assertEqual((schema, table, data_type, data_type_source),
                             ("RawInbox.Events", "NormalizedRecords", "fixture", "registration"))
            self.assertEqual((layer, table_role), ("ods", "current"))
            instance._pg.events.append("metadata")
        with patch.object(base, "require_registered_object", new_callable=AsyncMock, return_value=RECORD), \
             patch.object(base, "sync_object_metadata", side_effect=sync), \
             patch.object(base.settings, "etl_output_mode", "debezium"):
            await instance._ensure_registered_table("fixture", "current")
        instance._pg.execute.assert_awaited_once_with('CREATE SCHEMA IF NOT EXISTS "RawInbox.Events"')
        instance._pg.init_schema.assert_awaited_once_with([RECORD["ddl_sql"]])
        self.assertEqual(instance._pg.events, ["ddl", "begin", "metadata", "commit"])
        instance._ensure_cdc_identity.assert_awaited_once_with("RawInbox.Events", "NormalizedRecords", "fixture")
        instance._create_partitions.assert_awaited_once_with("fixture", table_role="current", ddl_record=RECORD)
        self.assertEqual(instance._table_fqn("fixture"), '"RawInbox.Events"."NormalizedRecords"')
        self.assertEqual(instance._partition_name("fixture", "current", "p00"),
                         '"RawInbox.Events"."NormalizedRecords_p00"')

    async def test_metadata_failure_retries_existing_table_without_caching_or_recreating_it(self):
        instance = worker(ts_rds.TsRds, "rds")
        with patch.object(base, "require_registered_object", new_callable=AsyncMock, return_value=RECORD), \
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

    async def test_unregistered_or_stale_ddl_cannot_create_a_table(self):
        instance = worker()
        instance._metadata_pg.fetch_all = AsyncMock(return_value=[])
        with self.assertRaisesRegex(ValueError, "No active Ontology binding"):
            await instance._ensure_registered_table("fixture", "current")
        instance._pg.fetch_one.return_value = {"relation": None}
        instance._metadata_pg.fetch_all.return_value = [{**RECORD, "schema_name": "renamed"}]
        with self.assertRaisesRegex(ValueError, "Missing matching registered DDL"):
            await instance._ensure_registered_table("fixture", "current")
        instance._pg.execute.assert_not_awaited()
        instance._pg.init_schema.assert_not_awaited()

    async def test_task_reads_from_ods_and_commits_target_before_releasing_source_lock(self):
        instance = worker(ts_task.TsTask, "task")
        instance._table_records[("fixture", "current")] = RECORD
        events = []

        @asynccontextmanager
        async def transaction(name):
            events.append(f"{name}:begin")
            yield name
            events.append(f"{name}:commit")

        class Source:
            def session(self):
                return transaction("ods")

        assert_in = self.assertIn
        assert_equal = self.assertEqual

        class Target:
            @asynccontextmanager
            async def session(self):
                async with transaction("task"):
                    yield self

            async def execute(self, query, params):
                assert_in('"RawInbox.Events"."NormalizedRecords"', str(query))
                assert_equal(params["source_revision"], "verified")
                events.append("write")
                return Result([{"record_id": "1"}])

        instance._pg = Target()
        instance._metadata_pg.fetch_all = AsyncMock(return_value=[{**RECORD, "schema_name": "Source.Layer"}])
        async def validate(session, layer, message, *, table_ref):
            self.assertEqual((session, layer, table_ref), ("ods", "ods", '"Source.Layer"."NormalizedRecords"'))
            events.append("read")
            return "verified"

        with patch.object(ts_task, "source_snapshot_revision", side_effect=validate), \
             patch.object(ts_task, "get_layer_pg_client", return_value=Source()) as select:
            result = await instance._write_result({"data_type": "fixture"},
                                                  "INSERT INTO {table_ref} DEFAULT VALUES",
                                                  {"data_type": "fixture"})
        select.assert_called_once_with("ods")
        self.assertEqual(result, {"record_id": "1"})
        self.assertEqual(events, ["ods:begin", "read", "task:begin", "write", "task:commit", "ods:commit"])


    async def test_es_backfill_resolves_the_registered_rds_table_and_layer_database(self):
        metadata, rds = Pg(), Pg()
        record = {**RECORD, "schema_name": "RawWarehouse.Events", "table_name": "EventRecords"}
        metadata.fetch_all = AsyncMock(return_value=[record])
        rds.fetch_one.return_value = None
        rds.fetch_all = AsyncMock(return_value=[])
        config = TableSyncConfig(table_name="fixture", schema_name="RawWarehouse.Events")
        worker = es_worker.EsSyncWorker(SyncConfig(tables=[config]), metadata_pg=metadata)
        worker._bindings = {"rds": ("source-a", None)}
        worker._watermark_store.get_cursor = AsyncMock(return_value=None)
        await worker._resolve_sources()
        with patch.object(es_worker, "get_layer_pg_client", return_value=rds) as select:
            await worker._backfill_bound(config)
            await worker._fetch_incremental(config)
        select.assert_called_once_with("rds")
        for query in (rds.fetch_one, rds.fetch_all):
            self.assertIn('FROM "RawWarehouse.Events"."EventRecords"', query.await_args.args[0])
            self.assertEqual(query.await_args.args[1]["source_type"], "fixture")
        self.assertEqual(worker._watermark_store.get_cursor.await_args.args[1], "source-a/7")
        worker._bindings = {"rds": ("source-b", None)}
        self.assertEqual(worker._checkpoint_source(config), "source-b/7")


class CdcOntologyConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_identity = cdc.postgres_cdc_identity
        self.original_registered = cdc.registered_cdc_objects
        self.objects = {("OddWarehouse", "DailyRollup"): ("ods", "fixture")}
        self.table = {"nspname": "OddWarehouse", "relname": "DailyRollup", "oid": 413, "relkind": "p",
                      "columns": ["record_id", "data_source", "data_type"]}
        self.covered = {**self.table, "full_table": True}
        self.publication = {"pubviaroot": True, "puballtables": False, "publishes_changes": True, "schemas": []}
        for name, value in (("postgres_cdc_identity", IDENTITY), ("registered_cdc_objects", self.objects)):
            patcher = patch.object(cdc, name, new_callable=AsyncMock, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_catalog(self, *, publication=None, published=()):
        pg = Pg()

        async def execute(query, params=None):
            sql = str(query)
            pg.calls.append((sql, params))
            return Result({
                cdc._PHYSICAL_SQL: [self.table],
                cdc._PUBLICATION_SQL: [publication] if publication else [],
                cdc._PUBLISHED_SQL: published,
                cdc._ROW_IMAGES_SQL: [{**self.table, "full_identity": False}],
            }.get(sql, []))

        pg.execute.side_effect = execute
        return pg

    async def test_worker_only_alters_tables_without_full_replica_identity(self):
        schema, table = "Inventory.Events", "DailyRows"
        for has_full_identity in (True, False):
            with self.subTest(has_full_identity=has_full_identity):
                instance = object.__new__(base.ETLBase)
                instance._pg, instance._layer = Pg(), "ods"
                instance._pg.fetch_one.return_value = {"has_full_identity": has_full_identity}
                with patch.object(cdc, "ensure_table_publication", new_callable=AsyncMock) as publish:
                    await instance._ensure_cdc_identity(schema, table, "fixture")
                sql, params = instance._pg.fetch_one.await_args.args
                self.assertIn("relreplident = 'f' AS has_full_identity", sql)
                self.assertEqual(params, {"table_name": '"Inventory.Events"."DailyRows"'})
                if has_full_identity:
                    instance._pg.execute.assert_not_awaited()
                else:
                    instance._pg.execute.assert_awaited_once_with('ALTER TABLE "Inventory.Events"."DailyRows" REPLICA IDENTITY FULL')
                publish.assert_awaited_once_with(instance._pg, schema, table)

    async def test_registry_scope_uses_saved_database_and_logical_table_metadata(self):
        metadata = Pg()
        metadata.fetch_all = AsyncMock(return_value=[
            {"schema_name": "OddWarehouse", "table_name": "DailyRollup", "layer": "ods", "data_type": "fixture"},
            {"schema_name": "OddWarehouse", "table_name": "PositionSamples", "layer": "ods", "data_type": "orbital"},
        ])
        with patch.object(cdc, "get_pg_client", return_value=metadata), \
             patch.object(cdc, "registered_cdc_objects", new=self.original_registered), \
             patch.object(cdc, "layer_connection_info", AsyncMock(return_value={
                 "ods": {"id": "source-a", "engine": "postgresql"},
                 "rds": {"id": "source-b", "engine": "postgresql"},
                 "task": {"id": "source-c", "engine": "dameng"},
             })):
            self.assertEqual(await cdc.registered_cdc_objects(connection_id="source-a"), self.objects)
        query, params = metadata.fetch_all.await_args.args
        self.assertEqual(params, {"layers": ["ods"]})
        for predicate in ("is_active", "table_role='current'", "data_type_source='registration'"):
            self.assertIn(predicate, query)

    async def test_prepare_uses_explicit_registered_physical_tables(self):
        pg = self.fake_catalog()
        await cdc.prepare_database(pg)
        statements = [sql for sql, _ in pg.calls]
        self.assertFalse(any("GRANT" in sql for sql in statements))
        publication = next(sql for sql in statements if sql.startswith("CREATE PUBLICATION"))
        self.assertIn('TABLE "OddWarehouse"."DailyRollup"', publication)
        self.assertNotIn("TABLES IN SCHEMA", publication)
        self.assertIn('ALTER TABLE "OddWarehouse"."DailyRollup" REPLICA IDENTITY FULL', statements)

    async def test_prepare_refuses_to_narrow_existing_publication(self):
        unregistered = {**self.covered, "relname": "Removed"}
        for schemas, published in [(["LegacyCatalog"], []), ([], [unregistered])]:
            pg = self.fake_catalog(publication={**self.publication, "schemas": schemas}, published=published)
            with self.assertRaisesRegex(cdc.SetupError, "requires a coordinated CDC cutover"):
                await cdc.prepare_database(pg)
            self.assertFalse(any(sql.startswith("ALTER") for sql, _ in pg.calls))
            self.assertEqual(pg.events, ["begin", "rollback"])

    async def test_prepare_preserves_historical_topics_with_retained_backlog(self):
        pg = self.fake_catalog(publication=self.publication, published=[self.covered])
        guard = AsyncMock(side_effect=cdc.SetupError("Unverified CDC backlog"))
        with patch.object(cdc, "check_unrouted_raw_topics", guard), self.assertRaisesRegex(cdc.SetupError, "backlog"):
            await cdc.prepare_database(pg)
        guard.assert_awaited_once()
        self.assertFalse(any(sql.startswith("ALTER") for sql, _ in pg.calls))
        self.assertEqual(pg.events, ["begin", "rollback"])

    async def test_cutover_reads_offsets_only_for_removed_raw_topics(self):
        retained = "fixture-pg.OddWarehouse.DailyRollup"
        removed = TopicPartition("fixture-pg.LegacyCatalog.ArchiveRows", 0)
        admin, reader = AsyncMock(), AsyncMock()
        admin.list_topics.return_value = [retained, removed.topic, "fixture-pg.transaction"]
        admin.describe_topics.return_value = [{"topic": removed.topic, "partitions": [{"partition": 0}]}]
        reader.beginning_offsets.return_value = {removed: 10}
        reader.end_offsets.return_value = {removed: 20}
        with patch.object(cdc, "AIOKafkaAdminClient", return_value=admin), \
             patch.object(cdc, "AIOKafkaConsumer", return_value=reader), patch.object(cdc.settings, "kafka_brokers", "broker"):
            for position in (None, 15):
                admin.list_consumer_group_offsets.return_value = {} if position is None else {removed: OffsetAndMetadata(position, "")}
                with self.assertRaisesRegex(cdc.SetupError, removed.topic):
                    await cdc.check_unrouted_raw_topics(IDENTITY, self.objects)
            admin.list_consumer_group_offsets.return_value = {removed: OffsetAndMetadata(20, "")}
            self.assertEqual(await cdc.check_unrouted_raw_topics(IDENTITY, self.objects), {removed.topic})
        admin.describe_topics.assert_awaited_with([removed.topic])
        admin.list_consumer_group_offsets.assert_awaited_with("fixture-pg-relay")
        reader.getmany.assert_not_awaited()

    async def test_unrouted_pause_accepts_missing_or_expired_offsets_only_on_empty_partitions(self):
        topic = "fixture-pg.Legacy.Catalog.ArchiveRows"
        expired, peer = TopicPartition(topic, 0), TopicPartition(topic, 1)
        admin, reader = AsyncMock(), AsyncMock()
        admin.list_topics.return_value = [topic]
        admin.describe_topics.return_value = [{"topic": topic, "partitions": [{"partition": 0}, {"partition": 1}]}]
        for paused, start, end, position, peer_position, allowed in [
            (True, 20, 20, 15, 30, True),
            (False, 20, 20, 15, 30, False),
            (True, 19, 20, 15, 30, False),
            (True, 20, 20, 21, 30, False),
            (True, 20, 20, None, 30, True),
            (False, 20, 20, None, 30, False),
            (True, 19, 20, None, 30, False),
            (False, 0, 0, None, 30, True),
            (True, 20, 20, 15, 29, False),
        ]:
            with self.subTest(paused=paused, start=start, end=end, position=position, peer_position=peer_position):
                reader.beginning_offsets.return_value = {expired: start, peer: 25}
                reader.end_offsets.return_value = {expired: end, peer: 30}
                commit = {peer: OffsetAndMetadata(peer_position, "")}
                if position is not None:
                    commit[expired] = OffsetAndMetadata(position, "")
                admin.list_consumer_group_offsets.return_value = commit
                with patch.object(cdc, "AIOKafkaAdminClient", return_value=admin), \
                     patch.object(cdc, "AIOKafkaConsumer", return_value=reader), \
                     patch.object(cdc.settings, "kafka_brokers", "broker"), \
                     patch.object(cdc.settings, "kafka_paused_topics", [topic] if paused else []):
                    if allowed:
                        self.assertEqual(await cdc.check_unrouted_raw_topics(IDENTITY, self.objects), {topic})
                    else:
                        with self.assertRaisesRegex(cdc.SetupError, "Unverified CDC backlog"):
                            await cdc.check_unrouted_raw_topics(IDENTITY, self.objects)

    async def test_dynamic_publication_checks_exact_registration_before_add(self):
        for registered, covered in [(True, False), (True, True), (False, False)]:
            with self.subTest(registered=registered, covered=covered):
                pg = self.fake_catalog(publication=self.publication, published=[self.covered] if covered else [])
                if registered:
                    await cdc.ensure_table_publication(pg, "OddWarehouse", "DailyRollup")
                else:
                    with self.assertRaisesRegex(cdc.SetupError, "active registered current ETL parent"):
                        await cdc.ensure_table_publication(pg, "OddWarehouse", "unregistered")
                additions = [sql for sql, _ in pg.calls if "ADD TABLE" in sql]
                self.assertEqual(len(additions), int(registered and not covered))

    async def test_existing_schema_publication_requires_coverage_and_never_auto_enrolls(self):
        publication = {**self.publication, "schemas": ["OddWarehouse"]}
        covered_pg = self.fake_catalog(publication=publication, published=[self.covered])
        await cdc.ensure_table_publication(covered_pg, "OddWarehouse", "DailyRollup")
        uncovered_pg = self.fake_catalog(publication=publication)
        with self.assertRaisesRegex(cdc.SetupError, "must be enrolled with the existing schema publication"):
            await cdc.ensure_table_publication(uncovered_pg, "OddWarehouse", "DailyRollup")
        self.assertFalse(any(sql.startswith("ALTER") for pg in (covered_pg, uncovered_pg) for sql, _ in pg.calls))

    async def test_relay_routes_only_full_published_registered_tables_in_mixed_publication(self):
        self.objects[("OddWarehouse", "Unpublished")] = ("ods", "another_type")
        external = {"nspname": "OddWarehouse", "relname": "other", "full_table": True}
        publication = {**self.publication, "schemas": ["OddWarehouse"]}
        pg = self.fake_catalog(publication=publication, published=[self.covered, external])
        self.assertEqual(await cdc.published_cdc_objects(pg, IDENTITY),
                         {("OddWarehouse", "DailyRollup"): ("ods", "fixture")})
        restricted = self.fake_catalog(publication=publication, published=[{**self.covered, "full_table": False}])
        with self.assertRaisesRegex(cdc.SetupError, "must include full tables"):
            await cdc.published_cdc_objects(restricted, IDENTITY)

    async def test_relay_subscribes_only_to_exact_published_topics_and_refreshes(self):
        worker = postgres_worker.PostgresSyncWorker(Pg(), IDENTITY)
        worker._consumer = Mock()
        old_topic = "fixture-pg.LegacyCatalog.RetiredRows"
        published = AsyncMock(side_effect=[self.objects, {}])
        check = AsyncMock(side_effect=[{old_topic}, set()])
        with patch.object(postgres_worker, "published_cdc_objects", published), \
             patch.object(postgres_worker, "check_unrouted_raw_topics", check):
            await worker._refresh_subscription()
            first = re.compile(worker._consumer.subscribe.call_args.kwargs["pattern"])
            self.assertTrue(first.fullmatch("fixture-pg.OddWarehouse.DailyRollup"))
            self.assertIsNone(first.fullmatch("fixture-pg.oddwarehouse.DailyRollup"))
            self.assertIsNone(first.fullmatch("fixture-pg.OddWarehouse.another_table"))
            self.assertIsNone(first.fullmatch("second-pg.OddWarehouse.DailyRollup"))
            self.assertTrue(first.fullmatch(old_topic))
            worker._producer = Mock()
            with self.assertRaisesRegex(cdc.SetupError, "no registered ETL route"):
                await worker._publish_batch(TopicPartition(old_topic, 0), [])
            worker._producer.transaction.assert_not_called()
            await worker._refresh_subscription()
            empty = re.compile(worker._consumer.subscribe.call_args.kwargs["pattern"])
            self.assertIsNone(empty.fullmatch("fixture-pg.OddWarehouse.DailyRollup"))

    async def test_relay_pause_keeps_old_and_new_topic_assignments_separate(self):
        worker = postgres_worker.PostgresSyncWorker(Pg(), IDENTITY)
        worker._consumer = Mock()
        old_topic = "fixture-pg.OldWarehouse.DailyRollup"
        target = "fixture-pg.New.Warehouse.DailyRollup"
        with patch.object(postgres_worker, "published_cdc_objects",
                          AsyncMock(return_value={("New.Warehouse", "DailyRollup"): ("ods", "fixture")})), \
             patch.object(postgres_worker, "check_unrouted_raw_topics", AsyncMock(return_value={old_topic})), \
             patch.object(cdc.settings, "kafka_paused_topics", [old_topic]):
            await worker._refresh_subscription()
        pattern = re.compile(worker._consumer.subscribe.call_args.kwargs["pattern"])
        self.assertIsNone(pattern.fullmatch(old_topic))
        self.assertTrue(pattern.fullmatch(target))
        self.assertIsNone(pattern.fullmatch("fixture-pg.NewXWarehouse.DailyRollup"))

    async def test_readonly_cdc_preflight_rejects_an_extra_publication_member(self):
        external = {"nspname": "OddWarehouse", "relname": "unbound", "full_table": True}
        for published in ([self.covered], [self.covered, external]):
            pg = Pg()
            pg.fetch_one.side_effect = [
                {"version": 160000, "wal_level": "logical", "replication": True}, self.publication, None,
            ]
            pg.fetch_all = AsyncMock(side_effect=[[self.table], published, []])
            if len(published) == 1:
                await cdc.check_database(pg)
            else:
                with self.assertRaisesRegex(cdc.SetupError, "differ from registered"):
                    await cdc.check_database(pg)
            pg.execute.assert_not_awaited()

    async def test_readonly_preflight_accepts_schema_publication_only_with_full_registered_parents(self):
        publication = {**self.publication, "schemas": ["OddWarehouse"]}
        external = {**self.covered, "relname": "Retired"}
        for published in ([self.covered, external], [external], [{**self.covered, "full_table": False}]):
            pg = Pg()
            pg.fetch_one.side_effect = [
                {"version": 160000, "wal_level": "logical", "replication": True}, publication, None,
            ]
            pg.fetch_all = AsyncMock(side_effect=[[self.table], published, []])
            if len(published) == 2:
                await cdc.check_database(pg)
            else:
                with self.assertRaisesRegex(cdc.SetupError, "differ from registered"):
                    await cdc.check_database(pg)
            pg.execute.assert_not_awaited()

    async def test_database_rename_uses_saved_slot_and_connector_and_current_database(self):
        metadata = Pg()
        saved = {"connector": "original-connector", "prefix": "original-prefix",
                 "slot": "original_slot", "publication": "original_publication"}
        metadata.fetch_one = AsyncMock(return_value=saved)
        url = "postgresql+asyncpg://postgres@fixture.example:5432/"
        connection = SimpleNamespace(connection_id="3e608188-a262-432b-af2a-d307b0d25e39",
                                     connection_url=url + "OriginalDatabase", sslmode="disable")
        with patch.object(cdc, "get_pg_client", return_value=metadata), \
             patch.object(cdc, "postgres_cdc_identity", new=self.original_identity):
            legacy = await cdc.postgres_cdc_identity(connection)
            connection.connection_url = url + "RenamedDatabase"
            updated = await cdc.postgres_cdc_identity(connection)
            await cdc._ensure_postgres_cdc_identity(connection)
        self.assertEqual(legacy["database"], "OriginalDatabase")
        self.assertEqual(updated["database"], "RenamedDatabase")
        self.assertEqual({key: updated[key] for key in saved}, saved)
        self.assertEqual({key: legacy[key] for key in saved}, saved)
        metadata.execute.assert_not_awaited()
        config = cdc.connector_config(connection, updated, snapshot_mode="initial", snapshot_tables=self.objects)
        self.assertEqual(config["database.dbname"], "RenamedDatabase")
        self.assertEqual(config["topic.prefix"], saved["prefix"])
        self.assertEqual(config["slot.name"], saved["slot"])
        self.assertEqual(config["publication.name"], saved["publication"])
        pattern = config["snapshot.include.collection.list"]
        self.assertIsNotNone(re.search(pattern, "OddWarehouse.DailyRollup", re.IGNORECASE))
        for invalid in ("oddwarehouse.DailyRollup", "OddWarehouse.dailyrollup",
                        "OddWarehouseXDailyRollup", "OddWarehouse.DailyRollupArchive"):
            self.assertIsNone(re.search(pattern, invalid, re.IGNORECASE))

    async def test_prepare_new_connection_allocates_distinct_identities_for_same_database(self):
        metadata = Pg()
        metadata.fetch_one = AsyncMock(return_value=None)
        url = "postgresql+asyncpg://postgres@fixture.example:5432/SameName"
        with patch.object(cdc, "get_pg_client", return_value=metadata), \
             patch.object(cdc.settings, "kafka_topic", "audit"):
            for identifier in ("3e608188-a262-432b-af2a-d307b0d25e39", "7b64fbd5-c2b5-4c54-a1b1-11bd26755b97"):
                await cdc._ensure_postgres_cdc_identity(SimpleNamespace(connection_id=identifier, connection_url=url))
        first, second = (call.args[1] for call in metadata.execute.await_args_list)
        self.assertNotEqual(first["prefix"], second["prefix"])
        self.assertNotEqual(first["slot"], second["slot"])

    def test_setup_only_updates_matching_connect_identity(self):
        config = {"connector.class": "postgres.connector", "plugin.name": "pgoutput", "topic.prefix": "stable",
                  "slot.name": "stable_slot", "publication.name": "stable_pub", "database.dbname": "new_db",
                  "database.hostname": "database.example", "database.port": "5432"}
        response = Mock(is_success=True)
        with patch.object(cdc.httpx, "Client") as client, patch.object(cdc, "register_connector") as register:
            client.return_value.__enter__.return_value.get.return_value = response
            response.json.return_value = {"original": {"info": {"config": {**config, "database.dbname": "old_db"}}}}
            cdc._register_internal_connector("http://connect", "original", config, slot_exists=True)
            register.assert_called_once_with("http://connect", config, name="original")
            register.reset_mock()
            response.json.return_value["original"]["info"]["config"]["slot.name"] = "different_slot"
            with self.assertRaisesRegex(cdc.SetupError, "identity differs"):
                cdc._register_internal_connector("http://connect", "original", config, slot_exists=True)
            register.assert_not_called()
            response.json.return_value["original"]["info"]["config"]["slot.name"] = config["slot.name"]
            offsets = Mock(is_success=True)
            offsets.json.return_value = {"offsets": [{"offset": {"lsn": 123}}]}
            client.return_value.__enter__.return_value.get.side_effect = [response, offsets]
            with self.assertRaisesRegex(cdc.SetupError, "slot is missing"):
                cdc._register_internal_connector("http://connect", "original", config, slot_exists=False)
            register.assert_not_called()

    def test_relay_preserves_contract_and_rejects_other_connection_with_same_table(self):
        row = {"record_id": "1", "data_source": "fixture_source", "data_type": "fixture"}
        table = {"schema_name": "OddWarehouse", "table": "DailyRollup", "layer": "ods", "data_type": "fixture"}
        source = {"connector": "postgresql", "name": IDENTITY["prefix"], "db": IDENTITY["database"],
                  "schema": table["schema_name"], "table": table["table"], "lsn": 7}
        schema = {"fields": [{"field": "after", "type": "struct", "fields": []}]}
        envelope = {"schema": schema, "payload": {"op": "u", "after": row, "source": source}}
        layer, target, value = committed_row(envelope, identity=IDENTITY, **table)
        self.assertEqual(layer, "ods")
        self.assertEqual(target, "DailyRollup")
        self.assertEqual(value, {**row, "_cdc_meta": {"lsn": 7, "schema": "OddWarehouse", "table": "DailyRollup"}})
        self.assertEqual(row_outputs(layer, value), [(cdc.settings.etl_ods_topic, value)])
        for invalid in ({"db": "unselected_database"}, {"name": "other-connection"},
                        {"schema": "oddwarehouse"}, {"table": "dailyrollup"}):
            with patch.dict(source, invalid), self.assertRaisesRegex(ValueError, "published ETL object"):
                committed_row(envelope, identity=IDENTITY, **table)
        with patch.dict(row, {"data_type": "other_type"}), self.assertRaisesRegex(ValueError, "row type"):
            committed_row(envelope, identity=IDENTITY, **table)


if __name__ == "__main__":
    unittest.main()
