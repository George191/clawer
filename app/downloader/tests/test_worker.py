"""Downloader single-attempt and deferred compensation regressions with mocked I/O."""

import asyncio
import errno
import hashlib
import sys
import tracemalloc
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch

for root in (Path.cwd(), *Path(__file__).resolve().parents):
    if (root / "app" / "__init__.py").is_file():
        sys.path.insert(0, str(root))
        break

from app.base.http import DownloadCleanupError, DownloadError, DownloadResponse, FileDownloadResponse
from app.downloader import worker as module
from app.downloader.adapters.sec_edgar import validate_response as validate_sec_response
from app.records.download_resources import apply_record_updates


class DownloadWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.worker = module.DownloadWorker.__new__(module.DownloadWorker)
        self.worker._http = types.SimpleNamespace(
            download_to_file=AsyncMock(), mark_last_proxy_failed=AsyncMock(),
        )
        self.worker._minio = types.SimpleNamespace(upload_file=AsyncMock(return_value="stored/object"))
        self.item = module.DownloadItem(
            url="https://fixture.invalid/file", filename="file", asset_key="assets.file", attachment=False,
            version=None,
        )
        self.response = DownloadResponse(
            self.item.url, self.item.url, 200, {"content-type": "application/octet-stream"}, b"fixture-data",
        )
        self.paths = []
        self.outcomes = []
        self.worker._http.download_to_file.side_effect = self.fetch_fixture
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

    async def fetch_fixture(self, url, path, **kwargs):
        self.paths.append(path)
        response = self.outcomes.pop(0) if self.outcomes else self.response
        if isinstance(response, BaseException):
            raise response
        path.write_bytes(response.data)
        return FileDownloadResponse(
            response.source_url, response.final_url, response.status_code, response.headers,
            path, len(response.data),
        )

    def assert_temporary_files_removed(self):
        for call in self.worker._http.download_to_file.await_args_list:
            self.assertFalse(call.args[1].parent.exists())

    async def test_503_fails_once_without_sleep_or_upload(self):
        error = DownloadError(self.item.url, 503, "unavailable")
        self.worker._http.download_to_file.side_effect = error
        with patch.object(asyncio, "sleep", AsyncMock()) as sleep:
            with self.assertRaises(DownloadError) as raised:
                await self.download()
            sleep.assert_not_awaited()
        self.assertIs(raised.exception, error)
        self.assertEqual(self.worker._http.download_to_file.await_count, 1)
        self.worker._minio.upload_file.assert_not_awaited()
        self.assert_temporary_files_removed()

    async def test_404_is_missing_without_retry_or_upload(self):
        self.worker._http.download_to_file.side_effect = DownloadError(self.item.url, 404, "missing")
        self.assertIsNone(await self.download())
        self.assertEqual(self.worker._http.download_to_file.await_count, 1)
        self.worker._http.mark_last_proxy_failed.assert_not_awaited()
        self.worker._minio.upload_file.assert_not_awaited()
        self.assert_temporary_files_removed()

    async def test_full_disk_does_not_mark_proxy_failed(self):
        error = OSError(errno.ENOSPC, "download disk is full")
        self.worker._http.download_to_file.side_effect = error
        with self.assertRaises(OSError) as raised:
            await self.download()
        self.assertIs(raised.exception, error)
        self.worker._http.mark_last_proxy_failed.assert_not_awaited()
        self.worker._minio.upload_file.assert_not_awaited()
        self.assert_temporary_files_removed()

    async def test_cleanup_failure_propagates_without_retry(self):
        error = DownloadCleanupError(self.item.url, message="cleanup unconfirmed")
        self.worker._http.download_to_file.side_effect = error
        with self.assertRaises(DownloadCleanupError) as raised:
            await self.download()
        self.assertIs(raised.exception, error)
        self.assertEqual(self.worker._http.download_to_file.await_count, 1)
        self.assert_temporary_files_removed()

    async def test_slow_fetch_completes_without_total_budget(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def stalled(url, path, **kwargs):
            path.write_bytes(b"partial")
            started.set()
            await release.wait()
            return await self.fetch_fixture(url, path, **kwargs)

        module.settings.http_download_timeout = 0.03
        self.worker._http.download_to_file.side_effect = stalled
        task = asyncio.create_task(self.download())
        try:
            await started.wait()
            await asyncio.sleep(0.06)
            self.assertFalse(task.done())
            release.set()
            self.assertEqual(await asyncio.wait_for(task, 1), "stored/object")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.worker._http.download_to_file.await_count, 1)
        self.worker._minio.upload_file.assert_awaited_once()

    async def test_external_cancellation_stays_cancelled(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def stalled(url, path, **kwargs):
            path.write_bytes(b"partial")
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.worker._http.download_to_file.side_effect = stalled
        task = asyncio.create_task(self.download())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(task.cancelled())
        self.assertTrue(cancelled.is_set())
        self.worker._minio.upload_file.assert_not_awaited()
        self.assert_temporary_files_removed()

    async def test_fetch_preserves_task_identity_for_proxy_cleanup(self):
        owner = asyncio.current_task()

        async def fetch(*args, **kwargs):
            self.assertIs(asyncio.current_task(), owner)
            return await self.fetch_fixture(*args, **kwargs)

        self.worker._http.download_to_file.side_effect = fetch
        self.assertEqual(await self.download(), "stored/object")
        self.worker._minio.upload_file.assert_awaited_once()

    async def test_failure_retries_only_on_next_independent_call(self):
        self.outcomes = [DownloadError(self.item.url, 503), self.response]
        with self.assertRaises(DownloadError):
            await self.download()
        self.assertEqual(self.worker._http.download_to_file.await_count, 1)
        self.worker._minio.upload_file.assert_not_awaited()
        self.assert_temporary_files_removed()
        self.assertEqual(await self.download(), "stored/object")
        self.assertEqual(self.worker._http.download_to_file.await_count, 2)
        self.worker._minio.upload_file.assert_awaited_once()

    async def test_large_file_preserves_hash_object_key_with_bounded_memory(self):
        block = b"bounded-download-fixture" * 2048
        count = 768
        expected = hashlib.sha256()
        for _ in range(count):
            expected.update(block)
        expected_size = len(block) * count
        iterations = 0
        finished = False

        async def heartbeat():
            nonlocal iterations
            while not finished:
                iterations += 1
                await asyncio.sleep(0)

        async def fetch(url, path, **kwargs):
            self.paths.append(path)
            with path.open("wb") as stream:
                for _ in range(count):
                    stream.write(block)
            return FileDownloadResponse(url, url, 200, self.response.headers, path, expected_size)

        async def upload(path, template, kind, filename, content_type):
            self.assertEqual((template, kind, content_type), ("fixture", "news", "application/octet-stream"))
            self.assertEqual(path.stat().st_size, expected_size)
            actual = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    actual.update(chunk)
            self.assertEqual(actual.hexdigest(), expected.hexdigest())
            self.assertEqual(filename, f"record/{expected.hexdigest()}")
            return "stored/object"

        self.worker._http.download_to_file.side_effect = fetch
        self.worker._minio.upload_file.side_effect = upload
        pulse = asyncio.create_task(heartbeat())
        tracemalloc.start()
        try:
            self.assertEqual(await self.download(), "stored/object")
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            finished = True
            await pulse
        self.assertGreater(expected_size, 32 * 1024 * 1024)
        self.assertLess(peak, 8 * 1024 * 1024)
        self.assertGreater(iterations, 10)
        self.assert_temporary_files_removed()

    async def test_type_detection_uses_prefix_and_preserves_extension_in_object_key(self):
        def detect(data):
            self.assertEqual(data, self.response.data)
            return types.SimpleNamespace(mime="image/png", extension="png")

        with patch.object(module.filetype, "guess", detect):
            await self.download()
        call = self.worker._minio.upload_file.await_args
        expected = hashlib.sha256(self.response.data).hexdigest()
        self.assertEqual(call.args[3], f"record/{expected}.png")
        self.assertEqual(call.args[4], "image/png")
        self.assert_temporary_files_removed()

    async def test_detected_extension_is_not_duplicated_in_flat_object_path(self):
        for filename in ('photo.png', 'photo.png.png', 'photo.jpg'):
            self.item = replace(self.item, filename=filename)
            with patch.object(module.filetype, 'guess', lambda _: types.SimpleNamespace(mime='image/png', extension='png')):
                await self.download()
            expected = hashlib.sha256(self.response.data).hexdigest()
            self.assertEqual(self.worker._minio.upload_file.await_args.args[3], f'record/{expected}.png')

    async def test_sec_file_validation_rejects_auth_page_before_upload(self):
        self.response = replace(self.response, data=b"<html>login.zscaler.net</html>")
        with patch.object(module, "get_download_response_validator", lambda _: validate_sec_response):
            with self.assertRaisesRegex(DownloadError, "Zscaler authentication page"):
                await self.download()
        self.worker._http.mark_last_proxy_failed.assert_awaited_once()
        self.worker._minio.upload_file.assert_not_awaited()
        self.assert_temporary_files_removed()

    async def test_upload_failure_cleans_file_without_marking_download_proxy(self):
        self.worker._minio.upload_file.side_effect = OSError("upload failed")
        with self.assertRaisesRegex(OSError, "upload failed"):
            await self.download()
        self.worker._http.mark_last_proxy_failed.assert_not_awaited()
        self.assert_temporary_files_removed()

    async def test_upload_cancellation_keeps_file_until_upload_has_stopped(self):
        started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        path = None

        async def upload(file_path, *args):
            nonlocal path
            path = file_path
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                self.assertTrue(path.exists())
                raise

        self.worker._minio.upload_file.side_effect = upload
        task = asyncio.create_task(self.download())
        try:
            await started.wait()
            task.cancel()
            await cancelled.wait()
            self.assertTrue(path.exists())
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assert_temporary_files_removed()
    async def test_html_attachment_does_not_retry_forever(self):
        self.item = replace(self.item, attachment=True)
        self.response = DownloadResponse(
            self.item.url, self.item.url, 200, {"content-type": "text/html"}, b"<html>challenge</html>",
        )
        with self.assertRaisesRegex(DownloadError, "attachment is HTML"):
            await self.download()
        self.assertEqual(self.worker._http.download_to_file.await_count, 1)
        self.worker._minio.upload_file.assert_not_awaited()
        self.assert_temporary_files_removed()

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
        self.outcomes = [DownloadError(self.item.url, 503), self.response]
        await self.worker._download_record(record)
        self.assertEqual(self.worker._http.download_to_file.await_count, 2)
        args = self.worker._update_mongo.await_args.args
        self.assertEqual(args[3], "pending")
        updates = args[2]
        self.assertIn("_meta.download_retry_at", updates)
        self.assertEqual(updates["assets.good"], "stored/object")
        self.assertNotIn("assets.file", updates)
        self.assertEqual(set(module.decode_manifest(updates["_meta.downloaded_inputs"])), {"assets.good"})

        recovered = apply_record_updates(record, updates)
        self.worker._http.download_to_file.reset_mock()
        self.worker._http.download_to_file.side_effect = self.fetch_fixture
        self.worker._update_mongo.reset_mock()
        await self.worker._download_record(recovered)
        self.worker._http.download_to_file.assert_awaited_once_with(self.item.url, ANY, use_proxy=True)
        final_args = self.worker._update_mongo.await_args.args
        self.assertEqual(final_args[3], "downloaded")
        self.assertEqual(set(module.decode_manifest(final_args[2]["_meta.downloaded_inputs"])), {"assets.file", "assets.good"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
