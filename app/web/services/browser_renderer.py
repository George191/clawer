from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from app.config.settings import settings

logger = logging.getLogger(__name__)

_CHROME_PATHS = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)


@dataclass
class BrowserRenderResult:
    url: str
    title: str
    html: str
    screenshot_data_url: str
    favicon_url: str = ""
    json_endpoints: list[str] = field(default_factory=list)


class BrowserRenderer:
    @staticmethod
    def available() -> bool:
        return any(path.is_file() for path in _CHROME_PATHS)

    @staticmethod
    def _executable_path() -> str:
        path = next((path for path in _CHROME_PATHS if path.is_file()), None)
        if path is None:
            raise RuntimeError("Chrome or Edge is not installed")
        return str(path)

    @staticmethod
    def _proxy_config(proxy_url: str) -> dict[str, str] | None:
        if not proxy_url:
            return None
        parsed = urlparse(proxy_url)
        if not parsed.scheme or not parsed.hostname or not parsed.port:
            raise ValueError("Configured browser proxy URL is invalid")
        config = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            config["username"] = parsed.username
        if parsed.password:
            config["password"] = parsed.password
        return config

    def _render_sync(self, url: str, use_proxy: bool, viewport_width: int) -> BrowserRenderResult:
        json_endpoints: list[str] = []
        proxy = self._proxy_config(settings.tunnel_proxy_url) if use_proxy else None
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=self._executable_path(),
                headless=True,
                proxy=proxy,
            )
            try:
                context = browser.new_context(
                    viewport={"width": viewport_width, "height": 1000},
                    user_agent=settings.http_user_agent,
                    ignore_https_errors=not settings.http_verify_ssl,
                    locale="zh-CN",
                )
                page = context.new_page()

                def track_response(response) -> None:
                    content_type = response.headers.get("content-type", "").lower()
                    if "json" in content_type and response.url not in json_endpoints:
                        json_endpoints.append(response.url)

                page.on("response", track_response)
                page.goto(url, wait_until="domcontentloaded", timeout=0)
                try:
                    page.wait_for_load_state("load", timeout=10_000)
                except PlaywrightTimeoutError:
                    logger.warning("Page load did not settle before capture: %s", url)
                page.evaluate(
                    """
                    async () => {
                        const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                        for (let step = 0; step < 80; step += 1) {
                            const maxScroll = Math.max(0, document.documentElement.scrollHeight - innerHeight);
                            const nextScroll = Math.min(maxScroll, (step + 1) * Math.max(1, innerHeight * 0.8));
                            scrollTo(0, nextScroll);
                            await delay(75);
                            if (nextScroll >= maxScroll) break;
                        }
                        scrollTo(0, 0);

                        const imagesReady = Promise.all(
                            Array.from(document.images)
                                .filter((image) => !image.complete)
                                .map((image) => new Promise((resolve) => {
                                    image.addEventListener('load', resolve, { once: true });
                                    image.addEventListener('error', resolve, { once: true });
                                }))
                        );
                        const fontsReady = document.fonts?.ready ?? Promise.resolve();
                        await Promise.race([
                            Promise.all([imagesReady, fontsReady]),
                            delay(5_000),
                        ]);
                    }
                    """
                )
                page.wait_for_timeout(250)
                final_url = page.url
                if urlparse(final_url).scheme not in {"http", "https"}:
                    raise ValueError("Browser navigation escaped the HTTP/HTTPS boundary")
                html = page.content()
                html = self._make_absolute_paths(html, final_url)
                favicon_href = page.locator('link[rel~="icon"]').last.get_attribute("href") if page.locator('link[rel~="icon"]').count() else ""
                page.evaluate(
                    """
                    () => {
                        const root = document.documentElement;
                        const bodyWidth = document.body?.scrollWidth ?? 0;
                        const contentWidth = Math.max(root.scrollWidth, bodyWidth);
                        const viewportWidth = root.clientWidth;
                        if (contentWidth > viewportWidth) {
                            root.style.zoom = String(Math.max(0.1, viewportWidth / contentWidth));
                        }
                    }
                    """
                )
                page.wait_for_timeout(250)
                screenshot = page.screenshot(type="jpeg", quality=78, full_page=True)
                return BrowserRenderResult(
                    url=final_url,
                    title=page.title(),
                    html=html,
                    screenshot_data_url="data:image/jpeg;base64," + base64.b64encode(screenshot).decode("ascii"),
                    favicon_url=urljoin(final_url, favicon_href) if favicon_href else urljoin(final_url, "/favicon.ico"),
                    json_endpoints=json_endpoints[:50],
                )
            finally:
                browser.close()

    def _make_absolute_paths(self, html: str, base_url: str) -> str:
        import re
        
        def make_absolute(match):
            attr = match.group(1)
            path = match.group(2)
            if path.startswith('data:') or path.startswith('http://') or path.startswith('https://'):
                return match.group(0)
            return f'{attr}="{urljoin(base_url, path)}"'
        
        html = re.sub(r'(href|src|action)=["\']([^"\']+)["\']', make_absolute, html)
        html = re.sub(r'url\(["\']?([^"\')]+)["\']?\)', lambda m: f'url("{urljoin(base_url, m.group(1))}")' if not m.group(1).startswith(('data:', 'http://', 'https://')) else m.group(0), html)
        
        return html

    async def render(
        self,
        url: str,
        use_proxy: bool = False,
        viewport_width: int = 1440,
    ) -> BrowserRenderResult:
        safe_viewport_width = max(320, min(int(viewport_width), 3840))
        return await asyncio.to_thread(self._render_sync, url, use_proxy, safe_viewport_width)


browser_renderer = BrowserRenderer()
