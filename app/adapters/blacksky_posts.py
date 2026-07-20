"""BlackSky 博客文章适配器 (Posts / posts CPT)。

核心逻辑
--------
1. 通过 WordPress REST API /wp-json/wp/v2/posts 翻页采集博客文章列表
2. posts 类型的 content.rendered 有完整 HTML 正文
3. 正文处理（图片/附件/外链）由 WordPress 新闻工具统一完成
4. featured_media 通过新闻通用层并行获取封面图
5. 清理所有 WP API 中间字段
"""

from __future__ import annotations

from typing import Any

from app.adapters import register_adapter
from app.adapters.utils.news import NewsBaseAdapter
from app.adapters.utils.news.wp import assets as wp_assets
from app.downloader.http_client import HttpClient
from app.logging_utils import get_adapter_logger

logger = get_adapter_logger(__name__, "blacksky_posts")


@register_adapter("blacksky_posts")
class BlackSkyPostsAdapter(NewsBaseAdapter):
    """BlackSky 博客文章适配器（WP REST API /posts CPT）。"""

    adapter_name = "blacksky_posts"
    site_domain = "blacksky.com"

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._media_cache: dict[int, str] = {}

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """列表页后处理：正文提取、封面图、字段清理。"""
        records = await super().on_after_page(page, records)
        if not records:
            return records

        # 并行获取封面图
        await wp_assets.enrich_cover_images_batch(
            self._client,
            self._base_url,
            records,
            self._media_cache,
            adapter_name=self.adapter_name,
        )

        for record in records:
            record["link_type"] = "blog_post"

            # 正文处理：图片/附件/外链
            url = str(record.get("url") or self._base_url)
            await wp_assets.process_content_html(self, record, url)

            # 清理 WP API 中间字段
            wp_assets.cleanup_wp_fields(record)

        return records

    def on_request_headers(self, page: int) -> dict[str, str]:
        """BlackSky JSON API 请求头。"""
        return {
            "Accept": "application/json",
            "Referer": "https://blacksky.com/blog/",
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
