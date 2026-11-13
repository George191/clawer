"""Downloader single-attempt and deferred compensation regressions with mocked I/O."""

import asyncio
import sys
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

for root in (Path.cwd(), *Path(__file__).resolve().parents):
    if (root / "app" / "__init__.py").is_file():
        sys.path.insert(0, str(root))
        break

from app.base.http import DownloadCleanupError, DownloadError, DownloadResponse
from app.downloader import worker as module
from app.records.download_resources import apply_record_updates


class DownloadWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.worker = module.DownloadWorker.__new__(module.DownloadWorker)
        self.worker._http = types.SimpleNamespace(
            download_response=AsyncMock(), mark_last_proxy_failed=AsyncMock(),
        )
        self.worker._minio = types.SimpleNamespace(upload_bytes=AsyncMock(return_value="stored/object"))
        self.item = module.DownloadItem(
            url="https://fixture.invalid/file", filename="file", asset_key="assets.file", attachment=False,
            version=None,
        )
        self.response = DownloadResponse(
            self.item.url, self.item.url, 200, {"content-type": "application/octet-stream"}, b"fixture-data",
        )
        self.worker._http.download_response.return_value = self.response
        self.patches = [
            patch.object(module.settings, "http_download_timeout", 1.0),
            patch.object(module, "get_download_response_validator", lambda _: None),
            patch.object(module.filetype, "guess", lambda _: None),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    async def download(self):
        return await self.worker._download_item(self.item, "fixture", "news", "record", True)

    async def test_503_fails_once_without_sleep_or_upload(self):
        error = DownloadError(self.item.url, 503, "unavailable")
        self.worker._http.download_response.side_effect = error
        with patch.object(asyncio, "sleep", AsyncMock()) as sleep:
            with self.assertRaises(DownloadError) as raised:
                await self.download()
            sleep.assert_not_awaited()
        self.assertIs(raised.exception, error)
        self.assertEqual(self.worker._http.download_response.await_count, 1)
        self.worker._minio.upload_bytes.assert_not_awaited()

    async def test_404_is_missing_without_retry_or_upload(self):
        self.worker._http.download_response.side_effect = DownloadError(self.item.url, 404, "missing")
        self.assertIsNone(await self.download())
        self.assertEqual(self.worker._http.download_response.await_count, 1)
        self.worker._http.mark_last_proxy_failed.assert_not_awaited()
        self.worker._minio.upload_bytes.assert_not_awaited()

    async def test_cleanup_failure_propagates_without_retry(self):
        error = DownloadCleanupError(self.item.url, message="cleanup unconfirmed")
        self.worker._http.download_response.side_effect = error
        with self.assertRaises(DownloadCleanupError) as raised:
            await self.download()
        self.assertIs(raised.exception, error)
        self.assertEqual(self.worker._http.download_response.await_count, 1)

    async def test_proxy_acquisition_stall_obeys_total_fetch_budget(self):
        cancelled = asyncio.Event()

        async def stalled(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        module.settings.http_download_timeout = 0.03
        self.worker._http.download_response.side_effect = stalled
        with self.assertRaisesRegex(DownloadError, "resource fetch exceeded"):
            await self.download()
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.worker._http.download_response.await_count, 1)
        self.worker._minio.upload_bytes.assert_not_awaited()

    async def test_external_cancellation_stays_cancelled(self):
        started = asyncio.Event()

        async def stalled(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        self.worker._http.download_response.side_effect = stalled
        task = asyncio.create_task(self.download())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(task.cancelled())

    async def test_fetch_preserves_task_identity_for_proxy_cleanup(self):
        owner = asyncio.current_task()

        async def fetch(*args, **kwargs):
            self.assertIs(asyncio.current_task(), owner)
            return self.response

        self.worker._http.download_response.side_effect = fetch
        self.assertEqual(await self.download(), "stored/object")
        self.worker._minio.upload_bytes.assert_awaited_once()

    async def test_failure_retries_only_on_next_independent_call(self):
        self.worker._http.download_response.side_effect = [DownloadError(self.item.url, 503), self.response]
        with self.assertRaises(DownloadError):
            await self.download()
        self.assertEqual(self.worker._http.download_response.await_count, 1)
        self.worker._minio.upload_bytes.assert_not_awaited()
        self.assertEqual(await self.download(), "stored/object")
        self.assertEqual(self.worker._http.download_response.await_count, 2)
        self.worker._minio.upload_bytes.assert_awaited_once()

    async def test_html_attachment_does_not_retry_forever(self):
        self.item = replace(self.item, attachment=True)
        self.worker._http.download_response.return_value = DownloadResponse(
            self.item.url, self.item.url, 200, {"content-type": "text/html"}, b"<html>challenge</html>",
        )
        with self.assertRaisesRegex(DownloadError, "attachment is HTML"):
            await self.download()
        self.assertEqual(self.worker._http.download_response.await_count, 1)
        self.worker._minio.upload_bytes.assert_not_awaited()

    async def test_record_keeps_partial_success_and_next_scan_fetches_only_failed_asset(self):
        good = replace(self.item, url="https://fixture.invalid/good", asset_key="assets.good")
        items = [self.item, good]
        self.worker._collection_routes = {
            "fixture_collection": {"template_name": "fixture", "data_type": "news"},
        }
        self.worker._poll_interval = 0.5
        self.worker._load_template = AsyncMock(return_value=types.SimpleNamespace(effective_download_use_proxy=True))
        self.worker._update_mongo = AsyncMock(return_value=True)
        record = {
            "_meta": {
                "template": "fixture_collection", "record_id": "record", "download_claim_token": "claim",
                "download_plan": module.encode_plan(items),
                "download_inputs": module.encode_manifest(module.resource_manifest(items)),
                "downloaded_inputs": [],
            },
            "assets": {},
        }
        self.worker._http.download_response.side_effect = [DownloadError(self.item.url, 503), self.response]
        await self.worker._download_record(record)
        self.assertEqual(self.worker._http.download_response.await_count, 2)
        args = self.worker._update_mongo.await_args.args
        self.assertEqual(args[3], "pending")
        updates = args[2]
        self.assertIn("_meta.download_retry_at", updates)
        self.assertEqual(updates["assets.good"], "stored/object")
        self.assertNotIn("assets.file", updates)
        self.assertEqual(set(module.decode_manifest(updates["_meta.downloaded_inputs"])), {"assets.good"})

        recovered = apply_record_updates(record, updates)
        self.worker._http.download_response.reset_mock()
        self.worker._http.download_response.side_effect = None
        self.worker._http.download_response.return_value = self.response
        self.worker._update_mongo.reset_mock()
        await self.worker._download_record(recovered)
        self.worker._http.download_response.assert_awaited_once_with(self.item.url, use_proxy=True)
        final_args = self.worker._update_mongo.await_args.args
        self.assertEqual(final_args[3], "downloaded")
        self.assertEqual(set(module.decode_manifest(final_args[2]["_meta.downloaded_inputs"])), {"assets.file", "assets.good"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
