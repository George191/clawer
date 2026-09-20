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

import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
from typing import Any

from lxml import etree

from app.adapters import BaseSiteAdapter, register_adapter
from app.downloader.http_client import HttpClient
from app.logger import get_adapter_logger

logger = get_adapter_logger(__name__, "news_base")

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
                clean = self.clean_url(urljoin(_base_url, href))
                parsed = urlparse(clean)
                domain = parsed.netloc.lower().replace("www.", "")
            except Exception:
                continue

            # 排除站内链接（仅排除主域名本身和 www 子域名，其他子域名视为外链）
            if not domain:
                continue
            if domain == self.site_domain or domain == f"www.{self.site_domain}":
                continue

            if domain in _SOCIAL_DOMAINS:
                continue

            if not parsed.scheme or parsed.scheme not in ("http", "https"):
                continue

            # 去重（忽略 fragment）
            if self.is_attachment_url(clean):
                continue

            if clean in seen:
                continue
            seen.add(clean)

            external_links.append(clean)

        return external_links

    @classmethod
    def dedupe_urls(cls, urls: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if not isinstance(url, str):
                continue
            clean = cls.clean_url(url)
            if not clean or clean in seen:
                continue
            seen.add(clean)
            deduped.append(clean)
        return deduped

    @classmethod
    def dedupe_media_items(cls, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            url = cls.clean_url(str(item.get("url") or ""))
            if not url or url in seen:
                continue
            seen.add(url)
            next_item = dict(item)
            next_item["url"] = url
            deduped.append(next_item)
        return deduped

    @classmethod
    def merge_unique_list(cls, existing: Any, incoming: list[str]) -> list[str]:
        merged: list[str] = []
        if isinstance(existing, list):
            merged.extend(str(item) for item in existing if isinstance(item, str))
        merged.extend(incoming)
        return cls.dedupe_urls(merged)

    @classmethod
    def merge_external_links(cls, existing: Any, incoming: list[str]) -> list[str]:
        merged: list[str] = []
        if isinstance(existing, list):
            merged.extend(str(item) for item in existing if isinstance(item, str))
        merged.extend(incoming)

        external_links: list[str] = []
        for url in merged:
            clean = cls.clean_url(url)
            if not clean or cls.is_attachment_url(clean):
                continue
            external_links.append(clean)
        return cls.dedupe_urls(external_links)

    def merge_external_links_from_content(
        self,
        record: dict[str, Any],
        base_url: str,
        content_field: str = "content_html",
    ) -> None:
        """从正文 HTML 提取外链并合并到 record.external_links。"""
        existing = record.get("external_links") or []
        links: list[str] = []
        content_html = str(record.get(content_field) or "").strip()
        if content_html:
            links = self.extract_external_links(content_html, base_url)
            videos = self.extract_video_media(content_html, base_url)
            iframe = self.extract_iframe_media(content_html, base_url)
            if videos:
                record["videos"] = self.dedupe_media_items(
                    list(record.get("videos") or []) + videos
                )
            if iframe:
                record["iframe"] = self.dedupe_media_items(
                    list(record.get("iframe") or []) + iframe
                )

        if not links and not existing:
            record.pop("external_links", None)
            return

        merged = self.merge_external_links(existing, links)
        if merged:
            record["external_links"] = merged
        else:
            record.pop("external_links", None)

    def process_content_assets(
        self,
        record: dict[str, Any],
        base_url: str,
        content_field: str = "content_html",
    ) -> None:
        """Extract all正文 media with the shared placeholder rules."""
        from lxml import html as lxml_html
        from app.adapters.utils.news.assets import (
            extract_attachments_from_wrapper,
            extract_iframes_from_wrapper,
            extract_images_from_wrapper,
            extract_videos_from_wrapper,
        )

        content = str(record.get(content_field) or "").strip()
        if not content:
            self._drop_empty_media_fields(record)
            record.pop("external_links", None)
            return
        try:
            wrapper = lxml_html.fragment_fromstring(content, create_parent="div")
        except Exception:
            self._drop_empty_media_fields(record)
            return

        images = extract_images_from_wrapper(wrapper, base_url)
        videos = extract_videos_from_wrapper(wrapper, base_url)
        iframes = extract_iframes_from_wrapper(wrapper, base_url)
        tagged_urls = {
            str(item.get("url") or "")
            for items in (images, videos, iframes)
            for item in items
            if isinstance(item, dict)
        }
        candidate_external = set(self.extract_external_links(content, base_url))
        attachments = extract_attachments_from_wrapper(
            wrapper,
            base_url,
            excluded_urls=tagged_urls,
        )

        record[content_field] = "".join(
            etree.tostring(child, encoding="unicode", method="html")
            for child in wrapper
        ).strip()
        extracted_by_key = (
            ("images", images),
            ("videos", videos),
            ("iframe", iframes),
            ("attachments", attachments),
        )
        for key, values in extracted_by_key:
            existing = record.get(key)
            merged = list(existing) if isinstance(existing, list) else []
            merged.extend(values)
            if merged:
                record[key] = self.dedupe_media_items(merged)
            else:
                record.pop(key, None)

        media_urls = tagged_urls | {
            str(item.get("url") or "")
            for key, _ in extracted_by_key
            for item in (record.get(key) or [])
            if isinstance(item, dict)
        }
        external = [url for url in candidate_external if url not in media_urls]
        if external:
            record["external_links"] = self.merge_external_links(
                record.get("external_links"), external
            )
        else:
            record.pop("external_links", None)

    @staticmethod
    def _drop_empty_media_fields(record: dict[str, Any]) -> None:
        for key in ("images", "videos", "iframe", "attachments"):
            if isinstance(record.get(key), list) and not record[key]:
                record.pop(key, None)

    @classmethod
    def extract_video_media(
        cls,
        html: str,
        base_url: str,
    ) -> list[dict[str, str]]:
        """Extract direct video resources from article HTML."""
        from lxml import html as lxml_html
        from app.adapters.utils.news.assets import extract_videos_from_wrapper

        if not html:
            return []
        try:
            wrapper = lxml_html.fragment_fromstring(html, create_parent="div")
        except Exception:
            return []

        return extract_videos_from_wrapper(wrapper, base_url)

    @classmethod
    def extract_iframe_media(
        cls,
        html: str,
        base_url: str,
    ) -> list[dict[str, str]]:
        """Extract iframe players separately from downloadable videos."""
        from lxml import html as lxml_html
        from app.adapters.utils.news.assets import extract_iframes_from_wrapper

        if not html:
            return []
        try:
            wrapper = lxml_html.fragment_fromstring(html, create_parent="div")
        except Exception:
            return []

        return extract_iframes_from_wrapper(wrapper, base_url)

    @staticmethod
    def is_attachment_url(url: str) -> bool:
        from app.adapters.utils.news.assets import is_attachment_url
        return is_attachment_url(url)

    @staticmethod
    def clean_url(url: str) -> str:
        value = str(url or "").strip()
        if not value:
            return ""
        parsed = urlparse(value)
        if not parsed.scheme or parsed.scheme not in ("http", "https"):
            return ""
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            clean += f"?{parsed.query}"
        return clean

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
