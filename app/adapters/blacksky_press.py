"""BlackSky 新闻稿适配器 (Press Releases / releases CPT)。

核心逻辑
--------
1. 通过 WordPress REST API /wp-json/wp/v2/releases 翻页采集新闻稿列表
2. releases 类型的 content.rendered 有完整 HTML 正文
3. 从 content_html 提取外链
4. 从 content_html 提取正文图片，生成 images 字段并在 content_html 中用占位符映射
5. featured_media 通过 WP Media API 获取封面图 URL
6. 删除 external_url（已有 external_links）
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.adapters import register_adapter
from app.adapters.utils.news import NewsBaseAdapter
from app.downloader.http_client import HttpClient
from app.models.template import RequestConfig

logger = logging.getLogger(__name__)


@register_adapter("blacksky_press")
class BlackSkyPressAdapter(NewsBaseAdapter):
    """BlackSky 新闻稿适配器（WP REST API /releases CPT）。"""

    adapter_name = "blacksky_press"
    site_domain = "blacksky.com"

    # 封面图缓存 {media_id: source_url}
    _media_cache: dict[int, str]

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._media_cache: dict[int, str] = {}

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
            resp = await self._client.request_page(url, cfg, anti_crawl_enabled=False)
            data = json.loads(resp)
            source_url = data.get("source_url", "")
            if source_url:
                self._media_cache[media_id] = source_url
            return source_url
        except Exception as e:
            logger.debug("[BlackSkyPress] Media %d fetch failed: %s", media_id, str(e)[:60])
            return ""

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """列表页后处理：提取外链、正文图片、封面图。"""
        records = await super().on_after_page(page, records)

        for record in records:
            record["link_type"] = "press_release"

            excerpt_html = str(record.get("excerpt") or "").strip()
            if excerpt_html:
                record["summary"] = self.html_to_text(excerpt_html)

            # 从 content_html 提取外链
            content_html = str(record.get("content_html") or "").strip()
            url = record.get("url", "")
            if content_html:
                images, new_html = self.extract_images_from_html(content_html, url)
                if images:
                    record["images"] = images
                    content_html = new_html
                    record["content_html"] = new_html

                attachments = self.extract_attachment_links(content_html, url)
                if attachments:
                    record["attachments"] = attachments

                record["content"] = self.html_to_text(content_html)
                ext_links = self.extract_external_links(content_html, url)
                if ext_links:
                    record["external_links"] = ext_links

            # 删除 external_url（已有 external_links）
            record.pop("external_url", None)

            # 获取封面图 URL
            media_id = record.get("featured_media", 0)
            if media_id:
                cover_url = await self._fetch_media_url(media_id)
                if cover_url:
                    record["cover_image"] = cover_url
                    record.setdefault("thumbnail", cover_url)

        return records

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
