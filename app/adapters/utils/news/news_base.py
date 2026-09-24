"""新闻站点通用适配器。

设计原则
--------
- 只放新闻共有逻辑：记录过滤、日期规范、正文外链提取
- 不放站点特定选择器，不枚举正文候选容器
- 站点特殊流程留在各自 adapter；字段选择器留在模板

子类应覆盖:
- site_domain: 站点主域名（用于区分内外链）
- on_request_headers(): 站点特定请求头
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse
from typing import Any

from app.adapters import BaseSiteAdapter, register_adapter
from app.downloader.http_client import HttpClient
from app.logger import get_adapter_logger
from app.adapters.utils.news import assets

logger = get_adapter_logger(__name__, "news_base")


@register_adapter("news_base")
class NewsBaseAdapter(BaseSiteAdapter):
    """新闻站点通用适配器。"""

    adapter_name = "news_base"
    site_domain: str = ""  # 子类必须设置

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        if not self.site_domain:
            parsed = urlparse(base_url)
            self.site_domain = parsed.netloc.lower().replace("www.", "")

    async def on_before_crawl(self, template: Any) -> None:
        """Keep the active template for template-owned date parsing."""
        self._template = template
        await super().on_before_crawl(template)

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """列表页后处理：过滤空记录，标准化日期。"""
        enriched = []
        for record in records:
            if not record.get("url"):
                continue
            # 日期标准化
            if "date" in record and record["date"]:
                template = getattr(self, "_template", None)
                incremental = getattr(template, "incremental", None)
                date_format = getattr(incremental, "format", None)
                record["date"] = self._normalize_date(record["date"], date_format)
            enriched.append(record)
        return enriched

    def process_content_assets(
        self,
        record: dict[str, Any],
        base_url: str,
        content_field: str = "content_html",
    ) -> None:
        """Extract all正文 media with the shared placeholder rules."""
        assets.process_content(record, base_url, content_field)

    @staticmethod
    def _normalize_date(date_str: str, date_format: str | None = None) -> str:
        """Normalize a date using the format declared by the template."""
        if not date_str:
            return ""
        value = str(date_str).strip()
        if not date_format:
            return value
        try:
            return datetime.strptime(value, date_format).strftime("%Y-%m-%d")
        except ValueError:
            return value

    def on_request_headers(self, page: int) -> dict[str, str]:
        """默认新闻站点请求头。"""
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }

    @staticmethod
    def detail_field_selector(template: Any, field_name: str) -> str:
        """Return the selector declared for a detail field in the site template."""
        for field in getattr(template, "detail_fields", []) or []:
            if getattr(field, "name", "") == field_name:
                return str(getattr(field, "selector", "") or "").strip()
        return ""
