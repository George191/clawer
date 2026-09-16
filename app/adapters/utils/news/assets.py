"""Generic media extraction helpers shared by all news adapters.

WordPress adapters re-export these functions for backwards compatibility, but
the implementation is intentionally independent of WordPress.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import etree

_ATTACHMENT_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".csv", ".txt", ".zip", ".rar", ".7z", ".json", ".xml",
    ".kml", ".kmz", ".geojson", ".gdb", ".gpkg",
)
_IMAGE_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg",
    ".tif", ".tiff", ".avif", ".ico",
)
_VIDEO_EXTENSIONS = (".mp4", ".webm", ".ogg", ".ogv", ".mov", ".m4v", ".avi", ".mkv")


def _base():
    # Lazy import avoids a cycle when NewsBaseAdapter delegates to this module.
    from app.adapters.utils.news import NewsBaseAdapter
    return NewsBaseAdapter


def _image_src(img: Any) -> str:
    srcset = (img.get("srcset") or "").strip()
    first = srcset.split(",", 1)[0].strip().split(" ", 1)[0] if srcset else ""
    return (img.get("src") or img.get("data-src") or first or "").strip()


def _is_ignored_image(src: str) -> bool:
    return not src or src.startswith("data:") or "/emoji/" in src or "emoji" in src.lower()


def _caption(node: Any) -> str:
    figure = node.xpath("ancestor::figure[1]")
    if figure:
        captions = figure[0].cssselect("figcaption")
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
    html = "".join(etree.tostring(c, encoding="unicode", method="html") for c in wrapper)
    return (html or etree.tostring(wrapper, encoding="unicode", method="html")).strip()


def extract_images_from_wrapper(wrapper: Any, base_url: str) -> list[dict[str, str]]:
    Base = _base(); images = []; placeholders = {}
    for img in wrapper.cssselect("img"):
        if _has_slide_ancestor(img):
            continue
        raw = _image_src(img)
        if _is_ignored_image(raw):
            continue
        url = Base.clean_url(urljoin(base_url, raw))
        if not url:
            continue
        if url in placeholders:
            img.set("src", placeholders[url]); img.attrib.pop("srcset", None); img.attrib.pop("data-src", None)
            continue
        item = {"url": url, "placeholder": f"{{{{img_{len(images)}}}}}", "alt": (img.get("alt") or "").strip()}
        cap = _caption(img)
        if cap: item["caption"] = cap
        images.append(item); placeholders[url] = item["placeholder"]
        img.set("src", item["placeholder"]); img.attrib.pop("srcset", None); img.attrib.pop("data-src", None)
    return Base.dedupe_media_items(images)


def extract_images_from_html(html: str, base_url: str) -> tuple[list[dict[str, str]], str]:
    from lxml import html as lxml_html
    if not html: return [], html
    try: wrapper = lxml_html.fragment_fromstring(html, create_parent="div")
    except Exception: return [], html
    return extract_images_from_wrapper(wrapper, base_url), _wrapper_html(wrapper)


def attachment_extension(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in _ATTACHMENT_EXTENSIONS:
        if path.endswith(ext): return ext
    return ""


def image_extension(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in _IMAGE_EXTENSIONS:
        if path.endswith(ext): return ext
    return ""


def is_attachment_url(url: str) -> bool:
    return bool(attachment_extension(url))


def is_image_url(url: str) -> bool:
    return bool(image_extension(url))


def video_extension(url: str) -> str:
    path = urlparse(url).path.lower()
    for ext in _VIDEO_EXTENSIONS:
        if path.endswith(ext): return ext
    return ""


def is_video_url(url: str) -> bool:
    return bool(video_extension(url))


def extract_attachment_links(html: str, base_url: str) -> list[dict[str, str]]:
    from lxml import html as lxml_html
    if not html: return []
    try: wrapper = lxml_html.fragment_fromstring(html, create_parent="div")
    except Exception: return []
    Base = _base(); out = []; seen = set()
    for link in wrapper.cssselect("a[href]"):
        url = Base.clean_url(urljoin(base_url, (link.get("href") or "").strip()))
        ext = attachment_extension(url)
        if not url or is_image_url(url) or not ext or url in seen: continue
        seen.add(url); item = {"url": url, "type": ext.lstrip(".")}
        label = re.sub(r"\s+", " ", link.text_content()).strip()
        if label: item["label"] = label
        out.append(item)
    return Base.dedupe_media_items(out)


def extract_video_links(wrapper: Any, base_url: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    Base = _base(); videos = []; embeds = []
    def media(node, kind):
        raw = (node.get("src") or node.get("data-src") or node.get("data-video-url") or node.get("data-url") or "").strip()
        url = Base.clean_url(urljoin(base_url, raw))
        if not url: return None
        item = {"url": url, "type": kind}; cap = _caption(node)
        if node.get("type"): item["media_type"] = node.get("type").strip()
        if node.get("title") and kind == "embed": item["label"] = re.sub(r"\s+", " ", node.get("title")).strip()
        if cap: item["caption"] = cap
        return item
    for node in wrapper.cssselect("video, video source"):
        item = media(node, "video")
        if item: videos.append(item)
    for node in wrapper.cssselect("iframe"):
        item = media(node, "embed")
        if item: embeds.append(item)
    return Base.dedupe_media_items(videos), Base.dedupe_media_items(embeds)


async def process_content_html(adapter: Any, record: dict[str, Any], base_url: str) -> None:
    from lxml import html as lxml_html
    content = str(record.get("content_html") or "").strip()
    if not content: return
    try: wrapper = lxml_html.fragment_fromstring(content, create_parent="div")
    except Exception: return
    Base = _base(); images = extract_images_from_wrapper(wrapper, base_url)
    if images: record["images"] = Base.dedupe_media_items(images)
    videos, embeds = extract_video_links(wrapper, base_url)
    if videos: record["videos"] = videos
    if embeds: record["video_embeds"] = embeds
    record["content_html"] = _wrapper_html(wrapper)
    attachments = extract_attachment_links(content, base_url)
    if attachments: record["attachments"] = Base.dedupe_media_items(attachments)
    adapter.merge_external_links_from_content(record, base_url)
