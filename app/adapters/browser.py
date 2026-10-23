"""Reusable Playwright transport for adapters that need a rendered browser page."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.adapters import BaseSiteAdapter
from app.base.http import DownloadError
from app.config.settings import settings
from app.downloader.browser_runtime import BrowserRuntimeError, browser_launch_options
from app.models.template import RequestConfig
from app.utils.runtime_control import check_control_state, controlled_sleep


class BrowserSiteAdapter(BaseSiteAdapter):
    """Keep one isolated browser session per crawl; subclasses supply DOM hooks.

    Supports GET navigation, request headers/cookies/params, direct or configured
    tunnel access, and bounded retries. Pool/pre-proxy chains are not browser
    transports. Override prepare_list_page/prepare_detail_page for site actions;
    returning text from a hook replaces the default, complete page HTML.
    """

    browser_wait_selector: str | None = None
    browser_wait_timeout_seconds: float | None = None
    browser_viewport = {"width": 1440, "height": 1000}
    browser_max_attempts = 3

    def __init__(self, base_url, http_client=None, **kwargs):
        super().__init__(base_url, http_client, **kwargs)
        self._browser_lock = asyncio.Lock()
        self._playwright = None
        self._browser = None
        self._browser_context = None
        self._browser_proxy_url = None

    async def _controlled(self, operation: Awaitable[Any]) -> Any:
        """Keep long navigation/selector waits responsive to task cancellation."""
        pending = asyncio.ensure_future(operation)
        try:
            while True:
                await check_control_state()
                done, _ = await asyncio.wait({pending}, timeout=0.5)
                if done:
                    return await pending
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)

    async def _ensure_browser(self, proxy_url: str) -> None:
        if self._browser_context is not None and self._browser_proxy_url == proxy_url:
            return
        await self.close()
        try:
            from playwright.async_api import async_playwright

            self._playwright = await self._controlled(async_playwright().start())
            options = browser_launch_options(
                self._playwright,
                timeout_ms=max(1, int(settings.http_request_timeout * 1000)),
                proxy_url=proxy_url,
            )
            self._browser = await self._controlled(self._playwright.chromium.launch(**options))
            self._browser_context = await self._controlled(self._browser.new_context(
                viewport=self.browser_viewport,
                user_agent=settings.http_user_agent,
                ignore_https_errors=not settings.http_verify_ssl,
            ))
            self._browser_context.set_default_timeout(
                max(1, int(settings.http_request_timeout * 1000))
            )
            self._browser_proxy_url = proxy_url
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception as error:
            await self.close()
            if isinstance(error, BrowserRuntimeError):
                raise
            raise BrowserRuntimeError(
                "Browser startup failed; install Playwright and Chromium or configure "
                "SPIDER_AI_BROWSER_EXECUTABLE_PATH"
            ) from error

    async def request_list_page(self, url, config=None, **kwargs) -> str:
        """Render a list page through the normal SpiderEngine request hook."""
        return await self._request_browser_page(url, config or RequestConfig(), detail=False, **kwargs)

    async def request_detail_page(self, url, config=None, **kwargs) -> str:
        """Render a detail page in the same session, for use by site adapters."""
        return await self._request_browser_page(url, config or RequestConfig(), detail=True, **kwargs)

    async def _request_browser_page(self, url, config, *, detail, **kwargs) -> str:
        if config.method.upper() != "GET":
            raise ValueError("BrowserSiteAdapter navigation supports GET requests only")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Browser navigation requires an HTTP/HTTPS URL")
        if config.params:
            query = [*parse_qsl(parsed.query, keep_blank_values=True), *config.params.items()]
            url = urlunsplit(parsed._replace(query=urlencode(query)))
        force_direct = config.force_direct or kwargs.get("force_direct", False)
        proxy_url = "" if force_direct else settings.tunnel_proxy_url
        use_anti_crawl = kwargs.get("anti_crawl_enabled")
        if use_anti_crawl is None:
            use_anti_crawl = settings.anti_crawl_enabled
        if not force_direct and not proxy_url and use_anti_crawl and (
            settings.proxy_pool_api_urls or settings.proxy_pool_file
        ):
            raise BrowserRuntimeError(
                "Browser transport does not support HTTP pool/pre-proxy chains; "
                "configure a tunnel proxy or explicitly use direct access"
            )
        async with self._browser_lock:
            await check_control_state()
            await self._ensure_browser(proxy_url)
            page = await self._controlled(self._browser_context.new_page())
            try:
                await page.set_extra_http_headers(config.headers)
                if config.cookies:
                    await self._browser_context.add_cookies([
                        {"name": name, "value": value, "url": url}
                        for name, value in config.cookies.items()
                    ])
                response = await self._controlled(page.goto(
                    url, wait_until="domcontentloaded",
                    timeout=max(1, int(settings.http_request_timeout * 1000)),
                ))
                if response is None or response.status >= 400:
                    raise DownloadError(
                        url, response.status if response is not None else None,
                        "Browser main document is unavailable",
                    )
                if urlsplit(page.url).scheme not in {"http", "https"}:
                    raise ValueError("Browser navigation escaped HTTP/HTTPS")
                prepare = self.prepare_detail_page if detail else self.prepare_list_page
                extracted = await prepare(page)
                await check_control_state()
                return extracted if extracted is not None else await page.content()
            finally:
                await page.close()

    async def wait_for_selector(self, page, selector: str) -> None:
        """Wait for rendered content without blocking pause/cancel checks."""
        timeout = self.browser_wait_timeout_seconds or settings.http_request_timeout
        await self._controlled(page.wait_for_selector(
            selector, state="visible", timeout=max(1, int(timeout * 1000)),
        ))

    async def prepare_list_page(self, page) -> str | None:
        """Override for list-page interaction or custom extraction."""
        if self.browser_wait_selector:
            await self.wait_for_selector(page, self.browser_wait_selector)
        return None

    async def prepare_detail_page(self, page) -> str | None:
        """Override for detail-page interaction or custom extraction."""
        return None

    async def collect_table_rows(
        self, table, *, max_scrolls: int = 100, settle_seconds: float = 2,
    ) -> list[dict[str, str]]:
        """Collect lazy/virtualized rows until two scrolls discover no new data.

        Preserve rows removed from the DOM by virtualization. Reaching the
        scroll limit is an error rather than a successful partial snapshot.
        """
        headers = [" ".join(value.split()) for value in await table.locator("thead th").all_text_contents()]
        if not headers or not all(headers) or len(set(headers)) != len(headers):
            raise ValueError("Browser table has missing or duplicate column headers")
        timeout = self.browser_wait_timeout_seconds or settings.http_request_timeout
        await self._controlled(table.locator("tbody tr").first.wait_for(
            state="visible", timeout=max(1, int(timeout * 1000)),
        ))
        seen = set()
        records = []
        stable = 0
        for _ in range(max_scrolls):
            await check_control_state()
            rows = await table.locator("tbody tr").evaluate_all(
                "rows => rows.map(row => Array.from(row.querySelectorAll('td'), "
                "cell => cell.innerText.trim()))"
            )
            previous_count = len(records)
            for cells in rows:
                if len(cells) != len(headers):
                    raise ValueError("Browser table contains a partial row or changed columns")
                identity = tuple(cells)
                if identity not in seen:
                    seen.add(identity)
                    records.append(dict(zip(headers, cells, strict=True)))
            stable = stable + 1 if len(records) == previous_count else 0
            if stable >= 2:
                return records
            if rows:
                await table.locator("tbody tr").last.evaluate(
                    "element => element.scrollIntoView({block: 'end'})"
                )
            await controlled_sleep(settle_seconds)
        raise ValueError("Browser table scroll limit reached; refusing an incomplete snapshot")

    async def on_error(self, error, page, attempt):
        if isinstance(error, BrowserRuntimeError | ValueError) or attempt + 1 >= self.browser_max_attempts:
            return "abort"
        await self.close()
        await controlled_sleep(min(settings.http_retry_backoff * 2 ** attempt, 10))
        return None

    async def close(self) -> None:
        """Release every resource even when another close fails; safe to repeat."""
        resources = (
            (self._browser_context, "close"), (self._browser, "close"),
            (self._playwright, "stop"),
        )
        self._browser_context = self._browser = self._playwright = None
        self._browser_proxy_url = None
        for resource, method in resources:
            if resource is not None:
                try:
                    await getattr(resource, method)()
                except Exception as error:
                    self._site_logger.warning("Browser resource cleanup failed: %s", type(error).__name__)
