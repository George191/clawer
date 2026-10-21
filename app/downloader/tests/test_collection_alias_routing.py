from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.downloader.worker import DownloadWorker


@pytest.mark.asyncio
async def test_unfiltered_worker_loads_alias_owner_without_restricting_collection_scan():
    worker = DownloadWorker.__new__(DownloadWorker)
    worker._data_type = None
    worker._template_name = None
    worker._batch_size = 50
    worker._collection_cursor = 0
    worker._collection_routes = {}
    record = {
        "_meta": {
            "template": "nvd_vulnerability",
            "data_type": "vulnerability",
            "record_id": "cve-record",
            "download_claim_token": "claim-token",
        },
    }
    worker._mongo = SimpleNamespace(get_pending_downloads=AsyncMock(return_value=[record]))
    worker._load_template = AsyncMock(return_value=SimpleNamespace(download=[]))
    worker._update_mongo = AsyncMock()
    route = {
        "template_name": "nvd_api",
        "collection_name": "nvd_vulnerability",
        "data_type": "vulnerability",
    }

    with patch(
        "app.downloader.worker.collection_route_store.list_by_data_type",
        AsyncMock(return_value=[route]),
    ) as list_routes:
        records = await worker._read_pending_records()
        await worker._download_record(records[0])

    list_routes.assert_awaited_once_with(None, None)
    worker._mongo.get_pending_downloads.assert_awaited_once_with(template_name=None, limit=50)
    worker._load_template.assert_awaited_once_with("nvd_api")
    worker._update_mongo.assert_awaited_once_with(
        "nvd_vulnerability", "cve-record", {}, "no_assets", "claim-token",
    )
