"""SSC (Space Systems Command) 新闻适配器。

核心逻辑
--------
1. 列表页提取新闻条目（标题、日期、摘要、详情页链接）
2. 详情页提取正文 + 外链（军事/航天相关外部参考链接）
3. .mil 军事站点需要特殊请求头
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters.news.base import NewsBaseAdapter, register_adapter
from app.downloader.http_client import HttpClient

logger = logging.getLogger(__name__)


@register_adapter("ssc_news")
class SscNewsAdapter(NewsBaseAdapter):
    """SSC Space Force 新闻适配器。"""

    adapter_name = "ssc_news"
    site_domain = "ssc.spaceforce.mil"

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """列表页后处理：过滤空记录，标准化日期。"""
        return await super().on_after_page(page, records)

    def on_request_headers(self, page: int) -> dict[str, str]:
        """SSC 军事站点请求头。"""
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
        """SSC 错误处理。"""
        error_str = str(error)
        if "404" in error_str:
            return "skip"
        if "403" in error_str:
            logger.warning("[SscNewsAdapter] 403 Forbidden, may need different approach")
            return "skip"
        return None
