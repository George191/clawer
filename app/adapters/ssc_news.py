"""SSC (Space Systems Command) news adapter.

SSC news still needs article detail content even though SpiderEngine no longer
handles detail pages generically. This adapter enriches list records by fetching
their detail pages before records are saved.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urljoin

from app.adapters.news_base import NewsBaseAdapter, register_adapter
from app.downloader.http_client import HttpClient
from app.parser.template_parser import TemplateParser

logger = logging.getLogger(__name__)

_DETAIL_CONCURRENCY = 4


@register_adapter("ssc_news")
class SscNewsAdapter(NewsBaseAdapter):
    """SSC Space Force news adapter."""

    adapter_name = "ssc_news"
    site_domain = "ssc.spaceforce.mil"

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._template: Any = None
        self._parser = TemplateParser()

    async def on_before_crawl(self, template: Any) -> None:
        await super().on_before_crawl(template)
        self._template = template
        logger.info(
            "[SscNewsAdapter] Starting crawl: base_url=%s, list_page=%s",
            self._base_url, template.list_page,
        )

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """Normalize list records and enrich them with detail page fields."""
        records = await super().on_after_page(page, records)
        if not records or not self._template or not self._template.detail_fields:
            return records

        semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

        async def enrich(record: dict) -> dict:
            async with semaphore:
                return await self._enrich_detail(record)

        return await asyncio.gather(*(enrich(record) for record in records))

    async def _enrich_detail(self, record: dict) -> dict:
        raw_url = (record.get("url") or record.get("detail_url") or "").strip()
        if not raw_url:
            return record

        detail_url = urljoin(f"{self._base_url}/", raw_url)
        record["url"] = detail_url

        try:
            html = await self._client.request_page(
                detail_url,
                self._detail_request(),
                anti_crawl_enabled=self._template.effective_anti_crawl_enabled,
            )
        except Exception as e:
            logger.warning(
                "[SscNewsAdapter] Failed to fetch detail '%s': %s",
                detail_url, e,
            )
            return record

        detail = self._parse_detail(html, detail_url)
        for key, value in detail.items():
            if value not in (None, ""):
                record[key] = value

        external_links = self.extract_external_links(html, detail_url)
        if external_links:
            record["external_links"] = external_links

        return record

    def _detail_request(self):
        request = self._template.detail_request
        extra_headers = self.on_request_headers(0)
        if not extra_headers:
            return request
        return request.model_copy(update={
            "headers": {**request.headers, **extra_headers},
        })

    def _parse_detail(self, html: str, detail_url: str) -> dict[str, Any]:
        try:
            detail = self._parser.parse_detail(html, self._template.detail_fields)
        except Exception as e:
            logger.warning(
                "[SscNewsAdapter] Failed to parse detail '%s': %s",
                detail_url, e,
            )
            detail = {}

        fallback_content = self._extract_content_fallback(html)
        if fallback_content and not detail.get("content"):
            detail["content"] = fallback_content
        return detail

    @staticmethod
    def _extract_content_fallback(html: str) -> str:
        """Fallback extraction for small SSC markup changes."""
        from lxml import html as lxml_html

        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return ""

        selectors = [
            "section.article-detail-content",
            ".article-content",
            ".article-body",
            ".body-copy",
            ".news-story",
            ".story-body",
            ".entry-content",
            "#article-content",
            "article",
            "main",
        ]
        for selector in selectors:
            for node in tree.cssselect(selector):
                text = " ".join(node.text_content().split())
                if len(text) >= 80:
                    return text
        return ""

    def on_request_headers(self, page: int) -> dict[str, str]:
        """SSC military site request headers."""
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "no-cache",
        }

    async def on_error(
        self, error: Exception, page: int, attempt: int,
    ) -> str | None:
        """Handle SSC-specific errors."""
        error_str = str(error)
        if "404" in error_str:
            return "skip"
        if "403" in error_str:
            logger.warning("[SscNewsAdapter] 403 Forbidden, may need different approach")
            return "skip"
        return None
