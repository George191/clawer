from __future__ import annotations

import asyncio
import base64
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

from app.config.ai_settings import ai_settings
from app.config.settings import settings
from app.logger import get_logger

logger = get_logger(__name__)

_CHROME_PATHS = (
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
    Path("/usr/bin/google-chrome"),
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
    preview_image: str = ""
    favicon_url: str = ""
    json_endpoints: list[str] = field(default_factory=list)
    network_responses: list[dict[str, object]] = field(default_factory=list)
    browser_events: list[dict[str, object]] = field(default_factory=list)


class BrowserRenderer:
    @staticmethod
    def _json_shape(body: str) -> dict[str, object]:
        """Return bounded field evidence without passing a full API payload to the model."""
        try:
            payload = json.loads(body)
        except (TypeError, json.JSONDecodeError):
            return {}

        def find_record(value: object, path: str = "", depth: int = 0):
            if depth > 5:
                return None
            if isinstance(value, list):
                record = next((item for item in value if isinstance(item, dict)), None)
                return (path, record) if record is not None else None
            if isinstance(value, dict):
                for key, child in value.items():
                    child_path = f"{path}.{key}" if path else key
                    found = find_record(child, child_path, depth + 1)
                    if found is not None:
                        return found
            return None

        def sample_value(value: object) -> object:
            if isinstance(value, str):
                return value[:240]
            if isinstance(value, int | float | bool) or value is None:
                return value
            if isinstance(value, list):
                return [sample_value(item) for item in value[:3]]
            if isinstance(value, dict):
                return {key: sample_value(item) for key, item in list(value.items())[:10]}
            return str(value)[:240]

        result: dict[str, object] = {
            "jsonTopLevelType": type(payload).__name__,
            "jsonTopLevelFields": list(payload)[:40] if isinstance(payload, dict) else [],
        }
        found = find_record(payload)
        if found is not None:
            item_path, record = found
            result.update(
                {
                    "jsonItemPath": item_path,
                    "recordFields": list(record)[:80],
                    "sampleRecord": {
                        key: sample_value(value)
                        for key, value in list(record.items())[:40]
                    },
                }
            )
        return result

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

    def _render_sync(
        self,
        url: str,
        use_proxy: bool,
        viewport_width: int,
        viewport_height: int,
        on_event: Callable[[dict[str, object]], None] | None,
    ) -> BrowserRenderResult:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Browser rendering is unavailable; install playwright and its browser runtime"
            ) from exc

        json_endpoints: list[str] = []
        network_responses: list[dict[str, object]] = []
        browser_events: list[dict[str, object]] = []

        def add_browser_event(kind: str, event_url: str, **details: object) -> None:
            if len(browser_events) >= 60:
                if kind not in {"snapshot", "closed"}:
                    return
                browser_events.pop()
            event = {"kind": kind, "url": event_url, **details}
            browser_events.append(
                {key: value for key, value in event.items() if key != "previewImage"}
            )
            if on_event:
                on_event(event)

        add_browser_event("navigation_requested", url, label="Open target URL")
        proxy = self._proxy_config(settings.tunnel_proxy_url) if use_proxy else None
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=self._executable_path(),
                headless=True,
                proxy=proxy,
            )
            page = None
            try:
                context = browser.new_context(
                    viewport={"width": viewport_width, "height": viewport_height},
                    user_agent=settings.http_user_agent,
                    ignore_https_errors=not settings.http_verify_ssl,
                    locale="zh-CN",
                )
                page = context.new_page()
                page.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in {"media", "font"}
                    else route.continue_(),
                )

                def track_navigation(frame) -> None:
                    if frame == page.main_frame and frame.url:
                        add_browser_event(
                            "navigation",
                            frame.url,
                            label="Main frame navigated",
                        )

                page.on("framenavigated", track_navigation)

                def track_response(response) -> None:
                    content_type = response.headers.get("content-type", "").lower()
                    if "json" in content_type and response.url not in json_endpoints:
                        json_endpoints.append(response.url)
                    resource_type = response.request.resource_type
                    if resource_type == "document":
                        add_browser_event(
                            "document_response",
                            response.url,
                            label="Document response",
                            status=response.status,
                            contentType=content_type,
                        )
                    if (
                        resource_type in {"xhr", "fetch"} or "json" in content_type
                    ) and len(network_responses) < 24:
                        evidence: dict[str, object] = {
                            "url": response.url,
                            "status": response.status,
                            "contentType": content_type,
                            "resourceType": resource_type,
                        }
                        structured_response = any(
                            marker in content_type
                            for marker in ("json", "text/plain", "application/xml", "text/xml")
                        )
                        textual_api_response = structured_response or (
                            resource_type in {"xhr", "fetch"} and "html" in content_type
                        )
                        if textual_api_response:
                            try:
                                content_length = int(response.headers.get("content-length", "0") or 0)
                                if content_length <= 262_144:
                                    body = response.text()
                                    evidence["bodyPreview"] = re.sub(r"\s+", " ", body)[:800]
                                    json_shape = self._json_shape(body)
                                    if json_shape:
                                        evidence.update(json_shape)
                                        if response.url not in json_endpoints:
                                            json_endpoints.append(response.url)
                                    if response.status >= 400 and "html" in content_type:
                                        evidence["links"] = [
                                            urljoin(response.url, href)
                                            for href in re.findall(
                                                r'href=["\']([^"\']+)', body, re.IGNORECASE
                                            )[:10]
                                        ]
                            except Exception:
                                logger.debug("Could not capture structured response body: %s", response.url)
                        if evidence not in network_responses:
                            network_responses.append(evidence)
                        add_browser_event(
                            "api_candidate",
                            response.url,
                            label=(
                                "Structured API candidate"
                                if evidence.get("recordFields")
                                else "XHR/fetch response inspected"
                            ),
                            status=response.status,
                            contentType=content_type,
                            resourceType=resource_type,
                            decision=(
                                "record_shape_found"
                                if evidence.get("recordFields")
                                else "no_record_shape"
                            ),
                            recordFields=list(evidence.get("recordFields") or [])[:12],
                        )

                page.on("response", track_response)
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=max(1, int(ai_settings.page_fetch_timeout * 1000)),
                )
                try:
                    page.wait_for_function(
                        "document.body && document.body.innerText.trim().length >= 80",
                        timeout=3_000,
                    )
                except PlaywrightTimeoutError:
                    logger.info("Capturing current dynamic page state without a populated body: %s", url)
                for _ in range(10):
                    page.wait_for_timeout(250)
                    if any(response.get("recordFields") for response in network_responses):
                        break
                final_url = page.url
                if urlparse(final_url).scheme not in {"http", "https"}:
                    raise ValueError("Browser navigation escaped the HTTP/HTTPS boundary")
                html = self._compact_html(page.content())
                favicon_href = page.locator('link[rel~="icon"]').last.get_attribute("href") if page.locator('link[rel~="icon"]').count() else ""
                discovered_links = page.eval_on_selector_all(
                    "a[href]",
                    """
                    (elements) => elements.slice(0, 80).map((element) => ({
                        href: element.href,
                        text: (element.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80),
                    }))
                    """,
                )
                seen_links: set[str] = set()
                final_host = urlparse(final_url).hostname
                for link in discovered_links:
                    link_url = str(link.get("href") or "")
                    if not link_url.startswith(("http://", "https://")) or link_url in seen_links:
                        continue
                    seen_links.add(link_url)
                    add_browser_event(
                        "page_link",
                        link_url,
                        label="Page link discovered",
                        text=str(link.get("text") or ""),
                        scope=(
                            "internal"
                            if urlparse(link_url).hostname == final_host
                            else "external"
                        ),
                    )
                    if len(seen_links) >= 20:
                        break
                screenshot = page.screenshot(type="jpeg", quality=62, full_page=False)
                preview_image = (
                    "data:image/jpeg;base64,"
                    + base64.b64encode(screenshot).decode("ascii")
                )
                add_browser_event(
                    "snapshot",
                    final_url,
                    label="Viewport snapshot captured",
                    title=page.title(),
                    previewImage=preview_image,
                )
                return BrowserRenderResult(
                    url=final_url,
                    title=page.title(),
                    html=html,
                    preview_image=preview_image,
                    favicon_url=urljoin(final_url, favicon_href) if favicon_href else urljoin(final_url, "/favicon.ico"),
                    json_endpoints=json_endpoints[:20],
                    network_responses=network_responses,
                    browser_events=browser_events,
                )
            finally:
                add_browser_event(
                    "closed",
                    page.url if page is not None else url,
                    label="Browser closed",
                )
                browser.close()

    @staticmethod
    def _compact_html(html: str) -> str:
        html = re.sub(
            r"<(script|style|noscript|svg)\b[^>]*>[\s\S]*?</\1>",
            " ",
            html,
            flags=re.IGNORECASE,
        )
        html = re.sub(r"<!--([\s\S]*?)-->", " ", html)
        html = re.sub(r"\s+", " ", html).strip()
        return html[: ai_settings.max_html_chars_for_llm]

    async def render(
        self,
        url: str,
        use_proxy: bool = False,
        viewport_width: int = 1440,
        viewport_height: int = 1000,
        on_event: Callable[[dict[str, object]], None] | None = None,
    ) -> BrowserRenderResult:
        safe_viewport_width = max(320, min(int(viewport_width), 3840))
        safe_viewport_height = max(240, min(int(viewport_height), 3840))
        return await asyncio.to_thread(
            self._render_sync,
            url,
            use_proxy,
            safe_viewport_width,
            safe_viewport_height,
            on_event,
        )


browser_renderer = BrowserRenderer()
