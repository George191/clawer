"""An exhausted exit pool waits cooperatively before allowing another attempt."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.downloader import http_client as module


class EmptyPoolControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_pool_backoff_allows_heartbeat_and_cancellation(self):
        attempts = 0

        async def empty_lease(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            return None

        pool = SimpleNamespace(enabled=True, lease_proxy=empty_lease)
        client = module.HttpClient()
        heartbeats = []

        async def retry():
            for _ in range(200):
                try:
                    await client._select_proxy(True, adapter_name="ssc_press")
                except module.ProxyError:
                    pass
            raise AssertionError("Empty-pool retry loop completed before control could run")

        async def control(task):
            for _ in range(3):
                await asyncio.sleep(0)
                heartbeats.append(attempts)
            task.cancel()

        with patch.object(module, "_proxy_pool", pool), patch.object(
            module, "_init_anti_crawl", lambda: None,
        ), patch.object(module.settings, "proxy_pre_proxy_url", "socks5h://fixture:1080"):
            retry_task = asyncio.create_task(retry())
            control_task = asyncio.create_task(control(retry_task))
            await control_task
            with self.assertRaises(asyncio.CancelledError):
                await retry_task
        self.assertEqual(len(heartbeats), 3)
        self.assertEqual(heartbeats, [1, 1, 1])
