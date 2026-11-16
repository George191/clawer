"""Interrupted transfers resume only when response identity and ranges agree."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.base.http import DownloadCleanupError, DownloadError
from app.downloader import http_client as module
from app.downloader.resume import DownloadTruncatedError, ResumeProtocolError, ResumeState
from app.models.template import RequestConfig
from app.utils.runtime_control import reset_control_checkpoint, set_control_checkpoint


URL = "https://fixture.invalid/archive?token=private"
ETAG = '"fixture-v1"'


class TransferError(ConnectionError):
    code = 56

    def __init__(self, status=200):
        self.response = SimpleNamespace(status_code=status)
        super().__init__("fixture transfer interrupted")


class Response:
    def __init__(self, chunks=(), *, status=200, headers=None, url=URL, error=None, content=b""):
        self.status_code, self.headers, self.url = status, headers or {}, url
        self.chunks, self.error, self.content = chunks, error, content
        self.aclose = AsyncMock()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise DownloadError(URL, self.status_code, "fixture rejection")

    async def aiter_content(self):
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error


class DownloadResumeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http = module.HttpClient()
        self.outcomes, self.requests, self.sessions = [], [], []
        self.http._select_proxy = AsyncMock(return_value=("socks5://fixture", None))
        owner = self

        async def make_client(*args, **kwargs):
            class Session:
                def __init__(self):
                    self.close = AsyncMock()
                    self.cookies = {}
                async def request(self, **kwargs):
                    owner.requests.append(kwargs)
                    outcome = owner.outcomes.pop(0)
                    if isinstance(outcome, BaseException):
                        raise outcome
                    if callable(outcome):
                        return await outcome()
                    return outcome
            session = Session()
            owner.sessions.append(session)
            return session

        self.http._create_client = make_client
        patches = (
            patch.object(module, "_init_anti_crawl", lambda **kwargs: None),
            patch.object(module, "_rotator", None),
            patch.object(module, "_delayer", None),
            patch.object(module, "_proxy_pool", None),
            patch.object(module, "_STREAM_REQUEST_KWARGS", {"stream": True}),
            patch.object(module, "_DOWNLOAD_CLEANUP_SECONDS", 0.03),
            patch.object(module.settings, "http_retry_backoff", 0),
            patch.object(module.settings, "http_download_timeout", 1),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.addAsyncCleanup(self.http.close)

    async def fetch(self, **kwargs):
        return await self.http.download_response(URL, use_proxy=False, **kwargs)

    def interrupted(self, *, headers=None):
        return Response([b"abc"], headers=headers or {"Content-Length": "6", "ETag": ETAG}, error=TransferError())

    def resumed(self, **kwargs):
        values = {"Content-Length": "3", "Content-Range": "bytes 3-5/6", "ETag": ETAG}
        values.update(kwargs.pop("headers", {}))
        return Response([b"def"], status=206, headers=values, **kwargs)

    async def test_no_callback_streams_and_resumes_same_object(self):
        first, second = self.interrupted(), self.resumed()
        self.outcomes[:] = [first, second]
        config = RequestConfig(headers={"If-None-Match": ETAG, "If-Modified-Since": "old"})
        result = await self.fetch(config=config)
        self.assertEqual(result.data, b"abcdef")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["content-length"], "6")
        self.assertNotIn("content-range", result.headers)
        headers = self.requests[1]["headers"]
        self.assertEqual(headers["Range"], "bytes=3-")
        self.assertEqual(headers["If-Range"], ETAG)
        self.assertEqual(headers["Accept-Encoding"], "identity")
        self.assertNotIn("If-None-Match", headers)
        self.assertNotIn("If-Modified-Since", headers)
        self.assertTrue(all(request["stream"] for request in self.requests))
        first.aclose.assert_awaited_once()
        self.sessions[0].close.assert_awaited_once()

    async def test_callback_once_after_complete_transfer_and_cleanup(self):
        self.outcomes[:] = [self.interrupted(), self.resumed()]
        callback = Mock()
        result = await self.fetch(chunk_callback=callback)
        callback.assert_called_once_with(result.data)

    async def test_server_ignores_range_restarts_instead_of_appending(self):
        self.outcomes[:] = [self.interrupted(), Response([b"new-file"], headers={"Content-Length": "8", "ETag": '"v2"'})]
        self.assertEqual((await self.fetch()).data, b"new-file")

    async def test_missing_weak_or_encoded_validator_restarts_whole_file(self):
        for headers in ({"Content-Length": "6"}, {"Content-Length": "6", "ETag": 'W/"v1"'},
                        {"Content-Length": "6", "ETag": ETAG, "Content-Encoding": "gzip"}):
            with self.subTest(headers=headers):
                self.outcomes[:] = [self.interrupted(headers=headers), Response([b"whole"], headers={"Content-Length": "5"})]
                result = await self.fetch()
                self.assertEqual(result.data, b"whole")
                self.assertNotIn("Range", self.requests[-1]["headers"])

    async def test_safe_last_modified_if_range(self):
        modified = "Wed, 21 Oct 2015 07:28:00 GMT"
        headers = {"Content-Length": "6", "Last-Modified": modified, "Date": "Wed, 21 Oct 2015 07:30:00 GMT"}
        resumed = self.resumed(headers={"Last-Modified": modified})
        self.outcomes[:] = [self.interrupted(headers=headers), resumed]
        self.assertEqual((await self.fetch()).data, b"abcdef")
        self.assertEqual(self.requests[-1]["headers"]["If-Range"], modified)

    async def test_short_clean_body_resumes(self):
        first = self.interrupted()
        first.error = None
        self.outcomes[:] = [first, self.resumed()]
        self.assertEqual((await self.fetch()).data, b"abcdef")

    async def test_invalid_ranges_validators_url_or_encoding_never_append(self):
        cases = [self.resumed(headers={"Content-Range": "bytes 2-4/6"}),
                 self.resumed(headers={"Content-Range": "bytes 3-5/7"}),
                 self.resumed(headers={"Content-Length": "99"}),
                 self.resumed(headers={"ETag": '"different"'}),
                 self.resumed(headers={"Content-Encoding": "gzip"}),
                 self.resumed(url="https://fixture.invalid/other")]
        for response in cases:
            with self.subTest(headers=response.headers, url=response.url):
                self.outcomes[:] = [self.interrupted(), response]
                before = len(self.requests)
                callback = Mock()
                with self.assertRaises(ResumeProtocolError):
                    await self.fetch(chunk_callback=callback)
                self.assertEqual(len(self.requests) - before, 2)
                callback.assert_not_called()

    async def test_416_discards_checkpoint_then_restarts_full_download(self):
        self.outcomes[:] = [self.interrupted(), Response(status=416), Response([b"whole"], headers={"Content-Length": "5"})]
        self.assertEqual((await self.fetch()).data, b"whole")
        self.assertNotIn("Range", self.requests[-1]["headers"])

    async def test_repeated_transfer_failure_is_bounded(self):
        self.outcomes[:] = [TransferError() for _ in range(5)]
        with self.assertRaises(TransferError):
            await self.fetch()
        self.assertEqual(len(self.requests), 4)

    async def test_rejections_are_not_transfer_retries(self):
        for status in (401, 403, 404, 429, 503):
            with self.subTest(status=status):
                self.outcomes[:] = [Response(status=status)]
                before = len(self.requests)
                with self.assertRaises(DownloadError):
                    await self.fetch()
                self.assertEqual(len(self.requests) - before, 1)

    async def test_non_get_transfer_failure_is_not_replayed(self):
        self.outcomes[:] = [TransferError()]
        with self.assertRaises(TransferError):
            await self.fetch(config=RequestConfig(method="POST"))
        self.assertEqual(len(self.requests), 1)

    async def test_explicit_range_keeps_caller_partial_response_contract(self):
        self.outcomes[:] = [Response([b"abc"], status=206, headers={"Content-Length": "3", "Content-Range": "bytes 0-2/6"})]
        result = await self.fetch(config=RequestConfig(headers={"Range": "bytes=0-2"}))
        self.assertEqual(result.data, b"abc")
        self.assertEqual(result.status_code, 206)
        self.assertEqual(self.requests[0]["headers"]["Range"], "bytes=0-2")

    async def test_304_keeps_conditional_snapshot_semantics(self):
        self.outcomes[:] = [Response(status=304, headers={"Content-Length": "999", "ETag": ETAG})]
        result = await self.fetch(config=RequestConfig(headers={"If-None-Match": ETAG}))
        self.assertEqual(result.status_code, 304)
        self.assertEqual(result.data, b"")

    async def test_response_cleanup_failure_stops_without_retry_or_callback(self):
        first = Response([b"abcdef"], headers={"Content-Length": "6", "ETag": ETAG})
        first.aclose.side_effect = RuntimeError("fixture cleanup failed")
        self.outcomes[:] = [first, self.resumed()]
        callback = Mock()
        with self.assertRaises(DownloadCleanupError):
            await self.fetch(chunk_callback=callback)
        self.assertEqual(len(self.requests), 1)
        callback.assert_not_called()

    async def test_304_and_204_cannot_accept_an_incomplete_resumed_object(self):
        for status in (304, 204):
            with self.subTest(status=status):
                self.outcomes[:] = [self.interrupted(), Response(status=status)]
                callback = Mock()
                with self.assertRaises(ResumeProtocolError):
                    await self.fetch(chunk_callback=callback)
                callback.assert_not_called()

    async def test_cancelled_read_cleans_up_and_does_not_retry(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()
        first = self.interrupted()

        async def stalled():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            yield b"unreachable"

        first.aiter_content = stalled
        self.outcomes[:] = [first]
        with patch.object(module.settings, "http_download_timeout", 0.01), \
                patch.object(module, "_STREAM_CHUNK_IDLE_SECONDS", 0.01):
            task = asyncio.create_task(self.fetch())
            await started.wait()
            await asyncio.sleep(0.05)
            self.assertFalse(task.done())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(cancelled.is_set())
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.http._download_operations, {})

    async def test_close_during_retry_backoff_cancels_outer_download(self):
        backoff_started = asyncio.Event()

        async def blocked_backoff(*args):
            backoff_started.set()
            await asyncio.Event().wait()

        self.outcomes[:] = [TransferError(), self.resumed()]
        with patch.object(module.asyncio, "sleep", blocked_backoff):
            task = asyncio.create_task(self.fetch())
            await backoff_started.wait()
            self.assertIs(self.http._download_owners[id(task)], task)
            await self.http.close()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.http._download_owners, {})

    async def test_head_content_length_describes_object_not_response_body(self):
        for transport in ({"stream": True}, {}):
            with self.subTest(streaming=bool(transport)):
                self.outcomes[:] = [Response(headers={"Content-Length": "999", "ETag": ETAG})]
                with patch.object(module, "_STREAM_REQUEST_KWARGS", transport):
                    result = await self.fetch(config=RequestConfig(method="HEAD"))
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.headers["content-length"], "999")
                self.assertEqual(result.data, b"")

    async def test_retry_backoff_does_not_consume_download_budget(self):
        self.outcomes[:] = [TransferError() for _ in range(5)]
        with patch.object(module.settings, "http_retry_backoff", 2), \
                patch.object(module.asyncio, "sleep", AsyncMock()) as sleep:
            with self.assertRaises(TransferError):
                await self.fetch()
        self.assertEqual(len(self.requests), 4)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [2, 4, 8])

    async def test_slow_response_headers_have_no_download_deadline(self):
        for transport in ({"stream": True}, {}):
            with self.subTest(streaming=bool(transport)):
                started, release = asyncio.Event(), asyncio.Event()

                async def delayed_response():
                    started.set()
                    await release.wait()
                    return Response([b"whole"], content=b"whole", headers={"Content-Length": "5"})

                self.outcomes[:] = [delayed_response]
                with patch.object(module.settings, "http_download_timeout", 0.01), \
                        patch.object(module, "_STREAM_REQUEST_KWARGS", transport):
                    task = asyncio.create_task(self.fetch())
                    try:
                        await started.wait()
                        await asyncio.sleep(0.05)
                        self.assertFalse(task.done())
                        release.set()
                        self.assertEqual((await asyncio.wait_for(task, 1)).data, b"whole")
                    finally:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                self.assertIsNone(self.requests[-1]["timeout"])

    async def test_client_sessions_disable_timeout_for_both_flag_values(self):
        with patch.object(module, "_ResponseHeaderSession") as session:
            for no_timeout in (False, True):
                await module.HttpClient._create_client(self.http, no_timeout=no_timeout)
                self.assertIsNone(session.call_args.kwargs["timeout"])

    async def test_legacy_watchdog_reports_stalls_without_terminating_transfer(self):
        state = {"t": asyncio.get_running_loop().time() - 301, "bytes": 0}
        with patch.object(module.asyncio, "sleep", AsyncMock(side_effect=[None, None, None, asyncio.CancelledError])), \
                patch.object(module.logger, "warning") as warning:
            with self.assertRaises(asyncio.CancelledError):
                await module._stream_download_watchdog(state, 300, URL)
        self.assertEqual(warning.call_count, 2)

    async def test_slow_pages_have_no_deadline_with_or_without_cached_session(self):
        self.http._before_request = AsyncMock(return_value="fixture")
        self.http._after_request = AsyncMock()
        for reuse_session in (False, True):
            started, release = asyncio.Event(), asyncio.Event()

            async def delayed_response():
                started.set()
                await release.wait()
                response = Response()
                response.text = "slow page"
                return response

            self.outcomes[:] = [delayed_response]
            with patch.object(module.settings, "http_request_timeout", 0.01):
                task = asyncio.create_task(self.http.request_page(
                    URL, anti_crawl_enabled=False, reuse_session=reuse_session,
                ))
                try:
                    await started.wait()
                    await asyncio.sleep(0.05)
                    self.assertFalse(task.done())
                    release.set()
                    self.assertEqual(await asyncio.wait_for(task, 1), "slow page")
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_cancelled_page_closes_cached_or_dedicated_session(self):
        self.http._before_request = AsyncMock(return_value="fixture")
        for reuse_session in (False, True):
            started = asyncio.Event()

            async def blocked_response():
                started.set()
                await asyncio.Event().wait()

            self.outcomes[:] = [blocked_response]
            task = asyncio.create_task(self.http.request_page(
                URL, anti_crawl_enabled=False, reuse_session=reuse_session,
            ))
            await started.wait()
            session = self.sessions[-1]
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            session.close.assert_awaited_once()
            self.assertEqual(self.http._download_operations, {})
            self.assertEqual(self.http._download_owners, {})

    async def test_page_observes_control_checkpoint_cancellation(self):
        started, cancelled = asyncio.Event(), asyncio.Event()
        self.http._before_request = AsyncMock(return_value="fixture")

        async def blocked_response():
            started.set()
            await asyncio.Event().wait()

        async def checkpoint():
            return not cancelled.is_set()

        self.outcomes[:] = [blocked_response]
        token = set_control_checkpoint(checkpoint)
        task = asyncio.create_task(self.http.request_page(URL, anti_crawl_enabled=False))
        try:
            await started.wait()
            cancelled.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            self.sessions[-1].close.assert_awaited_once()
            self.assertEqual(self.http._download_operations, {})
            self.assertEqual(self.http._download_owners, {})
        finally:
            reset_control_checkpoint(token)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_download_observes_control_checkpoint_cancellation(self):
        for waiting_for in ("headers", "body"):
            with self.subTest(waiting_for=waiting_for):
                started, cancelled, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

                async def blocked_response():
                    started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cleaned.set()

                async def blocked_chunks():
                    await blocked_response()
                    yield b"unreachable"

                async def checkpoint():
                    return not cancelled.is_set()

                response = Response()
                response.aiter_content = blocked_chunks
                self.outcomes[:] = [blocked_response if waiting_for == "headers" else response]
                token = set_control_checkpoint(checkpoint)
                task = asyncio.create_task(self.fetch())
                try:
                    await started.wait()
                    cancelled.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, 1)
                    self.assertTrue(cleaned.is_set())
                    self.sessions[-1].close.assert_awaited_once()
                    self.assertEqual(self.http._download_operations, {})
                    self.assertEqual(self.http._download_owners, {})
                finally:
                    reset_control_checkpoint(token)
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_legacy_transport_retries_complete_payload(self):
        self.outcomes[:] = [TransferError(), Response(content=b"whole", headers={"Content-Length": "5"})]
        with patch.object(module, "_STREAM_REQUEST_KWARGS", {}):
            self.assertEqual((await self.fetch()).data, b"whole")
        self.assertTrue(all(not request["stream"] for request in self.requests))
        self.assertNotIn("Range", self.requests[-1]["headers"])


class ResumeStateTests(unittest.TestCase):
    def test_validator_requires_strong_etag_or_demonstrably_strong_last_modified(self):
        for headers in ({}, {"ETag": 'W/"v1"'}, {"Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT"},
                        {"ETag": 'W/"v1"', "Last-Modified": "Wed, 21 Oct 2015 07:28:00 GMT", "Date": "Wed, 21 Oct 2015 07:30:00 GMT"}):
            with self.subTest(headers=headers):
                state = ResumeState()
                state.start_response(200, headers, URL)
                state.advance(3)
                self.assertNotIn("Range", state.request_headers())

    def test_unrequested_partial_and_body_overflow_are_rejected(self):
        state = ResumeState()
        with self.assertRaises(ResumeProtocolError):
            state.start_response(206, {"Content-Range": "bytes 0-2/6"}, URL)
        state.start_response(200, {"Content-Length": "3", "ETag": ETAG}, URL)
        with self.assertRaises(ResumeProtocolError):
            state.advance(4)

    def test_short_range_requires_another_segment(self):
        state = ResumeState()
        state.start_response(200, {"Content-Length": "6", "ETag": ETAG}, URL)
        state.advance(3)
        state.start_response(206, {"Content-Range": "bytes 3-4/6", "ETag": ETAG}, URL)
        state.advance(2)
        with self.assertRaises(DownloadTruncatedError):
            state.finish()
        self.assertEqual(state.request_headers()["Range"], "bytes=5-")


class CurlDownloadResumeTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(module._STREAM_REQUEST_KWARGS, "curl streaming is unavailable")
    async def test_real_curl_resumes_interrupted_local_http_body(self):
        body = bytes(range(256)) * 1024
        offset = len(body) // 2
        requests = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                requests.append(dict(self.headers))
                if len(requests) == 1:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("ETag", ETAG)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body[:offset])
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_WR)
                    self.close_connection = True
                else:
                    self.send_response(206)
                    self.send_header("Content-Length", str(len(body) - offset))
                    self.send_header("Content-Range", f"bytes {offset}-{len(body) - 1}/{len(body)}")
                    self.send_header("ETag", ETAG)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body[offset:])

            def log_message(self, *args): pass

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
            patch.object(module.settings, "http_retry_backoff", 0),
            patch.object(module.settings, "http_download_timeout", 5),
        )
        for item in patches: item.start()
        try:
            result = await client.download_response(
                f"http://127.0.0.1:{server.server_port}/archive", use_proxy=False,
            )
            self.assertEqual(result.data, body)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers["content-length"], str(len(body)))
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[1]["Range"], f"bytes={offset}-")
            self.assertEqual(requests[1]["If-Range"], ETAG)
        finally:
            await client.close()
            for item in reversed(patches): item.stop()
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
