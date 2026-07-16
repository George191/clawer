from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright

from app.config.settings import settings

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

    async def render(self, url: str, use_proxy: bool = False) -> BrowserRenderResult:
        json_endpoints: list[str] = []
        proxy = self._proxy_config(settings.tunnel_proxy_url) if use_proxy else None
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                executable_path=self._executable_path(),
                headless=True,
                proxy=proxy,
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 1440, "height": 1000},
                    user_agent=settings.http_user_agent,
                    ignore_https_errors=not settings.http_verify_ssl,
                    locale="zh-CN",
                )
                page = await context.new_page()

                def track_response(response) -> None:
                    content_type = response.headers.get("content-type", "").lower()
                    if "json" in content_type and response.url not in json_endpoints:
                        json_endpoints.append(response.url)

                page.on("response", track_response)
                await page.goto(url, wait_until="domcontentloaded", timeout=0)
                await page.wait_for_timeout(1500)
                final_url = page.url
                if urlparse(final_url).scheme not in {"http", "https"}:
                    raise ValueError("Browser navigation escaped the HTTP/HTTPS boundary")
                html = await page.content()
                favicon_href = await page.locator('link[rel~="icon"]').last.get_attribute("href") if await page.locator('link[rel~="icon"]').count() else ""
                screenshot = await page.screenshot(type="jpeg", quality=78, full_page=True)
                return BrowserRenderResult(
                    url=final_url,
                    title=await page.title(),
                    html=html,
                    screenshot_data_url="data:image/jpeg;base64," + base64.b64encode(screenshot).decode("ascii"),
                    favicon_url=urljoin(final_url, favicon_href) if favicon_href else urljoin(final_url, "/favicon.ico"),
                    json_endpoints=json_endpoints[:50],
                )
            finally:
                await browser.close()


browser_renderer = BrowserRenderer()
