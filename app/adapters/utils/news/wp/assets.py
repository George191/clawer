"""WordPress news media helpers."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import etree
from tenacity import retry, stop_after_attempt, wait_exponential, stop_never

from app.adapters.utils.news import NewsBaseAdapter
from app.downloader.http_client import HttpClient
from app.models.template import RequestConfig

logger = logging.getLogger(__name__)


def _image_src(img: Any) -> str:
    return (
        img.get("src")
        or img.get("data-src")
        or first_srcset_url(img.get("srcset", ""))
        or ""
    ).strip()


def _is_ignored_image(src: str) -> bool:
    return not src or src.startswith("data:") or "/emoji/" in src or "emoji" in src.lower()


def _caption_for_image(img: Any) -> str:
    node = img
    while node is not None and getattr(node, "tag", "").lower() != "figure":
        node = node.getparent()
    if node is not None:
        captions = node.cssselect("figcaption")
        if captions:
            return re.sub(r"\s+", " ", captions[0].text_content()).strip()
    return ""


def _has_slide_ancestor(img: Any) -> bool:
    node = img
    while node is not None:
        if node.get("data-spider-slide") == "1":
            return True
        node = node.getparent()
    return False


def _wrapper_html(wrapper: Any) -> str:
    html = "".join(
        etree.tostring(child, encoding="unicode", method="html")
        for child in wrapper
    )
    if not html:
        html = etree.tostring(wrapper, encoding="unicode", method="html")
    return html.strip()


def extract_images_from_wrapper(wrapper: Any, base_url: str) -> list[dict[str, str]]:
    """Extract non-slide body images from a parsed HTML wrapper."""
    images: list[dict[str, str]] = []

    for img in wrapper.cssselect("img"):
        if _has_slide_ancestor(img):
            continue

        raw_src = _image_src(img)
        if _is_ignored_image(raw_src):
            continue

        image = {
            "url": urljoin(base_url, raw_src),
            "placeholder": f"{{{{img_{len(images)}}}}}",
            "alt": (img.get("alt") or "").strip(),
        }
        caption = _caption_for_image(img)
        if caption:
            image["caption"] = caption
        images.append(image)

        img.set("src", image["placeholder"])
        if "srcset" in img.attrib:
            del img.attrib["srcset"]
        if "data-src" in img.attrib:
            del img.attrib["data-src"]

    return images


def extract_images_from_html(html: str, base_url: str) -> tuple[list[dict[str, str]], str]:
    """Extract body images from HTML and replace src values with placeholders."""
    from lxml import html as lxml_html

    if not html:
        return [], html

    try:
        wrapper = lxml_html.fragment_fromstring(html, create_parent="div")
    except Exception:
        return [], html

    images = extract_images_from_wrapper(wrapper, base_url)
    return images, _wrapper_html(wrapper)


def extract_attachment_links(html: str, base_url: str) -> list[dict[str, str]]:
    """Extract attachment links from HTML."""
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
    """Process content_html into slides, body images, attachments, and links."""
    from lxml import html as lxml_html

    content_html = str(record.get("content_html") or "").strip()
    if not content_html:
        return

    try:
        wrapper = lxml_html.fragment_fromstring(content_html, create_parent="div")
    except Exception:
        return

    images = extract_images_from_wrapper(wrapper, base_url)
    if images:
        record["images"] = images

    record["content_html"] = _wrapper_html(wrapper)

    attachments = extract_attachment_links(content_html, base_url)
    if attachments:
        record["attachments"] = attachments

    adapter.merge_external_links_from_content(record, base_url)

@retry(
    stop=stop_never,
    wait=wait_exponential(multiplier=1, min=1, max=10),
)
async def wp_request_json(
    client: HttpClient,
    url: str,
    anti_crawl_enabled=True
) -> Any:
    """Request JSON from a WordPress REST endpoint."""
    text = await client.request_page(url, anti_crawl_enabled=anti_crawl_enabled)
    return json.loads(text)


async def fetch_wp_media_url(
    client: HttpClient,
    base_url: str,
    media_id: int,
    cache: dict[int, str],
) -> str:
    """Fetch a WordPress media URL by ID."""
    if not media_id:
        return ""
    if media_id in cache:
        return cache[media_id]

    url = (
        f"{base_url}/wp-json/wp/v2/media/{media_id}"
        f"?_fields=source_url,media_details.sizes.full.source_url"
    )
    return await wp_request_json(client, url, anti_crawl_enabled=False)


async def enrich_cover_images_batch(
    client: HttpClient,
    base_url: str,
    records: list[dict[str, Any]],
    cache: dict[int, str],
) -> None:
    """Fetch cover image URLs and write the configured cover aliases."""
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
            record["featured_media"] = cover_url

    await asyncio.gather(*(_fetch_one(record, mid) for record, mid in pending))

def cleanup_wp_fields(record: dict[str, Any]) -> None:
    """Remove WordPress API intermediate fields."""
    for key in (
        "category_ids", "tag_ids", "source_ids",
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
