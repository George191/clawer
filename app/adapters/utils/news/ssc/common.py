"""SSC Newsroom / Press Releases 共享 HTML 解析逻辑。"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import etree
from lxml import html as lxml_html

from app.adapters.utils.news import NewsBaseAdapter, _ATTACHMENT_EXTENSIONS


def extract_meta_fields(html: str, record: dict) -> None:
    """提取 SSC 详情页 meta 信息：发布日期、机构、作者、电头。"""
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return

    time_nodes = tree.cssselect("section.article-detail-content .meta time")
    if time_nodes:
        dt = time_nodes[0].get("datetime", "").strip()
        if dt:
            record["date"] = dt

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

    if organization:
        record["organization"] = organization
    if author:
        record["author"] = author

    dateline_nodes = tree.cssselect("strong.article-detail-dateline")
    if dateline_nodes:
        dateline = dateline_nodes[0].text_content().strip()
        dateline = re.sub(r"\s*--\s*$", "", dateline).strip()
        if dateline:
            record["dateline"] = dateline


def extract_content(html: str, record: dict, detail_url: str, content_field_selector: str) -> None:
    """提取正文容器 HTML，并同步清洗图片占位符。"""
    if not content_field_selector:
        return

    try:
        tree = lxml_html.fromstring(html)
        nodes = tree.cssselect(content_field_selector)
    except Exception:
        return
    if not nodes:
        return

    content_clone = _deepcopy_node(nodes[0])
    for meta_div in content_clone.cssselect("div.meta"):
        parent = meta_div.getparent()
        if parent is not None:
            parent.remove(meta_div)
    for dateline in content_clone.cssselect("strong.article-detail-dateline"):
        dateline.drop_tree()

    images: list[dict[str, str]] = []
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

    content_html = etree.tostring(content_clone, encoding="unicode", method="html").strip()
    if content_html:
        record["content_html"] = content_html

    if images:
        record["images"] = images


def extract_slides(html: str, record: dict, detail_url: str, content_field_selector: str) -> None:
    """提取轮播图，caption 只取 p。"""
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return

    content_nodes = _find_content_nodes(tree, content_field_selector)
    slides: list[dict[str, str]] = []
    seen: set[str] = set()

    for slide_li in tree.cssselect("ul.slides > li.slide"):
        figure = slide_li.cssselect("figure.article-detail-gallery")
        if not figure:
            continue
        fig = figure[0]

        if _is_inside_any(fig, content_nodes):
            continue

        img = fig.cssselect("img.poster")
        if not img:
            continue

        raw_src = (
            img[0].get("src")
            or img[0].get("data-src")
            or _first_srcset_url(img[0].get("srcset", ""))
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


def extract_figures(html: str, record: dict, detail_url: str, content_field_selector: str) -> None:
    """提取正文内的 figure/img（不与 slides 重复）。"""
    if record.get("images"):
        return

    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return

    figures: list[dict] = []
    seen: set[str] = set()

    for content_node in _find_content_nodes(tree, content_field_selector):
        for img in content_node.cssselect("img"):
            raw_src = (
                img.get("src")
                or img.get("data-src")
                or _first_srcset_url(img.get("srcset", ""))
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


def extract_attachments(html: str, record: dict, detail_url: str, content_field_selector: str) -> None:
    """提取 PDF 等附件链接。"""
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return

    attachments: list[dict] = []
    seen: set[str] = set()

    for content_node in _find_content_nodes(tree, content_field_selector):
        for link in content_node.cssselect("a[href]"):
            raw_url = (link.get("href") or "").strip()
            if not raw_url:
                continue

            file_url = urljoin(detail_url, raw_url)
            if not _is_attachment_url(file_url) or file_url in seen:
                continue
            seen.add(file_url)

            label = " ".join(link.text_content().split())
            extension = _attachment_extension(file_url).lstrip(".")
            attachment: dict[str, str] = {
                "url": file_url,
                "type": extension or "file",
            }
            if label:
                attachment["label"] = label
            attachments.append(attachment)

    if attachments:
        record["attachments"] = attachments


def extract_tags(html: str, record: dict) -> None:
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


def extract_external_links(adapter: NewsBaseAdapter, record: dict, detail_url: str) -> None:
    """从 SSC 正文 HTML 提取外链。"""
    content_html = str(record.get("content_html") or "").strip()
    if not content_html:
        return

    external_links = [
        link
        for link in adapter.extract_external_links(content_html, detail_url)
        if not _is_attachment_url(link)
    ]
    if external_links:
        existing = record.get("external_links") or []
        merged: list[str] = []
        seen: set[str] = set()
        for url in [*existing, *external_links]:
            if url in seen:
                continue
            seen.add(url)
            merged.append(url)
        record["external_links"] = merged


def _find_content_nodes(tree: Any, content_field_selector: str) -> list[Any]:
    if not content_field_selector:
        return []
    try:
        nodes = tree.cssselect(content_field_selector)
    except Exception:
        return []
    return nodes if nodes else []


def _is_inside_any(node: Any, containers: list[Any]) -> bool:
    current = node
    while current is not None:
        if any(current is container for container in containers):
            return True
        current = current.getparent()
    return False


def _is_attachment_url(url: str) -> bool:
    return bool(_attachment_extension(url))


def _attachment_extension(url: str) -> str:
    path = urlparse(url).path.lower()
    for extension in _ATTACHMENT_EXTENSIONS:
        if path.endswith(extension):
            return extension
    return ""


def _first_srcset_url(srcset: str) -> str:
    if not srcset:
        return ""
    return srcset.split(",", 1)[0].strip().split(" ", 1)[0].strip()


def _deepcopy_node(node: Any) -> Any:
    return deepcopy(node)
