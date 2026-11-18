"""File upload cancellation must not delete a file still used by the SDK thread."""

import asyncio
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from app.storage.minio_client import MinioClient


class FileUploadCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.payload = None
        self.failure = None
        self.path = None
        self.client = MinioClient(bucket="fixture")
        self.client._ensure_connection = AsyncMock()
        self.client._client = SimpleNamespace(fput_object=self.upload)
        self.addAsyncCleanup(self.client.close)

    def upload(self, *, file_path, **kwargs):
        self.started.set()
        if not self.release.wait(5):
            raise TimeoutError("test upload was not released")
        self.payload = Path(file_path).read_bytes()
        if self.failure is not None:
            raise self.failure

    async def owner(self):
        with tempfile.TemporaryDirectory(prefix="upload-cleanup-test-") as directory:
            self.path = Path(directory) / "payload"
            self.path.write_bytes(b"file payload")
            return await self.client.upload_file_to_key(self.path, "original/object")

    async def start_owner(self):
        task = asyncio.create_task(self.owner())
        for _ in range(100):
            if self.started.is_set():
                return task
            await asyncio.sleep(0.01)
        self.release.set()
        await task
        self.fail("SDK thread did not start")

    async def test_repeated_cancel_keeps_file_until_sdk_finishes(self):
        task = await self.start_owner()
        try:
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0.01)
                self.assertFalse(task.done())
                self.assertTrue(self.path.is_file())
            self.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(self.payload, b"file payload")
            self.assertFalse(self.path.exists())
        finally:
            self.release.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_upload_error_after_cancel_is_observed_and_file_is_cleaned(self):
        self.failure = OSError("object store unavailable")
        task = await self.start_owner()
        try:
            task.cancel()
            await asyncio.sleep(0.01)
            self.assertTrue(self.path.exists())
            with self.assertLogs("app.storage.minio_client", level="ERROR") as logs:
                self.release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertIn("object store unavailable", "\n".join(logs.output))
            self.assertEqual(self.payload, b"file payload")
            self.assertFalse(self.path.exists())
        finally:
            self.release.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_success_preserves_object_key_and_cleans_file(self):
        task = await self.start_owner()
        self.release.set()
        self.assertEqual(await task, "original/object")
        self.assertEqual(self.payload, b"file payload")
        self.assertFalse(self.path.exists())

    async def test_upload_failure_propagates_and_cleans_file(self):
        self.failure = OSError("object store unavailable")
        task = await self.start_owner()
        self.release.set()
        with self.assertRaisesRegex(OSError, "object store unavailable"):
            await task
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
