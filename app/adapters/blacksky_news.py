"""BlackSky 新闻适配器。

核心逻辑
--------
1. 列表页提取新闻条目（标题、来源、日期、链接）
2. 区分两类链接:
   - Press release → 站内详情页，进入详情提取正文 + 外链
   - 媒体报道 → 外部链接（直接作为外链记录）
3. 详情页提取正文 + 外链
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse
from typing import Any

from app.adapters.news_base import NewsBaseAdapter, register_adapter
from app.downloader.http_client import HttpClient

logger = logging.getLogger(__name__)


@register_adapter("blacksky_news")
class BlackSkyNewsAdapter(NewsBaseAdapter):
    """BlackSky 新闻适配器。"""

    adapter_name = "blacksky_news"
    site_domain = "blacksky.com"

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """列表页后处理：区分站内/外链，标记外链类型。"""
        records = await super().on_after_page(page, records)

        for record in records:
            url = record.get("url", "")
            if not url:
                continue

            parsed = urlparse(url)
            domain = parsed.netloc.lower().replace("www.", "")

            # 外部媒体报道 → 直接标记为外链
            if domain and domain != self.site_domain and not domain.endswith(f".{self.site_domain}"):
                record["external_url"] = url
                record["link_type"] = "external_media"
                logger.debug(
                    "[BlackSkyNewsAdapter] External media link: %s → %s",
                    record.get("title", "")[:50], url,
                )
            else:
                record["link_type"] = "press_release"

        return records

    def on_request_headers(self, page: int) -> dict[str, str]:
        """BlackSky 请求头。"""
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://blacksky.com/",
            "Cache-Control": "no-cache",
        }

    async def on_error(
        self, error: Exception, page: int, attempt: int,
    ) -> str | None:
        """错误处理。"""
        error_str = str(error)
        if "404" in error_str:
            return "skip"
        return None
