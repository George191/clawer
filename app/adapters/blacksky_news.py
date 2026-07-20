"""BlackSky 媒体报道适配器 (Media Coverage / news CPT)。

核心逻辑
--------
1. 通过 WordPress REST API /wp-json/wp/v2/news 翻页采集媒体报道列表
2. news 类型的 content.rendered 为空，实际内容在外部站点
3. API 返回的 link 指向站内 /media-coverage/ 页面，不含外链
4. 外链在 HTML 列表页 /company/news/ 的 article.article-summary.news h3 a 中
5. 在 on_before_crawl 中请求 HTML 列表页，通过标题匹配提取外链
6. sources 分类 ID 映射为来源名称
7. featured_media 通过新闻通用层并行获取封面图
8. 清理所有 WP API 中间字段
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.adapters import register_adapter
from app.adapters.utils.news import NewsBaseAdapter, _SOCIAL_DOMAINS
from app.adapters.utils.news.wp import assets as wp_assets
from app.downloader.http_client import HttpClient
from app.logger import get_adapter_logger
from app.models.template import RequestConfig

logger = get_adapter_logger(__name__, "blacksky_news")


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

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._html_link_map: dict[str, str] = {}
        self._source_map: dict[int, str] = {}
        self._media_cache: dict[int, str] = {}

    async def on_before_crawl(self, template: Any) -> None:
        """爬取开始前：获取 sources 映射，请求 HTML 列表页。"""
        await super().on_before_crawl(template)
        self._template = template

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
                "Built HTML link map with %d entries",
                len(self._html_link_map),
            )
        except Exception as e:
            logger.warning(
                "Failed to build HTML link map: %s",
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
                url, cfg, anti_crawl_enabled=False, adapter_name=self.adapter_name,
            )
        except Exception as e:
            logger.debug("Failed to fetch HTML page %d: %s", page, str(e)[:80])
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
        url = f"{self._base_url}/wp-json/wp/v2/sources?per_page=100&_fields=id,name"
        items = await wp_assets.wp_request_json(
            self._client, url, adapter_name=self.adapter_name
        )
        for item in items:
            self._source_map[item["id"]] = item["name"]
        logger.info(
            "Fetched %d sources from API",
            len(self._source_map),
        )


    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """列表页后处理：映射来源、匹配外链、并行封面图。"""
        records = await super().on_after_page(page, records)
        if not records:
            return records

        await wp_assets.enrich_cover_images_batch(
            self._client,
            self._base_url,
            records,
            self._media_cache,
            adapter_name=self.adapter_name,
        )

        for record in records:
            record["link_type"] = "media_coverage"

            # sources ID → 名称
            source_ids = record.pop("source_ids", [])
            if isinstance(source_ids, list):
                record["source_names"] = [
                    self._source_map.get(sid, f"source_{sid}") for sid in source_ids
                ]

            # 通过标题匹配 HTML 列表页中的原始链接（当前页面直接跳转的外部文章）
            title = record.get("title", "")
            if title and self._html_link_map:
                normalized = _normalize_title(title)
                ext_url = self._html_link_map.get(normalized)
                if not ext_url:
                    ext_url = self._fuzzy_match_title(normalized)
                if ext_url:
                    record["source_url"] = ext_url

            # 清理 WP API 中间字段
            wp_assets.cleanup_wp_fields(record)

        return records

    def _fuzzy_match_title(self, normalized: str) -> str | None:
        """模糊匹配标题：检查是否有包含关系。"""
        for key, url in self._html_link_map.items():
            if normalized in key or key in normalized:
                return url
        return None

    def on_request_headers(self, page: int) -> dict[str, str]:
        """BlackSky JSON API 请求头。"""
        return {
            "Accept": "application/json",
            "Referer": "https://blacksky.com/company/news/",
        }

    async def on_error(
        self, error: Exception, page: int, attempt: int,
    ) -> str | None:
        """BlackSky 站点错误处理。"""
        error_str = str(error)
        lowered = error_str.lower()
        if "400" in error_str and (
            "invalid page" in lowered
            or "rest_post_invalid_page_number" in lowered
            or "larger than the number of pages available" in lowered
        ):
            return "abort"
        if "404" in error_str:
            return "abort"
        if "403" in error_str:
            if attempt >= 2:
                return "abort"
            import asyncio
            await asyncio.sleep(3 * (attempt + 1))
            return None
        if "429" in error_str or "503" in error_str:
            import asyncio
            await asyncio.sleep(5 * (attempt + 1))
            return None
        return None
