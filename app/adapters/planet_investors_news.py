"""Planet Investor Relations newsroom adapter."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html

from app.adapters import register_adapter
from app.adapters.utils.news import NewsBaseAdapter
from app.downloader.http_client import HttpClient
from app.logger import get_adapter_logger
from app.models.template import RequestConfig
from app.parser.template_parser import TemplateParser

logger = get_adapter_logger(__name__, "planet_investors_news")


@register_adapter("planet_investors_news")
class PlanetInvestorsNewsAdapter(NewsBaseAdapter):
    """Adapter for the standalone Planet investor news page."""

    adapter_name = "planet_investors_news"
    site_domain = "investors.planet.com"

    def __init__(self, base_url: str, http_client: HttpClient | None = None, **kwargs: Any) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._template: Any = None
        self._parser = TemplateParser()

    async def on_before_crawl(self, template: Any) -> None:
        await super().on_before_crawl(template)
        self._template = template

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        records = await super().on_after_page(page, records)
        if page > 1 or not records or not self._template:
            return [] if page > 1 else records
        semaphore = asyncio.Semaphore(6)

        async def enrich(record: dict) -> dict:
            async with semaphore:
                return await self._enrich_detail(record)

        return list(await asyncio.gather(*(enrich(record) for record in records)))

    async def _enrich_detail(self, record: dict) -> dict:
        url = str(record.get("url") or "").strip()
        if not url:
            return record
        try:
            html = await self._client.request_page(url, self._detail_request(), anti_crawl_enabled=self._template.effective_anti_crawl_enabled, adapter_name=self.adapter_name)
            detail = self._parser.parse_detail(html, self._template.detail_fields)
        except Exception as exc:
            logger.warning("Failed to fetch investor detail '%s': %s", url, exc)
            return record
        content_html = str(detail.get("content") or "").strip()
        if content_html:
            record["content_html"] = content_html
            try:
                tree = lxml_html.fragment_fromstring(content_html, create_parent="div")
                record["content"] = " ".join(tree.text_content().split())
                record["images"] = self.dedupe_media_items([{"url": self.clean_url(urljoin(url, i.get("src", ""))), "alt": (i.get("alt") or "").strip()} for i in tree.cssselect("img[src]")])
                record["attachments"] = self.dedupe_media_items([{"url": self.clean_url(urljoin(url, a.get("href", ""))), "type": a.get("href", "").rsplit(".", 1)[-1].lower()} for a in tree.cssselect("a[href]") if self.is_attachment_url(urljoin(url, a.get("href", "")))])
            except Exception:
                pass
        self.merge_external_links_from_content(record, url)
        return record

    def _detail_request(self) -> RequestConfig:
        request = self._template.detail_request
        return request.model_copy(update={"headers": {**request.headers, **self.on_request_headers(0)}})

    def on_request_headers(self, page: int) -> dict[str, str]:
        return {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9", "Cache-Control": "no-cache"}

    async def on_error(self, error: Exception, page: int, attempt: int) -> str | None:
        return "skip" if "404" in str(error) else None
