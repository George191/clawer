"""SSC (Space Systems Command) news adapter."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urljoin

from app.adapters import register_adapter
from app.adapters.utils.news import NewsBaseAdapter
from app.adapters.utils.news.ssc import common as ssc_common
from app.downloader.http_client import HttpClient
from app.logging_utils import get_adapter_logger
from app.parser.template_parser import TemplateParser

logger = get_adapter_logger(__name__, "ssc_news")


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
            "Starting crawl: base_url=%s, list_page=%s",
            self._base_url, template.list_page,
        )

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """Normalize list records and enrich them with detail page fields."""
        records = await super().on_after_page(page, records)
        if not records or not self._template or not self._template.detail_fields:
            return records


        async def enrich(record: dict) -> dict:
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
                adapter_name=self.adapter_name,
            )
        except Exception as e:
            logger.warning(
                "Failed to fetch detail '%s': %s",
                detail_url, e,
            )
            return record

        content_field_selector = self.detail_field_selector(self._template, "content")
        ssc_common.extract_meta_fields(html, record)
        ssc_common.extract_content(html, record, detail_url, content_field_selector)
        ssc_common.extract_slides(html, record, detail_url, content_field_selector)
        ssc_common.extract_attachments(html, record, detail_url, content_field_selector)
        ssc_common.extract_tags(html, record)
        ssc_common.extract_external_links(self, record, detail_url)

        return record

    def _detail_request(self):
        request = self._template.detail_request
        extra_headers = self.on_request_headers(0)
        if not extra_headers:
            return request
        return request.model_copy(update={
            "headers": {**request.headers, **extra_headers},
        })

    def on_request_headers(self, page: int) -> dict[str, str]:
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
        error_str = str(error)
        if "404" in error_str:
            return "skip"
        if "403" in error_str:
            logger.warning("403 Forbidden; skipping page")
            return "skip"
        return None
