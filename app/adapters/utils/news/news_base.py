"""新闻站点适配器基类 — 提供可复用的 HTML 处理和日期标准化工具。

设计原则
--------
- 只放真正可复用的通用工具方法（HTML 清洗、外链提取、日期标准化）
- 不放任何站点特定的选择器或内容提取逻辑
- 每个站点的选择器由各自的 base adapter 负责（如 ssc_base, blacksky_base）

子类应覆盖:
- site_domain: 站点主域名（用于区分内外链）
- on_request_headers(): 站点特定请求头
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
from typing import Any

from app.adapters import BaseSiteAdapter, register_adapter
from app.downloader.http_client import HttpClient
from app.models.template import RequestConfig
from lxml import etree

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
    """新闻站点通用适配器基类。

    子类应覆盖:
    - site_domain: 站点主域名（用于区分内外链）
    - on_request_headers(): 站点特定请求头
    """

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

    @staticmethod
    def html_to_text(html: str) -> str:
        """将 HTML 片段转换为纯文本。"""
        from lxml import html as lxml_html

        if not html:
            return ""

        try:
            wrapper = lxml_html.fragment_fromstring(html, create_parent="div")
            text = wrapper.text_content()
        except Exception:
            return re.sub(r"\s+", " ", html).strip()
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def extract_images_from_html(html: str, base_url: str) -> tuple[list[dict[str, str]], str]:
        """从 HTML 中提取图片资源，并将 src 替换为占位符。"""
        from lxml import html as lxml_html

        if not html:
            return [], html

        try:
            wrapper = lxml_html.fragment_fromstring(html, create_parent="div")
        except Exception:
            return [], html

        images: list[dict[str, str]] = []

        for img in wrapper.cssselect("img"):
            raw_src = (
                img.get("src")
                or img.get("data-src")
                or NewsBaseAdapter._first_srcset_url(img.get("srcset", ""))
            )
            if not raw_src or raw_src.startswith("data:"):
                continue
            if "/emoji/" in raw_src or "emoji" in raw_src.lower():
                continue

            full_url = urljoin(base_url, raw_src.strip())
            placeholder = f"{{{{img_{len(images)}}}}}"
            alt = (img.get("alt") or "").strip()
            images.append({
                "url": full_url,
                "placeholder": placeholder,
                "alt": alt,
            })

            img.set("src", placeholder)
            if "srcset" in img.attrib:
                del img.attrib["srcset"]
            if "data-src" in img.attrib:
                del img.attrib["data-src"]

        new_html = "".join(
            etree.tostring(child, encoding="unicode", method="html")
            for child in wrapper
        )
        if not new_html:
            new_html = etree.tostring(wrapper, encoding="unicode", method="html")
        return images, new_html.strip()

    @staticmethod
    def extract_attachment_links(html: str, base_url: str) -> list[dict[str, str]]:
        """从 HTML 中提取附件链接。"""
        from lxml import html as lxml_html

        if not html:
            return []

        try:
            wrapper = lxml_html.fragment_fromstring(html, create_parent="div")
        except Exception:
            return []

        attachments: list[dict[str, str]] = []
        seen: set[str] = set()

        for link in wrapper.cssselect("a[href]"):
            href = (link.get("href") or "").strip()
            if not href:
                continue
            file_url = urljoin(base_url, href)
            ext = NewsBaseAdapter._attachment_extension(file_url)
            if not ext or file_url in seen:
                continue
            seen.add(file_url)

            label = re.sub(r"\s+", " ", link.text_content()).strip()
            item: dict[str, str] = {
                "url": file_url,
                "type": ext.lstrip("."),
            }
            if label:
                item["label"] = label
            attachments.append(item)

        return attachments

    @staticmethod
    def _attachment_extension(url: str) -> str:
        path = urlparse(url).path.lower()
        for extension in _ATTACHMENT_EXTENSIONS:
            if path.endswith(extension):
                return extension
        return ""

    @staticmethod
    def _first_srcset_url(srcset: str) -> str:
        if not srcset:
            return ""
        return srcset.split(",", 1)[0].strip().split(" ", 1)[0].strip()

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

    # ── WordPress REST API 共享方法 ────────────────────────────

    async def _wp_request_json(self, url: str) -> Any:
        """发起 WP REST API JSON 请求并返回解析后的数据。"""
        config = RequestConfig(
            headers={
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Referer": f"{self._base_url}/",
                "Cache-Control": "no-cache",
            },
            encoding="utf-8",
        )
        text = await self._client.request_page(url, config, anti_crawl_enabled=False)
        return json.loads(text)

    async def _fetch_wp_media_url(self, media_id: int, cache: dict[int, str]) -> str:
        """通过 WP Media API 获取封面图 URL（带缓存）。

        Args:
            media_id: WordPress media ID
            cache: 子类维护的缓存字典

        Returns:
            封面图 URL 或空字符串
        """
        if not media_id or not self._client:
            return ""
        if media_id in cache:
            return cache[media_id]

        try:
            url = (
                f"{self._base_url}/wp-json/wp/v2/media/{media_id}"
                f"?_fields=source_url,media_details.sizes.full.source_url"
            )
            data = await self._wp_request_json(url)
        except Exception as e:
            logger.debug(
                "[%s] Media %d fetch failed: %s",
                self.adapter_name, media_id, str(e)[:80],
            )
            return ""

        if not isinstance(data, dict):
            return ""

        media_details = data.get("media_details") or {}
        sizes = media_details.get("sizes") or {}
        full = sizes.get("full") or {}
        source_url = str(
            full.get("source_url") or data.get("source_url") or "",
        ).strip()
        if source_url:
            cache[media_id] = source_url
        return source_url

    async def _process_content_html(
        self, record: dict, base_url: str,
    ) -> None:
        """从 content_html 中提取图片、附件、外链，并生成 content 纯文本。

        处理后 record 中会新增/更新以下字段：
        - images: 正文图片列表
        - attachments: 附件列表
        - external_links: 外链列表
        - content: 纯文本正文

        Args:
            record: 包含 content_html 的记录
            base_url: 用于解析相对 URL 的基础地址
        """
        content_html = str(record.get("content_html") or "").strip()
        if not content_html:
            return

        # 提取图片并替换占位符
        images, normalized_html = self.extract_images_from_html(content_html, base_url)
        if images:
            record["images"] = images
            content_html = normalized_html
            record["content_html"] = normalized_html

        # 提取附件
        attachments = self.extract_attachment_links(content_html, base_url)
        if attachments:
            record["attachments"] = attachments

        # 生成纯文本
        record["content"] = self.html_to_text(content_html)

        # 提取外链
        external_links = self.extract_external_links(content_html, base_url)
        if external_links:
            record["external_links"] = external_links

    async def _enrich_cover_image(
        self,
        record: dict,
        media_id: int,
        cache: dict[int, str],
    ) -> None:
        """获取封面图 URL 并设置 cover_image/thumbnail 字段。

        Args:
            record: 待补充的记录
            media_id: WordPress featured_media ID
            cache: 子类维护的缓存字典
        """
        if not media_id:
            return

        cover_url = await self._fetch_wp_media_url(media_id, cache)
        if cover_url:
            record["cover_image"] = cover_url
            record.setdefault("thumbnail", cover_url)

    async def _enrich_cover_images_batch(
        self,
        records: list[dict],
        cache: dict[int, str],
    ) -> None:
        """批量并行获取封面图 URL。

        Args:
            records: 待补充的记录列表（需含 featured_media 字段）
            cache: 子类维护的缓存字典
        """
        pending = [
            (record, int(record.get("featured_media") or 0))
            for record in records
            if record.get("featured_media")
        ]
        if not pending:
            return

        async def _fetch_one(record: dict, mid: int) -> None:
            await self._enrich_cover_image(record, mid, cache)

        await asyncio.gather(
            *(_fetch_one(rec, mid) for rec, mid in pending)
        )

    @staticmethod
    def _cleanup_wp_fields(record: dict) -> None:
        """清理 WordPress API 中间字段。

        删除已转换完成的冗余字段：
        - excerpt / excerpt_html → 已转为 summary
        - featured_media → 已获取 cover_image
        - category_ids / tag_ids → 已转为 names/slugs
        - source_ids → 已转为 source_names
        - external_url → 已有 external_links
        """
        for key in (
            "excerpt", "excerpt_html",
            "featured_media",
            "category_ids", "tag_ids",
            "source_ids",
            "external_url",
        ):
            record.pop(key, None)
