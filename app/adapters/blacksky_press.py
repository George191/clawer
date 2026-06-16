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
import re
from typing import Any
from urllib.parse import urljoin

from app.adapters.news_base import NewsBaseAdapter, register_adapter
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

    def _extract_images_from_html(self, html: str, base_url: str) -> tuple[list[dict], str]:
        """从 HTML 中提取图片信息，并用占位符替换 src。

        返回:
            images: [{"url": "原始URL", "placeholder": "{{img_0}}", "alt": "..."}, ...]
            new_html: 替换后的 HTML
        """
        images: list[dict] = []
        img_pattern = re.compile(r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>', re.IGNORECASE)

        def replace_img(match: re.Match) -> str:
            full_tag = match.group(0)
            src = match.group(1)

            # 跳过 data URI 和极小图标
            if src.startswith("data:"):
                return full_tag
            if "/emoji/" in src or "emoji" in src.lower():
                return full_tag

            # 补全相对路径
            if not src.startswith(("http://", "https://")):
                src = urljoin(base_url, src)

            idx = len(images)
            placeholder = f"{{{{img_{idx}}}}}"
            alt_match = re.search(r'alt=["\']([^"\']*)["\']', full_tag, re.IGNORECASE)
            alt_text = alt_match.group(1) if alt_match else ""

            images.append({
                "url": src,
                "placeholder": placeholder,
                "alt": alt_text,
            })

            # 替换 src 为占位符
            new_tag = full_tag.replace(match.group(1), placeholder)
            return new_tag

        new_html = img_pattern.sub(replace_img, html)
        return images, new_html

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """列表页后处理：提取外链、正文图片、封面图。"""
        records = await super().on_after_page(page, records)

        for record in records:
            record["link_type"] = "press_release"

            # 从 content_html 提取外链
            content_html = record.get("content_html", "")
            url = record.get("url", "")
            if content_html:
                ext_links = self.extract_external_links(content_html, url)
                if ext_links:
                    record["external_links"] = ext_links

            # 从 content_html 提取正文图片，替换为占位符
            if content_html:
                images, new_html = self._extract_images_from_html(content_html, url)
                if images:
                    record["images"] = images
                    record["content_html"] = new_html

            # 删除 external_url（已有 external_links）
            record.pop("external_url", None)

            # 获取封面图 URL
            media_id = record.get("featured_media", 0)
            if media_id:
                cover_url = await self._fetch_media_url(media_id)
                if cover_url:
                    record["cover_image"] = cover_url

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
