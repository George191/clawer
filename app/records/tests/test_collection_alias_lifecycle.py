import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config.settings import settings
from app.etl.base import extract_meta
from app.records.mongo_store import MongoRecordStore
from app.syncer.worker import SyncWorker


@pytest.mark.asyncio
async def test_sync_publishes_and_acknowledges_aliased_collection():
    nvd_route = {
        "template_name": "nvd_api",
        "collection_name": "nvd_vulnerability",
        "data_type": "vulnerability",
    }
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    record = MongoRecordStore._build_record_with_meta(
        template_name=nvd_route["collection_name"],
        data_type=nvd_route["data_type"],
        record_id="unchanged-cve-id",
        record={"cve_id": "CVE-2026-12345"},
        search_params={"__workspace_task_id": "nvd-task"},
        now=now,
    )
    original = deepcopy(record)
    worker = SyncWorker.__new__(SyncWorker)
    worker._template_name = "nvd_api"
    worker._data_type = "vulnerability"
    worker._batch_size = 50
    worker._semaphore = asyncio.Semaphore(1)
    worker._kafka = SimpleNamespace(send_record=AsyncMock())
    worker._mongo = SimpleNamespace(
        get_ready_to_sync=AsyncMock(return_value=[record]),
        update_sync_status=AsyncMock(return_value=True),
    )

    with patch(
        "app.syncer.worker.collection_route_store.list_by_data_type",
        AsyncMock(return_value=[nvd_route]),
    ) as list_routes:
        ready = await worker._read_ready_records()
        result = await worker._sync_record(ready[0])

    list_routes.assert_awaited_once_with("vulnerability", "nvd_api")
    worker._mongo.get_ready_to_sync.assert_awaited_once_with(
        template_name="nvd_vulnerability", limit=50,
    )
    worker._kafka.send_record.assert_awaited_once_with(
        original, topic=f"{settings.kafka_topic}-vulnerability",
    )
    worker._mongo.update_sync_status.assert_awaited_once_with(
        "nvd_vulnerability", "vulnerability", "unchanged-cve-id", "synced",
        updated_at=now, download_version=0,
    )
    assert result == (True, "nvd-task")
    assert record == original
    assert extract_meta(record) == {
        "data_source": "nvd_vulnerability",
        "data_type": "vulnerability",
        "record_id": "unchanged-cve-id",
    }
