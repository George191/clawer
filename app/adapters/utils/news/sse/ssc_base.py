"""SSC (Space Systems Command) 基础适配器。

SSC Newsroom 和 Media Room 的详情页共用同一套模板（DNN ArticleCS），
本基类封装共用的详情页解析逻辑：

  - _extract_meta_fields:   发布日期、机构、作者、电头
  - _extract_content:       正文 HTML + 图片占位符
  - _extract_slides:        轮播图
  - _extract_figures:       正文内图片
  - _extract_attachments:   PDF 等附件
  - _extract_tags:          标签
  - _extract_external_links: 外链

详情页结构:
  article.article-detail
    header > h1                          → 标题
    section.article-detail-content
      div.meta > ul > li                 → 发布日期 + 作者 + 机构
      strong.article-detail-dateline     → 电头 (地点, 如 "EL SEGUNDO, Calif. --")
      正文 HTML
    footer > a.article-detail-tag        → 标签 (USSF, Press Release 等)

  ul.slides > li.slide > figure.article-detail-gallery
    img.poster                           → 图片
    figcaption.wip-fb-caption > p        → 图片说明 (h1 是重复标题，跳过)
    div.actions a.download-url           → 高清下载链接
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import etree
from lxml import html as lxml_html

from app.adapters.utils.news import (
    NewsBaseAdapter,
    _ATTACHMENT_EXTENSIONS,
    _CONTENT_SELECTORS,
)

logger = logging.getLogger(__name__)


class SscBaseAdapter(NewsBaseAdapter):
    """SSC 站点共用适配器基类。

    子类只需关注列表页解析逻辑，详情页解析由基类统一处理。
    """

    # ── 详情页解析 ──────────────────────────────────────────────

    @staticmethod
    def _extract_meta_fields(html: str, record: dict) -> None:
        """提取 meta 信息：发布日期、机构、作者、电头。"""
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
            dateline = re.sub(r"\s*--\s*$", "", dateline).strip()
            if dateline:
                record["dateline"] = dateline

    @staticmethod
    def _extract_content(html: str, record: dict, detail_url: str) -> None:
        """提取正文 HTML，从中分离 meta/dateline，提取图片并映射占位符。"""
        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return

        content_node = None
        for selector in _CONTENT_SELECTORS:
            nodes = tree.cssselect(selector)
            if nodes:
                content_node = nodes[0]
                break

        if content_node is None:
            fallback = SscBaseAdapter._extract_content_fallback(html)
            if fallback:
                record["content"] = fallback
            return

        # 移除 meta div 和 dateline，只保留正文
        content_clone = _deepcopy_node(content_node)
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

    @staticmethod
    def _extract_slides(html: str, record: dict, detail_url: str) -> None:
        """提取轮播图，caption 只取 p（跳过重复的 h1）。"""
        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return

        content_nodes = SscBaseAdapter._find_content_nodes(tree)
        slides: list[dict] = []
        seen: set[str] = set()

        for slide_li in tree.cssselect("ul.slides > li.slide"):
            figure = slide_li.cssselect("figure.article-detail-gallery")
            if not figure:
                continue
            fig = figure[0]

            if SscBaseAdapter._is_inside_any(fig, content_nodes):
                continue

            img = fig.cssselect("img.poster")
            if not img:
                continue

            raw_src = (
                img[0].get("src")
                or img[0].get("data-src")
                or SscBaseAdapter._first_srcset_url(img[0].get("srcset", ""))
            )
            if not raw_src:
                continue

            media_url = urljoin(detail_url, raw_src.strip())
            if media_url in seen:
                continue
            seen.add(media_url)

            slide: dict[str, str] = {"url": media_url, "type": "image"}

            captions = fig.cssselect("figcaption.wip-fb-caption p")
            if captions:
                caption_text = " ".join(captions[0].text_content().split()).strip()
                if caption_text:
                    slide["caption"] = caption_text

            alt = (img[0].get("alt") or "").strip()
            if alt and alt != slide.get("caption"):
                slide["alt"] = alt

            slides.append(slide)

        if slides:
            record["slides"] = slides

    @staticmethod
    def _extract_figures(html: str, record: dict, detail_url: str) -> None:
        """提取正文内的 figure/img（不与 slides 重复）。"""
        if record.get("images"):
            return

        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return

        figures: list[dict] = []
        seen: set[str] = set()

        for content_node in SscBaseAdapter._find_content_nodes(tree):
            for img in content_node.cssselect("img"):
                raw_src = (
                    img.get("src")
                    or img.get("data-src")
                    or SscBaseAdapter._first_srcset_url(img.get("srcset", ""))
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

    @staticmethod
    def _extract_attachments(html: str, record: dict, detail_url: str) -> None:
        """提取 PDF 等附件链接。"""
        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return

        attachments: list[dict] = []
        seen: set[str] = set()

        for content_node in SscBaseAdapter._find_content_nodes(tree):
            for link in content_node.cssselect("a[href]"):
                raw_url = (link.get("href") or "").strip()
                if not raw_url:
                    continue

                file_url = urljoin(detail_url, raw_url)
                if not SscBaseAdapter._is_attachment_url(file_url) or file_url in seen:
                    continue
                seen.add(file_url)

                label = " ".join(link.text_content().split())
                extension = SscBaseAdapter._attachment_extension(file_url).lstrip(".")
                attachment: dict[str, str] = {
                    "url": file_url,
                    "type": extension or "file",
                }
                if label:
                    attachment["label"] = label
                attachments.append(attachment)

        if attachments:
            record["attachments"] = attachments

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

    @staticmethod
    def _extract_external_links(_html: str, record: dict, detail_url: str) -> None:
        """从正文 HTML 提取外链。"""
        content_html = record.get("content_html", "")
        if not content_html:
            return

        adapter = SscBaseAdapter.__new__(SscBaseAdapter)
        adapter.__init__("https://www.ssc.spaceforce.mil")
        external_links = [
            link
            for link in adapter.extract_external_links(content_html, detail_url)
            if not SscBaseAdapter._is_attachment_url(link)
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
        return bool(SscBaseAdapter._attachment_extension(url))

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


def _deepcopy_node(node: Any) -> Any:
    """深拷贝 lxml 元素。"""
    from copy import deepcopy
    return deepcopy(node)
