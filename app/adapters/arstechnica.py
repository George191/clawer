"""Ars Technica news adapter."""

from __future__ import annotations

import asyncio
import json
import re
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import etree, html as lxml_html

from app.adapters import register_adapter
from app.adapters.utils.news import NewsBaseAdapter
from app.downloader.http_client import HttpClient
from app.logger import get_adapter_logger

logger = get_adapter_logger(__name__, "arstechnica")

_PAGE_DELAY_SECONDS = 1.0
_MAX_RETRIES = 4
_RETRYABLE_PATTERNS = ("(28)", "(7)", "(6)", "HTTP Error 0", "HTTP Error 103")
_DETAIL_DELAY_SECONDS = 0.5
_DETAIL_REQUIRED_FIELDS = (
    "author",
    "source_published_at",
    "content_html",
)

_RSS_NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "media": "http://search.yahoo.com/mrss/",
}

_DROP_SELECTORS = (
    "script",
    "style",
    "noscript",
    "iframe",
    ".ad",
    ".advertisement",
    ".ars-interlude",
    ".article-permalink",
)


@register_adapter("arstechnica")
class ArsTechnicaAdapter(NewsBaseAdapter):
    """Ars Technica RSS + server-rendered article adapter."""

    adapter_name = "arstechnica"
    site_domain = "arstechnica.com"

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._template: Any = None
        self._rss_meta_by_url: dict[str, dict[str, Any]] = {}
        self._retry_count: int = 0
        self._should_stop_after_page: bool = False
        self._inline_detail_enabled: bool = False

    async def on_before_crawl(self, template: Any) -> None:
        await super().on_before_crawl(template)
        self._template = template
        self._retry_count = 0
        self._should_stop_after_page = False
        param_values = getattr(template, "_param_values", {}) or {}
        detail_value = str(param_values.get("detail") or "0").strip().lower()
        self._inline_detail_enabled = detail_value in {"1", "true", "yes", "on"}
        await self._load_rss_metadata()

    async def on_before_page(self, page: int, is_first: bool) -> None:
        if self._retry_count > 0:
            wait = min(30.0, 3.0 * (2 ** min(self._retry_count - 1, 3)))
            await asyncio.sleep(wait)
            return
        if not is_first:
            await asyncio.sleep(_PAGE_DELAY_SECONDS)

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        records = await super().on_after_page(page, records)
        if not records:
            return records

        records = self._dedupe_records(records)
        for record in records:
            self._merge_rss_metadata(record)
            self._populate_assets_from_content(record)

        if self._inline_detail_enabled:
            for index, record in enumerate(records):
                if not self._needs_detail_enrichment(record):
                    continue
                if index > 0:
                    await asyncio.sleep(_DETAIL_DELAY_SECONDS)
                records[index] = await self._enrich_detail(record)

        expected = (
            self._template.list_pagination.results_per_page
            if self._template is not None and self._template.list_pagination is not None
            else 0
        )
        self._should_stop_after_page = bool(expected and len(records) < expected)
        if self._retry_count > 0:
            logger.info(
                "Page %d recovered after %d retries",
                page,
                self._retry_count,
            )
            self._retry_count = 0
        return records

    def on_page_advance(self) -> bool | None:
        if self._should_stop_after_page:
            logger.info("Reached final archive page, stopping pagination")
            return False
        return None

    async def _load_rss_metadata(self) -> None:
        rss_url = f"{self._base_url}/feed/"
        try:
            text = await self._client.request_page(
                rss_url,
                None,
                anti_crawl_enabled=False,
                adapter_name=self.adapter_name,
            )
        except Exception as exc:
            logger.warning("Failed to fetch RSS metadata: %s", exc)
            return
        try:
            root = etree.fromstring(text.encode("utf-8"))
        except Exception as exc:
            logger.warning("Failed to parse RSS metadata: %s", exc)
            return

        for item in root.xpath("//item"):
            url = self._first_text(item, "guid/text()") or self._first_text(item, "link/text()")
            if not url:
                continue
            categories = self._all_text(item, "category/text()")
            media_url = self._first_text(item, "media:content/@url")
            thumb_url = self._first_text(item, "media:content/media:thumbnail/@url")
            summary = self._first_text(item, "description/text()")
            content_html = self._clean_feed_html(self._first_text(item, "content:encoded/text()"))
            published = self._normalize_rss_date(self._first_text(item, "pubDate/text()"))

            self._rss_meta_by_url[url] = {
                "author": self._first_text(item, "dc:creator/text()"),
                "summary": self._clean_text(summary),
                "summary_html": content_html,
                "content_html": content_html,
                "content": self._html_to_text(content_html),
                "category_names": categories[:1],
                "tags": categories[1:],
                "thumbnail": thumb_url or media_url,
                "date": published,
                "source_published_at": published,
            }

    @staticmethod
    def _dedupe_records(records: list[dict]) -> list[dict]:
        deduped: list[dict] = []
        seen: set[str] = set()
        for record in records:
            url = str(record.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            deduped.append(record)
        return deduped

    def _merge_rss_metadata(self, record: dict[str, Any]) -> None:
        url = str(record.get("url") or "").strip()
        meta = self._rss_meta_by_url.get(url)
        if not meta:
            return
        for key, value in meta.items():
            if value and not record.get(key):
                record[key] = value

    @staticmethod
    def _needs_detail_enrichment(record: dict[str, Any]) -> bool:
        return any(not record.get(field) for field in _DETAIL_REQUIRED_FIELDS)

    def _populate_assets_from_content(self, record: dict[str, Any]) -> None:
        content_html = str(record.get("content_html") or "").strip()
        url = str(record.get("url") or "").strip()
        if not content_html or not url:
            return

        if not record.get("images"):
            images = self._extract_images_from_html(content_html, url, record)
            if images:
                record["images"] = images

        if not record.get("attachments"):
            attachments = self._extract_attachments_from_html(content_html, url)
            if attachments:
                record["attachments"] = attachments

        self.merge_external_links_from_content(record, url)

    async def _enrich_detail(self, record: dict[str, Any]) -> dict[str, Any]:
        url = str(record.get("url") or "").strip()
        if not url:
            return record

        try:
            html = await self._client.request_page(
                url,
                self._detail_request(),
                adapter_name=self.adapter_name,
                anti_crawl_enabled=(
                    self._template.effective_anti_crawl_enabled
                    if self._template is not None
                    else False
                ),
            )
        except Exception as exc:
            error_str = str(exc)
            if "403" in error_str or "429" in error_str or "503" in error_str:
                await asyncio.sleep(5)
            logger.warning("Failed to fetch detail '%s': %s", url, exc)
            return record

        try:
            tree = lxml_html.fromstring(html)
        except Exception as exc:
            logger.warning("Failed to parse detail '%s': %s", url, exc)
            return record

        self._merge_detail_metadata(record, tree)
        content_html = self._extract_content_html(tree)
        if content_html:
            record["content_html"] = content_html
            record["content"] = self._html_to_text(content_html)

        images = self._extract_images(tree, url, record)
        if images:
            record["images"] = images
        else:
            record.pop("images", None)

        attachments = self._extract_attachments(tree, url)
        if attachments:
            record["attachments"] = attachments
        else:
            record.pop("attachments", None)

        self.merge_external_links_from_content(record, url)
        return record

    def _detail_request(self):
        request = self._template.detail_request
        extra_headers = self.on_request_headers(0)
        if not extra_headers:
            return request
        return request.model_copy(update={
            "headers": {**request.headers, **extra_headers},
        })

    def _merge_detail_metadata(self, record: dict[str, Any], tree: Any) -> None:
        parsely = self._json_meta(tree, 'meta[name="parsely-page"]')
        if parsely:
            if not record.get("author"):
                record["author"] = self._clean_text(parsely.get("author"))
            if not record.get("source_published_at"):
                record["source_published_at"] = parsely.get("pub_date")
            if not record.get("category_names") and parsely.get("section"):
                record["category_names"] = [parsely["section"]]
            if not record.get("tags") and parsely.get("tags"):
                record["tags"] = parsely.get("tags") or []
            image = parsely.get("image_url")
            if image and not record.get("thumbnail"):
                record["thumbnail"] = image

        published = self._meta_content(tree, 'meta[property="article:published_time"]')
        modified = self._meta_content(tree, 'meta[property="article:modified_time"]')
        image = self._meta_content(tree, 'meta[property="og:image"]')
        if published:
            record["source_published_at"] = published
        if modified:
            record["source_updated_at"] = modified
        if image:
            if not record.get("thumbnail"):
                record["thumbnail"] = image

    def _extract_content_html(self, tree: Any) -> str:
        blocks = tree.cssselect("article .post-content")
        cleaned: list[str] = []
        for block in blocks:
            for selector in _DROP_SELECTORS:
                for node in block.cssselect(selector):
                    parent = node.getparent()
                    if parent is not None:
                        parent.remove(node)
            html = etree.tostring(block, encoding="unicode", method="html").strip()
            if html:
                cleaned.append(html)
        return "\n".join(cleaned)

    def _extract_images(
        self,
        tree: Any,
        detail_url: str,
        record: dict[str, Any],
    ) -> list[dict[str, str]]:
        images: list[dict[str, str]] = []
        cover_urls = {
            self.clean_url(str(record.get("thumbnail") or "").strip()),
        }

        for img in tree.cssselect("article .post-content img[src]"):
            src = self.clean_url(urljoin(detail_url, img.get("src", "").strip()))
            if not src or src in cover_urls:
                continue
            item = {"url": src, "alt": (img.get("alt") or "").strip()}
            images.append(item)
        return self.dedupe_media_items(images)

    def _extract_attachments(self, tree: Any, detail_url: str) -> list[dict[str, str]]:
        attachments: list[dict[str, str]] = []
        for link in tree.cssselect("article .post-content a[href]"):
            href = self.clean_url(urljoin(detail_url, link.get("href", "").strip()))
            if not href or self.is_image_url(href):
                continue
            if not self.is_attachment_url(href):
                continue
            item: dict[str, str] = {
                "url": href,
                "type": self._extension_for_url(href),
            }
            label = self._clean_text(link.text_content())
            if label:
                item["label"] = label
            attachments.append(item)
        return self.dedupe_media_items(attachments)

    def _extract_images_from_html(
        self,
        content_html: str,
        detail_url: str,
        record: dict[str, Any],
    ) -> list[dict[str, str]]:
        try:
            wrapper = lxml_html.fragment_fromstring(content_html, create_parent="div")
        except Exception:
            return []

        images: list[dict[str, str]] = []
        cover_urls = {
            self.clean_url(str(record.get("thumbnail") or "").strip()),
        }

        for img in wrapper.cssselect("img"):
            raw_src = (
                img.get("src")
                or img.get("data-src")
                or ""
            ).strip()
            src = self.clean_url(urljoin(detail_url, raw_src))
            if not src or src in cover_urls:
                continue
            item = {"url": src, "alt": (img.get("alt") or "").strip()}
            images.append(item)
        return self.dedupe_media_items(images)

    def _extract_attachments_from_html(
        self,
        content_html: str,
        detail_url: str,
    ) -> list[dict[str, str]]:
        try:
            wrapper = lxml_html.fragment_fromstring(content_html, create_parent="div")
        except Exception:
            return []

        attachments: list[dict[str, str]] = []
        for link in wrapper.cssselect("a[href]"):
            href = self.clean_url(urljoin(detail_url, link.get("href", "").strip()))
            if not href or self.is_image_url(href):
                continue
            if not self.is_attachment_url(href):
                continue
            item: dict[str, str] = {
                "url": href,
                "type": self._extension_for_url(href),
            }
            label = self._clean_text(link.text_content())
            if label:
                item["label"] = label
            attachments.append(item)
        return self.dedupe_media_items(attachments)

    def extract_external_links(self, html: str, _base_url: str) -> list[str]:
        return self.dedupe_urls(super().extract_external_links(html, _base_url))

    def on_request_headers(self, page: int) -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{self._base_url}/",
            "Cache-Control": "no-cache",
        }

    async def on_error(
        self,
        error: Exception,
        page: int,
        attempt: int,
    ) -> str | None:
        error_str = str(error)
        if "404" in error_str:
            logger.info("404 on list page %d; reached archive end", page)
            return "stop"
        if attempt >= _MAX_RETRIES:
            logger.warning(
                "Page %d exceeded retry limit: %s",
                page,
                error_str[:160],
            )
            return "abort"
        if "403" in error_str or "429" in error_str or "503" in error_str:
            self._retry_count += 1
            logger.warning(
                "Retrying page %d after HTTP block/error [%d/%d]: %s",
                page,
                attempt + 1,
                _MAX_RETRIES,
                error_str[:160],
            )
            return None
        if any(pattern in error_str for pattern in _RETRYABLE_PATTERNS):
            self._retry_count += 1
            logger.warning(
                "Retrying page %d after network error [%d/%d]: %s",
                page,
                attempt + 1,
                _MAX_RETRIES,
                error_str[:160],
            )
            return None
        return None

    @staticmethod
    def _first_text(node: Any, xpath: str) -> str:
        values = node.xpath(xpath, namespaces=_RSS_NS)
        if not values:
            return ""
        return ArsTechnicaAdapter._clean_text(str(values[0]))

    @staticmethod
    def _all_text(node: Any, xpath: str) -> list[str]:
        values = node.xpath(xpath, namespaces=_RSS_NS)
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = ArsTechnicaAdapter._clean_text(str(value))
            if not text or text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        return cleaned

    @staticmethod
    def _clean_text(value: Any) -> str:
        if value is None:
            return ""
        text = str(value).replace("\xa0", " ")
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _clean_feed_html(value: str) -> str:
        if not value:
            return ""
        try:
            fragment = lxml_html.fragment_fromstring(value, create_parent="div")
            for link in fragment.cssselect("a[href]"):
                text = ArsTechnicaAdapter._clean_text(link.text_content())
                href = link.get("href", "")
                if text in {"Read full article", "Comments"} or "#comments" in href:
                    parent = link.getparent()
                    if parent is not None:
                        parent.remove(link)
            return etree.tostring(fragment, encoding="unicode", method="html").strip()
        except Exception:
            return value.strip()

    @staticmethod
    def _normalize_rss_date(value: str) -> str:
        if not value:
            return ""
        try:
            return parsedate_to_datetime(value).isoformat()
        except Exception:
            return value

    @staticmethod
    def _extension_for_url(url: str) -> str:
        path = urlparse(url).path
        filename = path.rsplit("/", 1)[-1]
        if "." not in filename:
            return "file"
        return filename.rsplit(".", 1)[-1].lower() or "file"

    @staticmethod
    def _html_to_text(value: str) -> str:
        try:
            fragment = lxml_html.fragment_fromstring(value, create_parent="div")
            return ArsTechnicaAdapter._clean_text(fragment.text_content())
        except Exception:
            return ArsTechnicaAdapter._clean_text(value)

    @staticmethod
    def _meta_content(tree: Any, selector: str) -> str:
        elements = tree.cssselect(selector)
        if not elements:
            return ""
        return ArsTechnicaAdapter._clean_text(elements[0].get("content"))

    @staticmethod
    def _json_meta(tree: Any, selector: str) -> dict[str, Any]:
        raw = ArsTechnicaAdapter._meta_content(tree, selector)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
