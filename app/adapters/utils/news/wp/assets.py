"""WordPress 新闻站正文和媒体资源工具。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import etree

from app.adapters.utils.news import NewsBaseAdapter
from app.downloader.http_client import HttpClient
from app.models.template import RequestConfig

logger = logging.getLogger(__name__)


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
            or first_srcset_url(img.get("srcset", ""))
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
        ext = attachment_extension(file_url)
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


async def process_content_html(
    adapter: NewsBaseAdapter,
    record: dict[str, Any],
    base_url: str,
) -> None:
    """处理 content_html：图片、附件和外链。"""
    content_html = str(record.get("content_html") or "").strip()
    if not content_html:
        return

    images, normalized_html = extract_images_from_html(content_html, base_url)
    if images:
        record["images"] = images
        content_html = normalized_html
        record["content_html"] = normalized_html

    attachments = extract_attachment_links(content_html, base_url)
    if attachments:
        record["attachments"] = attachments

    adapter.merge_external_links_from_content(record, base_url)


async def wp_request_json(
    client: HttpClient,
    base_url: str,
    url: str,
) -> Any:
    """发起 WordPress REST API JSON 请求。"""
    config = RequestConfig(
        headers={
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Referer": f"{base_url}/",
            "Cache-Control": "no-cache",
        },
        encoding="utf-8",
    )
    text = await client.request_page(url, config, anti_crawl_enabled=False)
    return json.loads(text)


async def fetch_wp_media_url(
    client: HttpClient,
    base_url: str,
    media_id: int,
    cache: dict[int, str],
) -> str:
    """通过 WP Media API 获取封面图 URL。"""
    if not media_id:
        return ""
    if media_id in cache:
        return cache[media_id]

    try:
        url = (
            f"{base_url}/wp-json/wp/v2/media/{media_id}"
            f"?_fields=source_url,media_details.sizes.full.source_url"
        )
        data = await wp_request_json(client, base_url, url)
    except Exception as e:
        logger.debug("[wp.assets] Media %d fetch failed: %s", media_id, str(e)[:80])
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


async def enrich_cover_images_batch(
    client: HttpClient,
    base_url: str,
    records: list[dict[str, Any]],
    cache: dict[int, str],
) -> None:
    """批量并行获取封面图 URL。"""
    pending = [
        (record, int(record.get("featured_media") or 0))
        for record in records
        if record.get("featured_media")
    ]
    if not pending:
        return

    async def _fetch_one(record: dict[str, Any], media_id: int) -> None:
        cover_url = await fetch_wp_media_url(client, base_url, media_id, cache)
        if cover_url:
            record["cover_image"] = cover_url
            record.setdefault("thumbnail", cover_url)

    await asyncio.gather(*(_fetch_one(record, mid) for record, mid in pending))


def cleanup_wp_fields(record: dict[str, Any]) -> None:
    """清理 WordPress API 中间字段。"""
    for key in (
        "excerpt", "excerpt_html",
        "featured_media",
        "category_ids", "tag_ids",
        "source_ids",
        "external_url",
    ):
        record.pop(key, None)


def attachment_extension(url: str) -> str:
    path = urlparse(url).path.lower()
    for extension in (
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".csv", ".txt", ".zip", ".rar", ".7z", ".json", ".xml",
        ".kml", ".kmz", ".geojson", ".gdb", ".gpkg",
    ):
        if path.endswith(extension):
            return extension
    return ""


def first_srcset_url(srcset: str) -> str:
    if not srcset:
        return ""
    return srcset.split(",", 1)[0].strip().split(" ", 1)[0].strip()
