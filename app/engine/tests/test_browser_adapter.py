"""Collection browser operations have no deadline and still honor cancellation."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.adapters import browser as module
from app.models.template import RequestConfig


class BrowserAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_and_navigation_disable_playwright_timeouts(self):
        page = SimpleNamespace(
            url="https://fixture.invalid/list", goto=AsyncMock(return_value=SimpleNamespace(status=200)),
            set_extra_http_headers=AsyncMock(), content=AsyncMock(return_value="<html>fixture</html>"),
            close=AsyncMock(),
        )
        context = SimpleNamespace(
            new_page=AsyncMock(return_value=page), set_default_timeout=Mock(),
            set_default_navigation_timeout=Mock(), close=AsyncMock(),
        )
        browser = SimpleNamespace(new_context=AsyncMock(return_value=context), close=AsyncMock())
        playwright = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)), stop=AsyncMock())
        factory = SimpleNamespace(start=AsyncMock(return_value=playwright))
        api = SimpleNamespace(async_playwright=Mock(return_value=factory))
        tunnel = SimpleNamespace(start=AsyncMock(return_value="socks5://127.0.0.1:9999"), close=AsyncMock())
        client = SimpleNamespace(_select_proxy=AsyncMock(return_value=("socks5h://front:1080", None)))
        adapter = module.BrowserSiteAdapter("https://fixture.invalid", client)
        self.addAsyncCleanup(adapter.close)
        with patch.dict("sys.modules", {"playwright.async_api": api}), patch.object(
            module, "ProxyTunnel", return_value=tunnel,
        ) as tunnel_type, patch.object(
            module, "browser_launch_options", return_value={"timeout": 1},
        ), patch.object(module, "check_control_state", AsyncMock()):
            result = await adapter.request_list_page(page.url, RequestConfig())
        self.assertEqual(result, "<html>fixture</html>")
        tunnel_type.assert_called_once_with("socks5h://front:1080", None, timeout=None)
        playwright.chromium.launch.assert_awaited_once_with(timeout=0)
        context.set_default_timeout.assert_called_once_with(0)
        context.set_default_navigation_timeout.assert_called_once_with(0)
        self.assertEqual(page.goto.await_args.kwargs["timeout"], 0)
        page.close.assert_awaited_once()

    async def test_selector_without_deadline_is_cancellable(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def wait_for_selector(selector, **kwargs):
            self.assertEqual(kwargs["timeout"], 0)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        adapter = module.BrowserSiteAdapter("https://fixture.invalid", Mock())
        page = SimpleNamespace(wait_for_selector=wait_for_selector)
        with patch.object(module, "check_control_state", AsyncMock()), patch.object(
            module.settings, "http_request_timeout", 0.001,
        ):
            task = asyncio.create_task(adapter.wait_for_selector(page, "#results"))
            await asyncio.wait_for(started.wait(), 1)
            ready, _ = await asyncio.wait({task}, timeout=0.02)
            self.assertFalse(ready)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(cancelled.is_set())
