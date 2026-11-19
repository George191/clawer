"""SDK proxy selection must obey each crawl's template mode."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from curl_cffi.requests.exceptions import ProxyError

from app.downloader import http_client as module
from app.utils.runtime_control import reset_crawl_proxy_mode, set_crawl_proxy_mode


FRONT = "socks5h://front.invalid:1080"
EXIT = "http://192.0.2.1:8080"


class TemplateProxyRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = SimpleNamespace(
            enabled=True, lease_proxy=AsyncMock(return_value=EXIT), release_proxy=AsyncMock(),
        )
        self.initialize = Mock()
        patches = (
            patch.object(module.settings, "proxy_pre_proxy_url", FRONT),
            patch.object(module.settings, "tunnel_proxy_url", ""),
            patch.object(module, "_proxy_pool", self.pool),
            patch.object(module, "_init_anti_crawl", self.initialize),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.client = module.HttpClient()
        self.addAsyncCleanup(self.client.close)

    async def test_disabled_template_overrides_sdk_request_without_acquiring_exit(self):
        token = set_crawl_proxy_mode(False)
        try:
            route = await self.client._select_proxy(True, adapter_name="fixture")
        finally:
            reset_crawl_proxy_mode(token)
        self.assertEqual(route, (FRONT, None))
        self.initialize.assert_not_called()
        self.pool.lease_proxy.assert_not_awaited()

    async def test_enabled_template_overrides_sdk_front_only_request(self):
        token = set_crawl_proxy_mode(True)
        try:
            route = await self.client._select_proxy(False, adapter_name="fixture")
        finally:
            reset_crawl_proxy_mode(token)
        self.assertEqual(route, (EXIT, FRONT))
        self.pool.lease_proxy.assert_awaited_once_with(
            id(asyncio.current_task()), "fixture", exclude_proxy_url=None,
        )

    async def test_enabled_template_with_empty_pool_fails_without_front_only_fallback(self):
        self.pool.lease_proxy.return_value = None
        token = set_crawl_proxy_mode(True)
        try:
            with (
                patch.object(module, "controlled_sleep", new_callable=AsyncMock) as wait,
                self.assertRaisesRegex(ProxyError, "available exit proxy"),
            ):
                await self.client._select_proxy(False, adapter_name="fixture")
            wait.assert_awaited_once_with(60)
        finally:
            reset_crawl_proxy_mode(token)
        self.pool.lease_proxy.assert_awaited_once()
        self.assertEqual(self.client._leased_proxies, {})

    async def test_waiting_for_an_exit_does_not_hold_the_client_lease_lock(self):
        sleeping = asyncio.Event()
        release = asyncio.Event()

        async def backoff(_seconds):
            sleeping.set()
            await release.wait()

        self.pool.lease_proxy.side_effect = [None, EXIT]
        with patch.object(module, "controlled_sleep", backoff):
            first = asyncio.create_task(self.client._select_proxy(True))
            try:
                await asyncio.wait_for(sleeping.wait(), 1)
                route = await asyncio.wait_for(self.client._select_proxy(True), 1)
                self.assertEqual(route, (EXIT, FRONT))
            finally:
                release.set()
                with self.assertRaisesRegex(ProxyError, "available exit proxy"):
                    await first

    async def test_enabled_template_requires_a_configured_pool(self):
        self.pool.enabled = False
        token = set_crawl_proxy_mode(True)
        try:
            with self.assertRaisesRegex(ProxyError, "configured exit proxy pool"):
                await self.client._select_proxy(False, adapter_name="fixture")
        finally:
            reset_crawl_proxy_mode(token)
        self.pool.lease_proxy.assert_not_awaited()

    async def test_concurrent_templates_keep_independent_modes_on_shared_client(self):
        ready = asyncio.Event()
        waiting = 0

        async def select(mode):
            nonlocal waiting
            token = set_crawl_proxy_mode(mode)
            try:
                waiting += 1
                if waiting == 2:
                    ready.set()
                await ready.wait()
                return await self.client._select_proxy(not mode, adapter_name="fixture")
            finally:
                reset_crawl_proxy_mode(token)

        disabled, enabled = await asyncio.gather(select(False), select(True))
        self.assertEqual(disabled, (FRONT, None))
        self.assertEqual(enabled, (EXIT, FRONT))
        self.pool.lease_proxy.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
