"""Request retries require confirmed cleanup, without a wall-clock deadline."""

import asyncio
from contextlib import suppress
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.base.http import DownloadCleanupError
from app.downloader import http_client as module
from app.utils.runtime_control import reset_control_checkpoint, set_control_checkpoint


URL = 'https://fixture.invalid/catalog'


class HttpCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http = module.HttpClient()
        response = SimpleNamespace(status_code=200, text='valid page', raise_for_status=lambda: None)
        self.session = SimpleNamespace(request=AsyncMock(return_value=response), close=AsyncMock())
        self.http._select_proxy = AsyncMock(return_value=('socks5://fixture', None))
        self.http._create_client = AsyncMock(return_value=self.session)
        self.http._before_request = AsyncMock(return_value='fixture')
        self.http._after_request = AsyncMock()
        self.addAsyncCleanup(self.http.close)

    async def fetch(self):
        return await self.http.request_page(URL, anti_crawl_enabled=False)

    async def test_page_waits_for_cleanup_after_event_loop_stall(self):
        closed = asyncio.Event()

        async def close():
            started = asyncio.Event()

            async def checker():
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)

            background = asyncio.create_task(checker())
            await started.wait()
            asyncio.get_running_loop().call_soon(time.sleep, 0.06)
            background.cancel()
            with suppress(asyncio.CancelledError):
                await background
            closed.set()

        self.session.close.side_effect = close
        # Scale the former five-second cleanup budget to reproduce a suspended loop.
        with patch.object(module, '_DOWNLOAD_CLEANUP_SECONDS', 0.03, create=True):
            self.assertEqual(await self.fetch(), 'valid page')
        self.assertTrue(closed.is_set())
        self.assertNotIn(asyncio.current_task(), self.http._download_cleanup_blocked)
        self.assertEqual(self.http._download_pending_tasks, set())

    async def test_real_cleanup_failure_preserves_cause_and_blocks_next_request(self):
        failure = RuntimeError('fixture close failed')
        self.session.close.side_effect = failure
        with self.assertRaises(DownloadCleanupError) as raised:
            await self.fetch()
        self.assertIs(raised.exception.__cause__, failure)
        self.assertIn(asyncio.current_task(), self.http._download_cleanup_blocked)
        with self.assertRaisesRegex(DownloadCleanupError, 'Previous HTTP cleanup unconfirmed'):
            await self.fetch()
        self.session.request.assert_awaited_once()
        self.http._create_client.assert_awaited_once()

    async def test_stalled_cleanup_exits_and_blocks_a_new_request(self):
        async def stalled():
            await asyncio.Event().wait()

        cleanup = asyncio.create_task(stalled())
        with self.assertRaises(DownloadCleanupError):
            await self.http._finish_download_tasks(
                0, {cleanup}, cleanup_tasks={cleanup}, timeout=0.01,
            )
        self.assertIn(asyncio.current_task(), self.http._download_cleanup_blocked)
        with self.assertRaises(DownloadCleanupError):
            await self.fetch()
        await asyncio.gather(cleanup, return_exceptions=True)
        self.session.request.assert_not_awaited()

    async def test_control_cancellation_registers_and_isolates_unfinished_cleanup(self):
        started, cancelled, release, closed = (asyncio.Event() for _ in range(4))
        stop = asyncio.Event()

        async def close():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                closed.set()

        async def checkpoint():
            return not stop.is_set()

        self.session.close.side_effect = close
        token = set_control_checkpoint(checkpoint)
        owner = asyncio.create_task(self.fetch())
        try:
            await asyncio.wait_for(started.wait(), 1)
            stop.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(owner, 2)
            await asyncio.wait_for(cancelled.wait(), 1)
            self.assertIn(owner, self.http._download_cleanup_blocked)
            self.assertTrue(self.http._download_pending_tasks)
            self.assertFalse(closed.is_set())
            self.session.request.assert_awaited_once()
        finally:
            reset_control_checkpoint(token)
            release.set()
            owner.cancel()
            await asyncio.gather(owner, *self.http._download_pending_tasks, return_exceptions=True)
        self.assertTrue(closed.is_set())


if __name__ == '__main__':
    unittest.main()
