"""Regression coverage for download retries and task cleanup."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from curl_cffi.requests import Response
from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError
from curl_cffi.requests.exceptions import HTTPError

from app.base.http import DownloadResponse, HttpClient
from app.downloader import worker as worker_module
from app.downloader.adapters import DownloadItem
from app.downloader.worker import NOT_FOUND_ASSET, DownloadWorker


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> DownloadWorker:
    minio = MagicMock(upload_bytes=AsyncMock(return_value="https://files.test/asset.pdf"))
    mongo = MagicMock(update_download_result=AsyncMock(return_value=True))
    monkeypatch.setattr(worker_module, "MinioClient", MagicMock(return_value=minio))
    monkeypatch.setattr(worker_module, "MongoClient", MagicMock(return_value=mongo))
    monkeypatch.setattr(worker_module.settings, "http_retry_on_statuses", [429, 500, 502, 503, 504])
    instance = DownloadWorker()
    instance._http = MagicMock(spec=HttpClient)
    return instance


@pytest.fixture
def item() -> DownloadItem:
    return DownloadItem(
        url="https://source.test/asset.pdf", filename="asset.pdf", asset_key="assets.pdf",
    )


def http_error(status: int) -> HTTPError:
    response = Response()
    response.status_code = status
    return HTTPError(f"HTTP {status}", response=response)


def transport_error(status: int) -> CurlConnectionError:
    response = Response()
    response.status_code = status
    return CurlConnectionError("connection reset", response=response)


def download_response(item: DownloadItem) -> DownloadResponse:
    return DownloadResponse(
        source_url=item.url, final_url=item.url, status_code=200,
        headers={"content-type": "application/pdf"}, data=b"%PDF-test",
    )


@pytest.mark.asyncio
async def test_http_404_is_saved_as_missing_without_retry_or_proxy_penalty(
    worker: DownloadWorker, item: DownloadItem, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker._http.download_response.side_effect = http_error(404)
    worker._load_template = AsyncMock(return_value=SimpleNamespace(
        download=[], effective_download_use_proxy=True,
    ))
    worker._download_template_fields = MagicMock(return_value=[item])
    sleep = AsyncMock()
    monkeypatch.setattr(worker_module.asyncio, "sleep", sleep)
    record = {"_meta": {
        "template": "example", "data_type": "binary", "record_id": "record-1",
        "download_claim_token": "claim-1",
    }}

    await worker._download_record(record)

    worker._http.download_response.assert_awaited_once_with(item.url, use_proxy=True)
    worker._http.mark_last_proxy_failed.assert_not_called()
    worker._minio.upload_bytes.assert_not_awaited()
    sleep.assert_not_awaited()
    worker._mongo.update_download_result.assert_awaited_once_with(
        "example", "record-1", {"assets.pdf": NOT_FOUND_ASSET}, "downloaded",
        claim_token="claim-1",
    )


@pytest.mark.parametrize("status", [400, 403, 410])
@pytest.mark.asyncio
async def test_permanent_http_errors_fail_once_without_proxy_penalty(
    worker: DownloadWorker, item: DownloadItem, monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    error = http_error(status)
    worker._http.download_response.side_effect = error
    sleep = AsyncMock()
    monkeypatch.setattr(worker_module.asyncio, "sleep", sleep)

    with pytest.raises(HTTPError) as caught:
        await worker._download_item(item, "example", "binary", "record-1", True)

    assert caught.value is error
    worker._http.download_response.assert_awaited_once_with(item.url, use_proxy=True)
    worker._http.mark_last_proxy_failed.assert_not_called()
    worker._minio.upload_bytes.assert_not_awaited()
    sleep.assert_not_awaited()


@pytest.mark.parametrize("error", [
    http_error(429), http_error(503), OSError("connection reset"),
    transport_error(0), transport_error(200),
])
@pytest.mark.asyncio
async def test_transient_errors_retry_and_upload_after_recovery(
    worker: DownloadWorker, item: DownloadItem, monkeypatch: pytest.MonkeyPatch, error: Exception,
) -> None:
    worker._http.download_response.side_effect = [error, download_response(item)]
    sleep = AsyncMock()
    monkeypatch.setattr(worker_module.asyncio, "sleep", sleep)

    result = await worker._download_item(item, "example", "binary", "record-1", True)

    assert result == "https://files.test/asset.pdf"
    assert worker._http.download_response.await_count == 2
    worker._http.mark_last_proxy_failed.assert_not_called()
    sleep.assert_awaited_once_with(1)
    worker._minio.upload_bytes.assert_awaited_once_with(
        b"%PDF-test", "example", "binary", "record-1/asset.pdf", "application/pdf",
    )


@pytest.mark.asyncio
async def test_transient_failures_stop_after_five_attempts(
    worker: DownloadWorker, item: DownloadItem, monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker._http.download_response.side_effect = http_error(503)
    sleep = AsyncMock()
    monkeypatch.setattr(worker_module.asyncio, "sleep", sleep)

    with pytest.raises(HTTPError):
        await worker._download_item(item, "example", "binary", "record-1", True)

    assert worker._http.download_response.await_count == 5
    assert sleep.await_args_list == [call(1), call(2), call(4), call(8)]
    worker._http.mark_last_proxy_failed.assert_not_called()
    worker._minio.upload_bytes.assert_not_awaited()


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.asyncio
async def test_record_success_and_failure_release_proxy_in_the_download_task(
    worker: DownloadWorker, failure: bool,
) -> None:
    download_tasks = []
    release_tasks = []

    async def download(record: dict) -> None:
        download_tasks.append(asyncio.current_task())
        if failure:
            raise RuntimeError("download failed")

    async def release() -> None:
        release_tasks.append(asyncio.current_task())

    worker._download_record = AsyncMock(side_effect=download)
    worker._http.release_current_task_proxy.side_effect = release

    if failure:
        with pytest.raises(RuntimeError, match="download failed"):
            await worker._download_list([{}])
    else:
        await worker._download_list([{}])

    worker._http.release_current_task_proxy.assert_awaited_once()
    assert release_tasks == download_tasks


@pytest.mark.asyncio
async def test_cancelled_batch_releases_running_task_proxy(worker: DownloadWorker) -> None:
    started = asyncio.Event()

    async def download(record: dict) -> None:
        started.set()
        await asyncio.Event().wait()

    worker._download_record = AsyncMock(side_effect=download)
    batch = asyncio.create_task(worker._download_list([{}]))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
    finally:
        batch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await batch

    worker._http.release_current_task_proxy.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_record_cancels_siblings_before_batch_returns(
    worker: DownloadWorker, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_module.settings, "download_asset_concurrency", 2)
    blocked_started = asyncio.Event()
    blocked_stopped = asyncio.Event()
    download_tasks = []
    release_tasks = []

    async def download(record: dict) -> None:
        download_tasks.append(asyncio.current_task())
        if record["id"] == "failed":
            await blocked_started.wait()
            raise RuntimeError("download failed")
        blocked_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            blocked_stopped.set()

    async def release() -> None:
        release_tasks.append(asyncio.current_task())

    worker._download_record = AsyncMock(side_effect=download)
    worker._http.release_current_task_proxy.side_effect = release

    with pytest.raises(RuntimeError, match="download failed"):
        await asyncio.wait_for(
            worker._download_list([{"id": "blocked"}, {"id": "failed"}]), timeout=1,
        )

    assert blocked_stopped.is_set()
    assert len(download_tasks) == 2
    assert all(task.done() for task in download_tasks)
    assert set(release_tasks) == set(download_tasks)
    assert worker._http.release_current_task_proxy.await_count == 2


@pytest.mark.asyncio
async def test_batch_uses_download_concurrency_instead_of_template_concurrency(
    worker: DownloadWorker, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_module.settings, "download_asset_concurrency", 2)
    monkeypatch.setattr(worker_module.settings, "max_concurrent_tasks", 1)
    started = asyncio.Event()
    finish = asyncio.Event()
    active = 0
    peak = 0

    async def download(record: dict) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            started.set()
        await finish.wait()
        active -= 1

    worker._download_record = AsyncMock(side_effect=download)
    batch = asyncio.create_task(worker._download_list([{}, {}, {}, {}]))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert worker._download_record.await_count == 2
        finish.set()
        await asyncio.wait_for(batch, timeout=1)
    finally:
        finish.set()
        batch.cancel()
        await asyncio.gather(batch, return_exceptions=True)

    assert peak == 2
    assert worker._download_record.await_count == 4
    assert worker._http.release_current_task_proxy.await_count == 4
