"""Exercise Mongo RDS verification and ODS retry/idempotence with local fixtures."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy.exc import OperationalError

from app.etl import mongo_source, ts_ods


STAMP = datetime(2026, 7, 3, tzinfo=timezone.utc)
META = {"record_id": "r1", "template": "news_feed", "data_type": "news",
        "updated_at": STAMP, "download_version": 1, "write_token": "saved-version"}
RAW = {"_meta": META, "title": "Verified"}
SOURCE = {"record_id": "r1", "data_source": "news_feed", "data_type": "news",
          "meta": META, "raw_data": RAW}


class MongoSourceTests(unittest.IsolatedAsyncioTestCase):
    def test_canonical_record_builds_the_existing_ods_input(self):
        result = mongo_source.mongo_ods_input({**RAW, "_kafka_meta": {"kafka_offset": 21}})
        self.assertEqual(result, {"record_id": "r1", "data_type": "news",
                                  "data_source": "news_feed", "raw_data": RAW})
        with self.assertRaisesRegex(ValueError, "write token"):
            mongo_source.mongo_ods_input({**RAW, "_meta": {**META, "write_token": None}})
        with self.assertRaisesRegex(ValueError, "event journal"):
            mongo_source.mongo_ods_input({**RAW, "_meta": {**META, "data_type": "orbital"}})

    async def test_checks_bound_collection_and_exact_persisted_version(self):
        metadata = SimpleNamespace(fetch_one=AsyncMock(return_value={"id": 7}))
        stored = AsyncMock(return_value=[{"_id": "bson", "_meta": {**META, "sync_status": "synced"},
                                         "title": "Verified"}])
        collection = Mock()
        collection.find.return_value.limit.return_value.to_list = stored
        database = Mock()
        database.get_collection.return_value = collection
        source = object.__new__(mongo_source.MongoOdsSource)
        source.metadata, source.database, source.client = metadata, "SourceDb", {"SourceDb": database}

        route = {"collection_name": "news_feed", "enabled": True}
        with patch.object(mongo_source.collection_route_store, "resolve_for_write", new_callable=AsyncMock,
                          return_value=route):
            self.assertTrue(await source.is_current(SOURCE))
            database.get_collection.assert_called_once()
            self.assertEqual(database.get_collection.call_args.args[0], "news_feed")
            self.assertEqual(database.get_collection.call_args.kwargs["read_concern"].level, "majority")
            self.assertEqual(metadata.fetch_one.await_args.args[1], {
                "database": "SourceDb", "collection": "news_feed", "data_type": "news",
            })
            collection.find.assert_called_once_with({
                "_meta.record_id": "r1", "_meta.template": "news_feed", "_meta.data_type": "news",
            })
            stored.return_value = [{**RAW, "_meta": {**META, "download_version": 2}}]
            self.assertFalse(await source.is_current(SOURCE))
            stored.return_value = [{**RAW, "_meta": {**META, "download_version": 0}}]
            with self.assertRaisesRegex(ValueError, "ahead"):
                await source.is_current(SOURCE)
            stored.return_value = [{**RAW, "_meta": {**META, "write_token": "different"}}]
            with self.assertRaisesRegex(ValueError, "cannot order"):
                await source.is_current(SOURCE)
            stored.return_value = [{**RAW, "title": "Changed after publish"}]
            with self.assertRaisesRegex(ValueError, "persisted business payload"):
                await source.is_current(SOURCE)
            metadata.fetch_one.return_value = None
            with self.assertRaisesRegex(ValueError, "No active MongoDB RDS binding"):
                await source.is_current(SOURCE)

    async def test_rolls_back_revalidates_then_replays_without_a_second_write(self):
        columns = {**dict.fromkeys(("record_id", "data_source", "data_type", "title"), "TEXT"),
                   "created_at": "TIMESTAMPTZ", "updated_at": "TIMESTAMPTZ"}
        payload = {**{key: SOURCE[key] for key in ("record_id", "data_source", "data_type")},
                   "title": "Verified", "created_at": STAMP, "updated_at": STAMP}
        worker = object.__new__(ts_ods.TsOds)
        worker._log_prefix = "[test-ods]"
        worker._columns = {"news": columns}
        worker._table_records = {("news", "current"): {"schema_name": "CleanLayer", "table_name": "News"}}
        worker._mongo_source = SimpleNamespace(is_current=AsyncMock(return_value=True))
        events = []

        class Destination:
            fail_once = True
            persisted = None
            calls = 0

            @asynccontextmanager
            async def locked_transaction(self, key):
                events.append(("lock", key))
                self.pending = self.persisted
                yield self
                if self.fail_once:
                    self.fail_once = False
                    events.append("rollback")
                    raise OperationalError(None, None, ConnectionError("lost commit request"))
                self.persisted = self.pending
                events.append("commit")

            async def execute(self, query, values):
                self.calls += 1
                statement = str(query)
                assert 'INSERT INTO "CleanLayer"."News" AS target' in statement
                assert 'WHERE target."title" IS DISTINCT FROM EXCLUDED."title"' in statement
                assert 'WHERE target."updated_at"' not in statement
                if self.persisted and self.persisted["title"] == values["title"]:
                    row = None
                else:
                    self.pending = row = dict(values)
                return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: row))

        worker._pg = Destination()
        with patch.object(ts_ods, "_ODS_WRITE_RETRY_WAIT", 0):
            self.assertEqual(await worker._write_outputs([payload], SOURCE), [(payload, payload)])
            self.assertEqual(await worker._write_outputs([payload], SOURCE), [])
        self.assertEqual(worker._pg.calls, 3)
        self.assertEqual(worker._mongo_source.is_current.await_count, 3)
        self.assertEqual([event for event in events if isinstance(event, str)], ["rollback", "commit", "commit"])
