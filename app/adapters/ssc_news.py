"""SSC (Space Systems Command) news adapter.

采集 SSC Newsroom 文章和 Media Room 新闻稿。
两者共用同一套详情页模板（DNN ArticleCS），区别通过 article-detail-tag 区分。

详情页结构:
  article.article-detail
    header > h1                          → 标题
    section.article-detail-content
      div.meta > ul > li                 → 发布日期 + 机构
      strong.article-detail-dateline     → 电头 (地点, 如 "EL SEGUNDO, Calif. --")
      正文 HTML
    footer > a.article-detail-tag        → 标签 (USSF, Press Release 等)

  ul.slides > li.slide > figure.article-detail-gallery
    img.poster                           → 图片
    figcaption.wip-fb-caption > p        → 图片说明 (h1 是重复标题，跳过)
    div.actions a.download-url           → 高清下载链接

优化点:
  - meta 信息（日期、机构）单独字段，不混入 content
  - dateline 单独提取
  - slide caption 只取 p（跳过重复的 h1）
  - 正文图片提取并映射占位符
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import etree
from lxml import html as lxml_html

from app.adapters.news_base import NewsBaseAdapter, register_adapter
from app.downloader.http_client import HttpClient
from app.parser.template_parser import TemplateParser

logger = logging.getLogger(__name__)

_DETAIL_CONCURRENCY = 4

_CONTENT_SELECTORS = [
    ".article-view [itemprop='articleBody']",
    ".article-view .article-detail-content",
    ".article-view .article-content",
    ".article-view .article-body",
    ".article-view .body-copy",
    ".article-view .news-story",
    ".article-view .story-body",
    ".article-view .entry-content",
    ".article-view .body",
    "[itemprop='articleBody']",
    "section.article-detail-content",
    ".article-content",
    ".article-body",
    ".body-copy",
    ".news-story",
    ".story-body",
    ".entry-content",
    "#article-content",
]

_ATTACHMENT_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".csv", ".txt", ".zip", ".rar", ".7z", ".json", ".xml",
    ".kml", ".kmz", ".geojson", ".gdb", ".gpkg",
)


@register_adapter("ssc_news")
class SscNewsAdapter(NewsBaseAdapter):
    """SSC Space Force news adapter."""

    adapter_name = "ssc_news"
    site_domain = "ssc.spaceforce.mil"

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._template: Any = None
        self._parser = TemplateParser()

    async def on_before_crawl(self, template: Any) -> None:
        await super().on_before_crawl(template)
        self._template = template
        logger.info(
            "[SscNews] Starting crawl: base_url=%s, list_page=%s",
            self._base_url, template.list_page,
        )

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """Normalize list records and enrich them with detail page fields."""
        records = await super().on_after_page(page, records)
        if not records or not self._template or not self._template.detail_fields:
            return records

        semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

        async def enrich(record: dict) -> dict:
            async with semaphore:
                return await self._enrich_detail(record)

        return await asyncio.gather(*(enrich(record) for record in records))

    async def _enrich_detail(self, record: dict) -> dict:
        raw_url = (record.get("url") or record.get("detail_url") or "").strip()
        if not raw_url:
            return record

        detail_url = urljoin(f"{self._base_url}/", raw_url)
        record["url"] = detail_url

        try:
            html = await self._client.request_page(
                detail_url,
                self._detail_request(),
                anti_crawl_enabled=self._template.effective_anti_crawl_enabled,
            )
        except Exception as e:
            logger.warning(
                "[SscNews] Failed to fetch detail '%s': %s",
                detail_url, e,
            )
            return record

        # 解析详情页
        self._extract_meta_fields(html, record)
        self._extract_content(html, record, detail_url)
        self._extract_slides(html, record, detail_url)
        self._extract_figures(html, record, detail_url)
        self._extract_attachments(html, record, detail_url)
        self._extract_tags(html, record)
        self._extract_external_links(html, record, detail_url)

        return record

    def _detail_request(self):
        request = self._template.detail_request
        extra_headers = self.on_request_headers(0)
        if not extra_headers:
            return request
        return request.model_copy(update={
            "headers": {**request.headers, **extra_headers},
        })

    # ── Meta 信息 ──────────────────────────────────────────────

    @staticmethod
    def _extract_meta_fields(html: str, record: dict) -> None:
        """提取 meta 信息：发布日期、机构、电头。"""
        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return

        # 发布日期: div.meta time[datetime]
        time_nodes = tree.cssselect("section.article-detail-content .meta time")
        if time_nodes:
            dt = time_nodes[0].get("datetime", "").strip()
            if dt:
                record["date"] = dt

        # 机构/作者: div.meta ul li
        # 结构: li[0]="Published ...", li[1]="By ..."(作者), li[2]=机构
        meta_lis = tree.cssselect("section.article-detail-content .meta ul li")
        organization = []
        author = ""
        for li in meta_lis:
            text = li.text_content().strip()
            if text.startswith("Published"):
                continue
            if text.startswith("By "):
                author = text[3:].strip()
                continue
            if text:
                organization.append(text)

        record["organization"] = organization
        if author:
            record["author"] = author

        # 电头: strong.article-detail-dateline
        dateline_nodes = tree.cssselect("strong.article-detail-dateline")
        if dateline_nodes:
            dateline = dateline_nodes[0].text_content().strip()
            # 去掉末尾的 -- 和 &nbsp;
            dateline = re.sub(r"\s*--\s*$", "", dateline).strip()
            if dateline:
                record["dateline"] = dateline

    # ── 正文 ────────────────────────────────────────────────────

    @staticmethod
    def _extract_content(html: str, record: dict, detail_url: str) -> None:
        """提取正文 HTML，从中分离 meta/dateline，提取图片并映射占位符。"""
        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return

        # 找到正文容器
        content_node = None
        for selector in _CONTENT_SELECTORS:
            nodes = tree.cssselect(selector)
            if nodes:
                content_node = nodes[0]
                break

        if content_node is None:
            # fallback 纯文本
            fallback = SscNewsAdapter._extract_content_fallback(html)
            if fallback:
                record["content"] = fallback
            return

        # 移除 meta div 和 dateline，只保留正文
        content_clone = copy_clone(content_node)
        for meta_div in content_clone.cssselect("div.meta"):
            meta_div.getparent().remove(meta_div)
        for dateline in content_clone.cssselect("strong.article-detail-dateline"):
            dateline.drop_tree()

        # 提取正文中的图片，替换为占位符
        images = []
        img_idx = 0
        for img in content_clone.cssselect("img"):
            src = img.get("src") or img.get("data-src") or ""
            if not src or src.startswith("data:"):
                continue
            if "/emoji/" in src or "emoji" in src.lower():
                continue

            full_url = urljoin(detail_url, src.strip())
            placeholder = f"{{{{img_{img_idx}}}}}"
            alt = (img.get("alt") or "").strip()
            images.append({
                "url": full_url,
                "placeholder": placeholder,
                "alt": alt,
            })
            img.set("src", placeholder)
            img_idx += 1

        content_html = etree.tostring(content_clone, encoding="unicode", method="html")
        record["content_html"] = content_html.strip()

        if images:
            record["images"] = images

    @staticmethod
    def _extract_content_fallback(html: str) -> str:
        """Fallback extraction for small SSC markup changes."""
        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return ""

        for selector in _CONTENT_SELECTORS:
            for node in tree.cssselect(selector):
                text = " ".join(node.text_content().split())
                if len(text) >= 80:
                    return text
        return ""

    # ── Slides (轮播图) ────────────────────────────────────────

    @staticmethod
    def _extract_slides(html: str, record: dict, detail_url: str) -> None:
        """提取轮播图，caption 只取 p（跳过重复的 h1）。"""
        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return

        content_nodes = SscNewsAdapter._find_content_nodes(tree)
        slides: list[dict] = []
        seen: set[str] = set()

        for slide_li in tree.cssselect("ul.slides > li.slide"):
            figure = slide_li.cssselect("figure.article-detail-gallery")
            if not figure:
                continue
            fig = figure[0]

            # 检查是否在正文内（避免重复提取）
            if SscNewsAdapter._is_inside_any(fig, content_nodes):
                continue

            img = fig.cssselect("img.poster")
            if not img:
                continue

            raw_src = (
                img[0].get("src")
                or img[0].get("data-src")
                or SscNewsAdapter._first_srcset_url(img[0].get("srcset", ""))
            )
            if not raw_src:
                continue

            media_url = urljoin(detail_url, raw_src.strip())
            if media_url in seen:
                continue
            seen.add(media_url)

            slide: dict[str, str] = {"url": media_url, "type": "image"}

            # Caption: 只取 figcaption > p，跳过 h1（重复标题）
            captions = fig.cssselect("figcaption.wip-fb-caption p")
            if captions:
                caption_text = " ".join(captions[0].text_content().split()).strip()
                if caption_text:
                    slide["caption"] = caption_text

            # Alt
            alt = (img[0].get("alt") or "").strip()
            if alt and alt != slide.get("caption"):
                slide["alt"] = alt

            slides.append(slide)

        if slides:
            record["slides"] = slides

    # ── 正文内图片 (figures) ────────────────────────────────────

    @staticmethod
    def _extract_figures(html: str, record: dict, detail_url: str) -> None:
        """提取正文内的 figure/img（不与 slides 重复）。"""
        # 如果 images 已从 content_html 提取，跳过
        if record.get("images"):
            return

        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return

        figures: list[dict] = []
        seen: set[str] = set()

        for content_node in SscNewsAdapter._find_content_nodes(tree):
            for img in content_node.cssselect("img"):
                raw_src = (
                    img.get("src")
                    or img.get("data-src")
                    or SscNewsAdapter._first_srcset_url(img.get("srcset", ""))
                )
                if not raw_src or raw_src.startswith("data:"):
                    continue

                media_url = urljoin(detail_url, raw_src.strip())
                if media_url in seen:
                    continue
                seen.add(media_url)

                fig: dict[str, str] = {"url": media_url, "type": "image"}
                alt = (img.get("alt") or "").strip()
                if alt:
                    fig["alt"] = alt
                figures.append(fig)

        if figures:
            record["figures"] = figures

    # ── 附件 ────────────────────────────────────────────────────

    @staticmethod
    def _extract_attachments(html: str, record: dict, detail_url: str) -> None:
        """提取 PDF 等附件链接。"""
        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return

        attachments: list[dict] = []
        seen: set[str] = set()

        for content_node in SscNewsAdapter._find_content_nodes(tree):
            for link in content_node.cssselect("a[href]"):
                raw_url = (link.get("href") or "").strip()
                if not raw_url:
                    continue

                file_url = urljoin(detail_url, raw_url)
                if not SscNewsAdapter._is_attachment_url(file_url) or file_url in seen:
                    continue
                seen.add(file_url)

                label = " ".join(link.text_content().split())
                extension = SscNewsAdapter._attachment_extension(file_url).lstrip(".")
                attachment: dict[str, str] = {
                    "url": file_url,
                    "type": extension or "file",
                }
                if label:
                    attachment["label"] = label
                attachments.append(attachment)

        if attachments:
            record["attachments"] = attachments

    # ── 标签 ────────────────────────────────────────────────────

    @staticmethod
    def _extract_tags(html: str, record: dict) -> None:
        """提取 article-detail-tag 标签。"""
        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return

        tags = []
        for tag_a in tree.cssselect("a.article-detail-tag"):
            tag_text = tag_a.text_content().strip()
            if tag_text:
                tags.append(tag_text)

        if tags:
            record["tags"] = tags

        # 判断是否为 press release
        if "Press Release" in tags:
            record["link_type"] = "press_release"
        else:
            record["link_type"] = "news"

    # ── 外链 ────────────────────────────────────────────────────

    @staticmethod
    def _extract_external_links(html: str, record: dict, detail_url: str) -> None:
        """从正文 HTML 提取外链。"""
        content_html = record.get("content_html", "")
        if not content_html:
            return

        adapter = SscNewsAdapter.__new__(SscNewsAdapter)
        adapter.__init__("https://www.ssc.spaceforce.mil")
        external_links = [
            link
            for link in adapter.extract_external_links(content_html, detail_url)
            if not SscNewsAdapter._is_attachment_url(link)
        ]
        if external_links:
            record["external_links"] = external_links

    # ── 工具方法 ────────────────────────────────────────────────

    @staticmethod
    def _find_content_nodes(tree: Any) -> list[Any]:
        for selector in _CONTENT_SELECTORS:
            nodes = tree.cssselect(selector)
            if nodes:
                return nodes
        return []

    @staticmethod
    def _is_inside_any(node: Any, containers: list[Any]) -> bool:
        current = node
        while current is not None:
            if any(current is container for container in containers):
                return True
            current = current.getparent()
        return False

    @staticmethod
    def _is_attachment_url(url: str) -> bool:
        return bool(SscNewsAdapter._attachment_extension(url))

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

    def on_request_headers(self, page: int) -> dict[str, str]:
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
        error_str = str(error)
        if "404" in error_str:
            return "skip"
        if "403" in error_str:
            logger.warning("[SscNews] 403 Forbidden, may need different approach")
            return "skip"
        return None


def copy_clone(node: Any) -> Any:
    """深拷贝 lxml 元素节点。"""
    return deepcopy_node(node)


def deepcopy_node(node: Any) -> Any:
    """深拷贝 lxml 元素。"""
    from copy import deepcopy
    return deepcopy(node)
