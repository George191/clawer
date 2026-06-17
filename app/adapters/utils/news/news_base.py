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

import logging
import re
from datetime import datetime
from urllib.parse import urlparse
from typing import Any

from app.adapters import BaseSiteAdapter, register_adapter
from app.downloader.http_client import HttpClient

logger = logging.getLogger(__name__)

# 社交媒体和常见非内容域名（外链提取时排除）
_SOCIAL_DOMAINS = {
    "facebook.com", "twitter.com", "x.com", "linkedin.com",
    "instagram.com", "youtube.com", "tiktok.com", "reddit.com",
    "t.co", "bit.ly", "ow.ly", "buff.ly", "tinyurl.com",
    "sharethis.com", "addthis.com",
}

# 导航/功能链接关键词
_NAV_PATTERNS = re.compile(
    r"^(mailto:|tel:|javascript:|#|/login|/signup|/subscribe|/rss|/feed)",
    re.IGNORECASE,
)

_ATTACHMENT_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".csv", ".txt", ".zip", ".rar", ".7z", ".json", ".xml",
    ".kml", ".kmz", ".geojson", ".gdb", ".gpkg",
)


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

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """列表页后处理：过滤空记录，标准化日期。"""
        enriched = []
        for record in records:
            if not record.get("title") and not record.get("url"):
                continue
            # 日期标准化
            if "date" in record and record["date"]:
                record["date"] = self._normalize_date(record["date"])
            enriched.append(record)
        return enriched

    def extract_external_links(self, html: str, _base_url: str) -> list[str]:
        """从 HTML 中提取外链（排除站内链接、社交媒体、导航链接）。

        Args:
            html: 页面 HTML 内容
            _base_url: 保留给调用方的页面 URL，上层接口兼容用

        Returns:
            去重后的外链列表
        """
        from lxml import html as lxml_html

        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return []

        seen: set[str] = set()
        external_links: list[str] = []

        for a_tag in tree.iter("a"):
            href = a_tag.get("href", "").strip()
            if not href or _NAV_PATTERNS.match(href):
                continue

            try:
                parsed = urlparse(href)
                domain = parsed.netloc.lower().replace("www.", "")
            except Exception:
                continue

            # 排除站内链接（仅排除主域名本身和 www 子域名，其他子域名视为外链）
            # 例如 blacksky.com 和 www.blacksky.com 是站内，ir.blacksky.com 是外链
            if not domain:
                continue
            if domain == self.site_domain or domain == f"www.{self.site_domain}":
                continue

            # 排除社交媒体
            if domain in _SOCIAL_DOMAINS:
                continue

            # 排除纯锚点 / 无协议
            if not parsed.scheme or parsed.scheme not in ("http", "https"):
                continue

            # 去重（忽略 fragment）
            clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                clean += f"?{parsed.query}"
            if clean in seen:
                continue
            seen.add(clean)

            external_links.append(href)

        return external_links

    def merge_external_links_from_content(
        self,
        record: dict[str, Any],
        base_url: str,
        content_field: str = "content_html",
    ) -> None:
        """从正文 HTML 提取外链并合并到 record.external_links。"""
        content_html = str(record.get(content_field) or "").strip()
        if not content_html:
            return

        links = [
            link
            for link in self.extract_external_links(content_html, base_url)
            if not self.is_attachment_url(link)
        ]
        if not links:
            return

        existing = record.get("external_links") or []
        merged: list[str] = []
        seen: set[str] = set()
        for url in [*existing, *links]:
            if url in seen:
                continue
            seen.add(url)
            merged.append(url)
        record["external_links"] = merged

    @staticmethod
    def is_attachment_url(url: str) -> bool:
        return bool(NewsBaseAdapter._attachment_extension(url))

    @staticmethod
    def _attachment_extension(url: str) -> str:
        path = urlparse(url).path.lower()
        for extension in _ATTACHMENT_EXTENSIONS:
            if path.endswith(extension):
                return extension
        return ""

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        """将各种日期格式标准化为 ISO 8601。"""
        if not date_str:
            return ""

        date_str = date_str.strip()

        # 尝试常见格式
        formats = [
            "%B %d, %Y",       # June 12, 2026
            "%b %d, %Y",       # Jun 12, 2026
            "%Y-%m-%d",        # 2026-06-12
            "%m/%d/%Y",        # 06/12/2026
            "%d/%m/%Y",        # 12/06/2026
            "%Y年%m月%d日",     # 2026年06月12日
            "%B %d %Y",        # June 12 2026
            "%d %B %Y",        # 12 June 2026
            "%d %b %Y",        # 12 Jun 2026
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

        # 尝试提取日期模式
        match = re.search(
            r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", date_str,
        )
        if match:
            y, m, d = match.groups()
            try:
                dt = datetime(int(y), int(m), int(d))
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass

        return date_str  # 无法解析则原样返回

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
