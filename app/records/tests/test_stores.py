"""Record persistence keeps routing, deduplication and lifecycle behavior."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.records import mongo_store
from app.records.adapters import RecordAdapter
from app.records.file_store import FileRecordStore
from app.records.mongo_store import MongoRecordStore
from app.storage.file_client import FileClient
from app.storage.mongo_client import MongoClient
from app.utils.record_id import resolve_record_id


def test_file_client_round_trips_json_without_record_metadata(tmp_path):
    client = FileClient(str(tmp_path))
    path = tmp_path / "document.json"
    document = {"value": [1, "中文"], "nested": {"empty": None}}

    client.write_json(path, document)

    assert client.read_json(path) == document


@pytest.mark.asyncio
async def test_file_record_replay_preserves_sync_state_and_deduplication(tmp_path):
    store = FileRecordStore(str(tmp_path))
    record = {"id": "item-1", "title": "original"}
    paths = await store.save_records("sample", "news", ["id"], [deepcopy(record)])
    record_id = Path(paths[0]).stem
    await store.update_file_status("sample", "news", record_id, "asset.pdf", "downloaded")
    await store.update_sync_status("sample", "news", record_id, "synced")

    replayed = await store.save_records("sample", "news", ["id"], [deepcopy(record)])

    assert replayed == paths
    assert len(list(tmp_path.rglob("*.json"))) == 1
    assert store.last_save_stats == {
        "inserted": 0, "updated": 0, "unchanged": 1, "deleted": 0,
    }
    document = store.read_json(Path(paths[0]))
    assert document["_meta"]["download_status"] == "downloaded"
    assert document["_meta"]["sync_status"] == "synced"

    await store.save_records("sample", "news", ["id"], [{**record, "title": "changed"}])

    document = store.read_json(Path(paths[0]))
    assert document["title"] == "changed"
    assert document["_meta"]["download_status"] == "downloaded"
    assert document["_meta"]["sync_status"] == "pending"


@pytest.mark.asyncio
async def test_mongo_client_uses_literal_collection_name_without_record_indexes():
    collection = MagicMock()
    collection.create_index = AsyncMock()
    client = MongoClient()
    client._db = {"twitter": collection}

    assert await client._get_collection("twitter") is collection
    collection.create_index.assert_not_awaited()


@pytest.fixture
def mongo_record_store(monkeypatch):
    route = {"template_name": "sample", "collection_name": "configured_records", "data_type": "news"}
    routes = SimpleNamespace(resolve_for_write=AsyncMock(return_value=route))
    monkeypatch.setattr(mongo_store, "collection_route_store", routes)
    monkeypatch.setattr(RecordAdapter, "after_save", AsyncMock())
    collection = MagicMock()
    cursor = MagicMock()
    cursor.__aiter__.return_value = []
    collection.find.return_value = cursor
    collection.bulk_write = AsyncMock()
    collection.update_one = AsyncMock(return_value=SimpleNamespace(matched_count=1))
    store = MongoRecordStore()
    store._db = {"configured_records": collection}
    store._get_collection_by_name = AsyncMock(return_value=collection)
    store._ensure_indexes = AsyncMock()
    return store, collection, cursor, routes


@pytest.mark.asyncio
async def test_mongo_record_save_uses_database_route_and_deduplicates_batch(mongo_record_store):
    store, collection, _cursor, routes = mongo_record_store

    ids = await store.save_records("sample", "news", ["id"], [
        {"id": "item-1", "title": "first", "content": "retained"},
        {"id": "item-1", "title": "latest"},
    ])

    routes.resolve_for_write.assert_awaited_once_with("sample", "news")
    store._get_collection_by_name.assert_awaited_once_with("configured_records", indexes=None)
    assert ids == [resolve_record_id({"id": "item-1"})] * 2
    operations = collection.bulk_write.await_args.args[0]
    assert len(operations) == 1
    document = operations[0]._doc
    assert document["title"] == "latest"
    assert document["content"] == "retained"
    assert document["_meta"]["template"] == "configured_records"
    assert document["_meta"]["download_status"] == "pending"
    assert document["_meta"]["sync_status"] == "pending"
    assert store.last_save_stats["inserted"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_mongo_record_replay_retains_downloads_and_resets_only_changed_sync(
    mongo_record_store, changed,
):
    store, collection, cursor, _routes = mongo_record_store
    previous_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    record_id = resolve_record_id({"id": "item-1"})
    existing = {
        "id": "item-1",
        "title": "original",
        "_meta": {
            "template": "configured_records",
            "data_type": "news",
            "record_id": record_id,
            "download_status": "downloaded",
            "sync_status": "synced",
            "created_at": previous_time,
            "updated_at": previous_time,
            "search_params": {"__workspace_task_id": "task", "__workspace_run_id": "old-run"},
        },
    }
    cursor.__aiter__.return_value = [deepcopy(existing)]

    ids = await store.save_records("sample", "news", ["id"], [
        {"id": "item-1", "title": "changed" if changed else "original"},
    ])

    assert ids == [record_id]
    if not changed:
        collection.bulk_write.assert_not_awaited()
        assert store.last_save_stats["unchanged"] == 1
        return
    document = collection.bulk_write.await_args.args[0][0]._doc
    assert document["_meta"]["created_at"] == previous_time
    assert document["_meta"]["updated_at"] > previous_time
    assert document["_meta"]["download_status"] == "downloaded"
    assert document["_meta"]["sync_status"] == "pending"
    assert "__workspace_run_id" not in document["_meta"]["search_params"]
    assert store.last_save_stats["updated"] == 1


@pytest.mark.asyncio
async def test_sync_acknowledgement_checks_exported_version(mongo_record_store):
    store, collection, _cursor, _routes = mongo_record_store
    exported_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    acknowledged = await store.update_sync_status(
        "configured_records", "news", "item-1", "synced",
        updated_at=exported_time, download_version=3,
    )

    assert acknowledged
    query, update = collection.update_one.await_args.args
    assert query == {
        "_meta.record_id": "item-1",
        "_meta.sync_status": {"$ne": "synced"},
        "_meta.updated_at": exported_time,
        "_meta.download_version": 3,
    }
    assert update["$set"]["_meta.synced_at"] == exported_time
