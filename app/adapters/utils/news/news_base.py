"""新闻站点适配器基类 — 提供通用的外链提取和日期标准化逻辑。

子类只需关注站点特定的选择器和请求头，通用逻辑由基类处理：
1. 详情页外链提取（排除站内链接、导航、社交媒体等）
2. 日期格式标准化
3. 正文清洗
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from datetime import datetime
from urllib.parse import urljoin, urlparse
from typing import Any

from app.adapters import BaseSiteAdapter, register_adapter
from app.downloader.http_client import HttpClient
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

_CONTENT_SELECTORS = [
    "article [itemprop='articleBody']",
    "article .entry-content",
    "article .post-content",
    "article .article-body",
    "article .article-content",
    "article .story-body",
    "article .body-copy",
    ".entry-content",
    ".post-content",
    ".article-body",
    ".article-content",
    ".story-body",
    ".body-copy",
    ".main-content article",
    "main article",
    "article",
]


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
            if not href or _NAV_PATTERNS.match(href):
                continue

            # 解析 URL
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
    def extract_main_content_html(page_html: str) -> str:
        """从完整页面中按通用新闻正文选择器提取主内容 HTML。"""
        from lxml import html as lxml_html

        if not page_html:
            return ""

        try:
            tree = lxml_html.fromstring(page_html)
        except Exception:
            return ""

        best_node = None
        best_score = 0

        for selector in _CONTENT_SELECTORS:
            try:
                nodes = tree.cssselect(selector)
            except Exception:
                continue
            for node in nodes:
                text = re.sub(r"\s+", " ", node.text_content()).strip()
                if len(text) > best_score:
                    best_score = len(text)
                    best_node = node

        if best_node is None or best_score < 80:
            return ""

        clone = deepcopy(best_node)
        for selector in (
            "script", "style", "noscript", "iframe", "svg", "form",
            "button", "nav", "aside", "footer", "header",
            ".share", ".sharing", ".social", ".social-share",
            ".newsletter", ".subscribe", ".advertisement", ".ads",
            ".related", ".recommended", ".author-box", ".post-meta",
        ):
            for node in clone.cssselect(selector):
                node.drop_tree()

        return etree.tostring(clone, encoding="unicode", method="html").strip()

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
