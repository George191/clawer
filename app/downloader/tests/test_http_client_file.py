"""Disk downloads preserve range validation without retaining complete payloads."""

import asyncio
import errno
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import tempfile
from threading import Event, Thread
import time
import tracemalloc
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.base.http import DownloadError, FileDownloadResponse
from app.downloader import http_client as module
from app.downloader.tests import test_http_client_resume as fixtures
from app.downloader.tests.test_http_client_resume import ETAG, Response, TransferError, URL


class FileDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.DownloadResumeTests.asyncSetUp(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "download.part"
        owner = self

        async def make_client(*args, **kwargs):
            class Session:
                def __init__(self):
                    self.close = AsyncMock()
                    self.cookies = {}

                async def request(self, **kwargs):
                    owner.requests.append(kwargs)
                    self.assert_callback(kwargs)
                    outcome = owner.outcomes.pop(0)
                    if isinstance(outcome, BaseException):
                        raise outcome
                    try:
                        async for chunk in outcome.aiter_content():
                            if kwargs["content_callback"](chunk) != len(chunk):
                                raise TransferError()
                    except TransferError as error:
                        error.response = outcome
                        raise
                    return outcome

                def assert_callback(self, kwargs):
                    owner.assertFalse(kwargs["stream"])
                    owner.assertTrue(callable(kwargs["content_callback"]))

            session = Session()
            owner.sessions.append(session)
            return session

        self.http._create_client = make_client

    async def fetch(self):
        return await self.http.download_to_file(URL, self.path, use_proxy=False)

    async def test_complete_transfer_returns_metadata_and_bounded_prefix(self):
        self.outcomes[:] = [Response([b"abc", b"def"], headers={
            "Content-Length": "6", "Content-Type": "application/pdf; charset=binary",
        })]
        result = await self.fetch()
        self.assertIsInstance(result, FileDownloadResponse)
        self.assertEqual((result.path, result.size, result.status_code), (self.path, 6, 200))
        self.assertEqual(result.content_type, "application/pdf")
        self.assertEqual(self.path.read_bytes(), b"abcdef")
        self.assertEqual(result.read_prefix(2), b"ab")
        self.assertEqual(result.read_prefix(0), b"")
        self.assertFalse(hasattr(result, "data"))
        with self.assertRaises(ValueError):
            result.read_prefix(-1)

    async def test_interrupted_transfer_appends_only_validated_range(self):
        first = Response([b"abc"], headers={"Content-Length": "6", "ETag": ETAG},
                         error=TransferError())
        second = Response([b"def"], status=206, headers={
            "Content-Length": "3", "Content-Range": "bytes 3-5/6", "ETag": ETAG,
        })
        self.outcomes[:] = [first, second]
        result = await self.fetch()
        self.assertEqual(self.path.read_bytes(), b"abcdef")
        self.assertEqual(result.size, 6)
        self.assertEqual(self.requests[1]["headers"]["Range"], "bytes=3-")
        self.assertEqual(self.requests[1]["headers"]["If-Range"], ETAG)
        self.assertEqual(result.headers["content-length"], "6")
        self.sessions[0].close.assert_awaited_once()

    async def test_status_zero_retries_original_transport_error_before_first_response(self):
        for code in (5, 35, 56):
            with self.subTest(curl_code=code):
                self.path = self.path.with_name(f"initial-{code}.part")
                failure = TransferError(status=0)
                failure.code = code
                failure.response.raise_for_status = Mock(side_effect=DownloadError(URL, 0, "OK"))
                self.requests.clear()
                self.outcomes[:] = [
                    failure,
                    Response([b"whole"], headers={"Content-Length": "5", "ETag": ETAG}),
                ]
                result = await self.fetch()
                self.assertEqual((result.size, self.path.read_bytes()), (5, b"whole"))
                self.assertEqual(len(self.requests), 2)
                self.assertTrue(all("Range" not in request["headers"] for request in self.requests))
                failure.response.raise_for_status.assert_not_called()

    async def test_status_zero_preserves_validated_partial_and_resume_headers(self):
        for code in (5, 35, 56):
            with self.subTest(curl_code=code):
                self.path = self.path.with_name(f"partial-{code}.part")
                failure = TransferError(status=0)
                failure.code = code
                failure.response.raise_for_status = Mock(side_effect=DownloadError(URL, 0, "OK"))
                self.requests.clear()
                self.outcomes[:] = [
                    Response([b"abc"], headers={"Content-Length": "6", "ETag": ETAG},
                             error=TransferError()),
                    failure,
                    Response([b"def"], status=206, headers={
                        "Content-Length": "3", "Content-Range": "bytes 3-5/6", "ETag": ETAG,
                    }),
                ]
                result = await self.fetch()
                self.assertEqual((result.size, self.path.read_bytes()), (6, b"abcdef"))
                self.assertEqual(result.headers["content-length"], "6")
                self.assertEqual(len(self.requests), 3)
                for request in self.requests[1:]:
                    self.assertEqual(request["headers"]["Range"], "bytes=3-")
                    self.assertEqual(request["headers"]["If-Range"], ETAG)
                self.assertEqual(self.requests[1]["headers"], self.requests[2]["headers"])
                failure.response.raise_for_status.assert_not_called()

    async def test_ignored_range_truncates_old_partial_before_restart(self):
        self.outcomes[:] = [
            Response([b"old-partial"], headers={"Content-Length": "20", "ETag": ETAG},
                     error=TransferError()),
            Response([b"new"], headers={"Content-Length": "3", "ETag": '"v2"'}),
        ]
        result = await self.fetch()
        self.assertEqual((result.size, self.path.read_bytes()), (3, b"new"))

    async def test_invalid_range_removes_partial_after_transport_cleanup(self):
        first = Response([b"abc"], headers={"Content-Length": "6", "ETag": ETAG},
                         error=TransferError())
        invalid = Response([b"def"], status=206, headers={
            "Content-Range": "bytes 4-6/7", "ETag": ETAG,
        })
        self.outcomes[:] = [first, invalid]
        with self.assertRaises(module.ResumeProtocolError):
            await self.fetch()
        self.assertFalse(self.path.exists())
        self.sessions[-1].close.assert_awaited_once()
        self.assertEqual(self.http._download_operations, {})
        self.assertEqual(self.http._download_owners, {})

    async def test_416_restarts_new_complete_file(self):
        self.outcomes[:] = [
            Response([b"abc"], headers={"Content-Length": "6", "ETag": ETAG},
                     error=TransferError()),
            Response(status=416),
            Response([b"whole"], headers={"Content-Length": "5", "ETag": '"v2"'}),
        ]
        result = await self.fetch()
        self.assertEqual((result.size, self.path.read_bytes()), (5, b"whole"))
        self.assertNotIn("Range", self.requests[-1]["headers"])

    async def test_failure_and_cancellation_remove_owned_file(self):
        self.outcomes[:] = [Response(status=503)]
        with self.assertRaises(DownloadError):
            await self.fetch()
        self.assertFalse(self.path.exists())

        started, released = asyncio.Event(), asyncio.Event()
        response = Response()

        async def blocked():
            yield b"partial"
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                released.set()

        response.aiter_content = blocked
        self.outcomes[:] = [response]
        task = asyncio.create_task(self.fetch())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(released.is_set())
        self.sessions[-1].close.assert_awaited_once()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.http._download_owners, {})

    async def test_existing_file_is_neither_truncated_nor_removed(self):
        self.path.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            await self.fetch()
        self.assertEqual(self.path.read_bytes(), b"keep")
        self.assertEqual(self.requests, [])

    async def test_nonstream_transport_fails_before_creating_file(self):
        with patch.object(module, "_STREAM_REQUEST_KWARGS", {}), self.assertRaisesRegex(
            DownloadError, "streaming transport",
        ):
            await self.fetch()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.requests, [])

    async def test_disk_full_is_explicit_and_does_not_retry_transfer(self):
        failure = OSError(errno.ENOSPC, "fixture disk full")
        segment = Mock()
        segment.write.side_effect = failure
        self.outcomes[:] = [Response([b"abc"], headers={"Content-Length": "3"})]
        with patch.object(module, "TemporaryFile", return_value=segment), self.assertRaises(OSError) as raised:
            await self.fetch()
        self.assertIs(raised.exception, failure)
        self.assertEqual(len(self.requests), 1)
        segment.close.assert_called_once_with()
        self.assertFalse(self.path.exists())

    async def test_disk_creation_and_write_failures_do_not_penalize_proxy(self):
        for operation in ("create", "write"):
            with self.subTest(operation=operation):
                pool = SimpleNamespace(mark_failure=AsyncMock(), mark_success=AsyncMock())
                segment = Mock()
                failure = OSError(errno.ENOSPC, "fixture disk full")
                segment.write.side_effect = failure
                self.outcomes[:] = [Response([b"abc"], headers={"Content-Length": "3"})]
                temporary = (patch.object(module, "TemporaryFile", side_effect=failure) if operation == "create"
                             else patch.object(module, "TemporaryFile", return_value=segment))
                with temporary, patch.object(module, "_proxy_pool", pool), \
                     patch.object(self.http, "_release_failed_proxy", AsyncMock()) as release, \
                     self.assertRaises(OSError) as raised:
                    await self.http.download_to_file(URL, self.path, use_proxy=True)
                self.assertIs(raised.exception, failure)
                pool.mark_failure.assert_not_awaited()
                release.assert_not_awaited()
                self.assertFalse(self.path.exists())

    async def test_network_failure_still_penalizes_proxy_before_validated_resume(self):
        pool = SimpleNamespace(mark_failure=AsyncMock(), mark_success=AsyncMock())
        self.outcomes[:] = [
            Response([b"abc"], headers={"Content-Length": "6", "ETag": ETAG}, error=TransferError()),
            Response([b"def"], status=206, headers={"Content-Range": "bytes 3-5/6", "ETag": ETAG}),
        ]
        with patch.object(module, "_proxy_pool", pool), \
             patch.object(self.http, "_release_failed_proxy", AsyncMock()) as release:
            await self.http.download_to_file(URL, self.path, use_proxy=True)
        pool.mark_failure.assert_awaited_once_with("socks5://fixture")
        release.assert_awaited_once()
        self.assertEqual(self.path.read_bytes(), b"abcdef")


class CurlFileDownloadTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(module._STREAM_REQUEST_KWARGS, "curl streaming is unavailable")
    async def test_generated_64_mib_resumes_on_disk_with_bounded_python_memory(self):
        block = bytes(range(256)) * 256
        size = 64 * 1024 * 1024
        cut = size // 2
        requests = []
        cancel_started, cancel_release = Event(), Event()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                if self.path == "/cancel":
                    self.send_response(200)
                    self.send_header("Content-Length", str(size))
                    self.end_headers()
                    self.wfile.write(block)
                    self.wfile.flush()
                    cancel_started.set()
                    cancel_release.wait(2)
                    return
                requests.append(dict(self.headers))
                start = 0 if len(requests) == 1 else cut
                end = cut if len(requests) == 1 else size
                self.send_response(200 if not start else 206)
                self.send_header("Content-Length", str(size - start))
                self.send_header("ETag", ETAG)
                self.send_header("Connection", "close")
                if start:
                    self.send_header("Content-Range", f"bytes {cut}-{size - 1}/{size}")
                self.end_headers()
                for _ in range(start, end, len(block)):
                    self.wfile.write(block)
                self.wfile.flush()
                if not start:
                    self.connection.shutdown(socket.SHUT_WR)
                    self.close_connection = True

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = module.HttpClient()
        client._select_proxy = AsyncMock(return_value=(None, None))
        patches = (
            patch.object(module, "_init_anti_crawl", lambda **kwargs: None),
            patch.object(module, "_rotator", None), patch.object(module, "_delayer", None),
            patch.object(module, "_proxy_pool", None),
            patch.object(module.settings, "http_interface", ""),
        )
        for item in patches:
            item.start()

        def slow_segment(*args, **kwargs):
            source = tempfile.TemporaryFile(*args, **kwargs)

            class SlowFile:
                def __getattr__(self, name):
                    return getattr(source, name)

                def write(self, chunk):
                    time.sleep(0.0001)
                    return source.write(chunk)

            return SlowFile()

        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "archive.part"
                tracemalloc.start()
                try:
                    with patch.object(module, "TemporaryFile", side_effect=slow_segment):
                        result = await client.download_to_file(
                            f"http://127.0.0.1:{server.server_port}/archive", path, use_proxy=False,
                        )
                    _, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                self.assertEqual(result.size, size)
                self.assertEqual(path.stat().st_size, size)
                expected = hashlib.sha256()
                for _ in range(0, size, len(block)):
                    expected.update(block)
                actual = hashlib.sha256()
                with path.open("rb") as source:
                    while chunk := source.read(1 << 20):
                        actual.update(chunk)
                self.assertEqual(actual.hexdigest(), expected.hexdigest())
                self.assertEqual(len(requests), 2)
                self.assertEqual(requests[1]["Range"], f"bytes={cut}-")
                self.assertEqual(requests[1]["If-Range"], ETAG)
                self.assertLess(peak, 8 * 1024 * 1024, f"Python peak memory was {peak:,} bytes")
                cancelled_path = Path(directory) / "cancelled.part"
                task = asyncio.create_task(client.download_to_file(
                    f"http://127.0.0.1:{server.server_port}/cancel", cancelled_path, use_proxy=False,
                ))
                self.assertTrue(await asyncio.to_thread(cancel_started.wait, 1))
                await asyncio.sleep(0.05)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
                self.assertFalse(cancelled_path.exists())
                self.assertEqual(client._download_owners, {})
                self.assertEqual(client._download_operations, {})
                self.assertEqual(list(Path(directory).iterdir()), [path])
        finally:
            cancel_release.set()
            await client.close()
            for item in reversed(patches):
                item.stop()
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
