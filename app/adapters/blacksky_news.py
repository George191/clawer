"""BlackSky 媒体报道适配器 (Media Coverage / news CPT)。

核心逻辑
--------
1. 通过 WordPress REST API /wp-json/wp/v2/news 翻页采集媒体报道列表
2. news 类型的 content.rendered 为空，实际内容在外部站点
3. API 返回的 link 指向站内 /media-coverage/ 页面，不含外链
4. 外链在 HTML 列表页 /company/news/ 的 article.article-summary.news h3 a 中
5. 在 on_before_crawl 中请求 HTML 列表页，通过标题匹配提取外链
6. sources 分类 ID 映射为来源名称
7. featured_media 通过 WP Media API 获取封面图 URL
8. 删除 source_ids（已有 source_names）和 external_url（已有 external_links）
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from app.adapters import register_adapter
from app.adapters.utils.news import NewsBaseAdapter, _SOCIAL_DOMAINS
from app.downloader.http_client import HttpClient
from app.models.template import RequestConfig

logger = logging.getLogger(__name__)


def _normalize_title(title: str) -> str:
    """标准化标题用于匹配：去空白、转小写、去标点。"""
    t = re.sub(r"\s+", " ", title).strip().lower()
    t = re.sub(r"[^\w\s]", "", t)
    return t


@register_adapter("blacksky_news")
class BlackSkyNewsAdapter(NewsBaseAdapter):
    """BlackSky 媒体报道适配器（WP REST API /news CPT）。"""

    adapter_name = "blacksky_news"
    site_domain = "blacksky.com"

    # HTML 列表页外链缓存 {normalized_title: external_url}
    _html_link_map: dict[str, str]
    # 封面图缓存 {media_id: source_url}
    _media_cache: dict[int, str]
    # sources 分类映射 {id: name}
    _source_map: dict[int, str]

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._html_link_map: dict[str, str] = {}
        self._media_cache: dict[int, str] = {}
        self._source_map: dict[int, str] = {}

    async def on_before_crawl(self, template: Any) -> None:
        """爬取开始前：获取 sources 映射，请求 HTML 列表页。"""
        await super().on_before_crawl(template)

        if not self._client:
            return

        # 动态获取 sources 分类映射
        await self._fetch_source_map()

        try:
            await self._build_html_link_map(page=1)
            for p in range(2, 50):
                if len(self._html_link_map) >= 500:
                    break
                await self._build_html_link_map(page=p)

            logger.info(
                "[BlackSkyNews] Built HTML link map with %d entries",
                len(self._html_link_map),
            )
        except Exception as e:
            logger.warning(
                "[BlackSkyNews] Failed to build HTML link map: %s",
                str(e)[:100],
            )

    async def _build_html_link_map(self, page: int = 1) -> None:
        """请求 HTML 列表页，提取 news 类型文章的标题和外链。"""
        from lxml import html as lxml_html

        url = f"https://blacksky.com/company/news/page/{page}/"
        cfg = RequestConfig(
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }
        )

        try:
            html_content = await self._client.request_page(
                url, cfg, anti_crawl_enabled=False,
            )
        except Exception as e:
            logger.debug("[BlackSkyNews] HTML page %d fetch failed: %s", page, str(e)[:80])
            return

        try:
            tree = lxml_html.fromstring(html_content)
        except Exception:
            return

        for article in tree.cssselect("article.article-summary.news"):
            links = article.cssselect("h3 a")
            if not links:
                continue
            a_tag = links[0]
            title = a_tag.text_content().strip()
            href = a_tag.get("href", "").strip()

            if not title or not href:
                continue

            try:
                parsed = urlparse(href)
                domain = parsed.netloc.lower().replace("www.", "")
            except Exception:
                continue

            if not domain or domain == self.site_domain:
                continue
            if domain in _SOCIAL_DOMAINS:
                continue

            normalized = _normalize_title(title)
            self._html_link_map[normalized] = href

    async def _fetch_source_map(self) -> None:
        """通过 WP REST API 动态获取 sources 分类的 ID→名称映射。"""
        try:
            url = "https://blacksky.com/wp-json/wp/v2/sources?per_page=100&_fields=id,name"
            cfg = RequestConfig(
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                }
            )
            import json
            resp = await self._client.request_page(url, cfg, anti_crawl_enabled=False)
            items = json.loads(resp)
            for item in items:
                self._source_map[item["id"]] = item["name"]
            logger.info(
                "[BlackSkyNews] Fetched %d sources from API",
                len(self._source_map),
            )
        except Exception as e:
            logger.warning(
                "[BlackSkyNews] Failed to fetch sources: %s",
                str(e)[:100],
            )

    async def _fetch_media_url(self, media_id: int) -> str:
        """通过 WP Media API 获取封面图 URL。"""
        if not media_id or not self._client:
            return ""

        if media_id in self._media_cache:
            return self._media_cache[media_id]

        try:
            url = f"https://blacksky.com/wp-json/wp/v2/media/{media_id}?_fields=source_url,alt_text,title"
            cfg = RequestConfig(
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                }
            )
            import json
            resp = await self._client.request_page(url, cfg, anti_crawl_enabled=False)
            data = json.loads(resp)
            source_url = data.get("source_url", "")
            if source_url:
                self._media_cache[media_id] = source_url
            return source_url
        except Exception as e:
            logger.debug("[BlackSkyNews] Media %d fetch failed: %s", media_id, str(e)[:60])
            return ""

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """列表页后处理：映射来源名称，匹配外链，并尽量补全文章内容。"""
        records = await super().on_after_page(page, records)

        for record in records:
            # sources ID → 名称，然后删除 source_ids
            source_ids = record.pop("source_ids", [])
            if isinstance(source_ids, list):
                record["source_names"] = [
                    self._source_map.get(sid, f"source_{sid}") for sid in source_ids
                ]

            # 标记类型
            record["link_type"] = "media_coverage"

            # 通过标题匹配 HTML 列表页中的外链
            title = record.get("title", "")
            if title and self._html_link_map:
                normalized = _normalize_title(title)
                ext_url = self._html_link_map.get(normalized)
                if ext_url:
                    record["external_links"] = [ext_url]
                    record["source_url"] = ext_url
                else:
                    best_match = self._fuzzy_match_title(normalized)
                    if best_match:
                        record["external_links"] = [best_match]
                        record["source_url"] = best_match

            excerpt_html = str(record.get("excerpt") or "").strip()
            if excerpt_html:
                record["summary"] = self.html_to_text(excerpt_html)

            # 删除 external_url（已有 external_links）
            record.pop("external_url", None)

            # 获取封面图 URL
            media_id = record.get("featured_media", 0)
            if media_id:
                cover_url = await self._fetch_media_url(media_id)
                if cover_url:
                    record["cover_image"] = cover_url
                    record.setdefault("thumbnail", cover_url)

            source_url = str(record.get("source_url") or "").strip()
            if source_url:
                await self._enrich_external_article(record, source_url)

        return records

    async def _enrich_external_article(self, record: dict[str, Any], source_url: str) -> None:
        """抓取外部来源文章，按 ssc_news 风格补充正文、图片和附件。"""
        if not self._client:
            return

        try:
            cfg = RequestConfig(
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://blacksky.com/company/news/",
                }
            )
            page_html = await self._client.request_page(
                source_url, cfg, anti_crawl_enabled=False,
            )
        except Exception as e:
            logger.debug(
                "[BlackSkyNews] Failed to fetch source article '%s': %s",
                source_url, str(e)[:100],
            )
            return

        content_html = self.extract_main_content_html(page_html)
        if not content_html:
            return

        images, normalized_html = self.extract_images_from_html(content_html, source_url)
        if images:
            record["images"] = images
            content_html = normalized_html

        record["content_html"] = content_html
        record["content"] = self.html_to_text(content_html)

        attachments = self.extract_attachment_links(content_html, source_url)
        if attachments:
            record["attachments"] = attachments

        external_refs = self.extract_external_links(content_html, source_url)
        if external_refs:
            merged: list[str] = []
            seen: set[str] = set()
            for url in [source_url, *external_refs]:
                if url not in seen:
                    merged.append(url)
                    seen.add(url)
            record["external_links"] = merged

    def _fuzzy_match_title(self, normalized: str) -> str | None:
        """模糊匹配标题：检查是否有包含关系。"""
        for key, url in self._html_link_map.items():
            if normalized in key or key in normalized:
                return url
        return None

    def on_request_headers(self, page: int) -> dict[str, str]:
        """JSON API 请求头。"""
        return {
            "Accept": "application/json",
            "Referer": "https://blacksky.com/company/news/",
        }

    async def on_error(
        self, error: Exception, page: int, attempt: int,
    ) -> str | None:
        """错误处理。"""
        error_str = str(error)
        if "404" in error_str:
            return "skip"
        return None
