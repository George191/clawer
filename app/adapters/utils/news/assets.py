"""Generic media extraction helpers shared by all news adapters.

WordPress adapters re-export these functions for backwards compatibility, but
the implementation is intentionally independent of WordPress.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import etree


def _base():
    # Lazy import avoids a cycle when NewsBaseAdapter delegates to this module.
    from app.adapters.utils.news import NewsBaseAdapter

    return NewsBaseAdapter


def _image_src(img: Any) -> str:
    srcset = (img.get("srcset") or "").strip()
    first = srcset.split(",", 1)[0].strip().split(" ", 1)[0] if srcset else ""
    return (img.get("src") or img.get("data-src") or first or "").strip()


def _is_ignored_image(src: str) -> bool:
    return (
        not src
        or src.startswith("data:")
        or (src.startswith("{{") and src.endswith("}}"))
        or "/emoji/" in src
        or "emoji" in src.lower()
    )


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
    html = "".join(
        etree.tostring(c, encoding="unicode", method="html") for c in wrapper
    )
    return (html or etree.tostring(wrapper, encoding="unicode", method="html")).strip()


def extract_images_from_wrapper(wrapper: Any, base_url: str) -> list[dict[str, str]]:
    Base = _base()
    images = []
    placeholders = {}
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
            img.set("src", placeholders[url])
            img.attrib.pop("srcset", None)
            img.attrib.pop("data-src", None)
            continue
        item = {
            "url": url,
            "placeholder": f"{{{{img_{len(images)}}}}}",
            "alt": (img.get("alt") or "").strip(),
        }
        cap = _caption(img)
        if cap:
            item["caption"] = cap
        images.append(item)
        placeholders[url] = item["placeholder"]
        img.set("src", item["placeholder"])
        img.attrib.pop("srcset", None)
        img.attrib.pop("data-src", None)
    return Base.dedupe_media_items(images)


def extract_images_from_html(
    html: str, base_url: str
) -> tuple[list[dict[str, str]], str]:
    from lxml import html as lxml_html

    if not html:
        return [], html
    try:
        wrapper = lxml_html.fragment_fromstring(html, create_parent="div")
    except Exception:
        return [], html
    return extract_images_from_wrapper(wrapper, base_url), _wrapper_html(wrapper)


def url_extension(url: str) -> str:
    path = urlparse(url).path.lower()
    filename = path.rsplit("/", 1)[-1]
    match = re.search(r"(\.[a-z0-9]{1,16})$", filename)
    return match.group(1) if match else ""


def attachment_extension(url: str) -> str:
    return url_extension(url)


def is_attachment_url(url: str) -> bool:
    return bool(attachment_extension(url))


def extract_attachments_from_wrapper(
    wrapper: Any,
    base_url: str,
    excluded_urls: set[str] | None = None,
    included_urls: set[str] | None = None,
) -> list[dict[str, str]]:
    Base = _base()
    out = []
    placeholders = {}
    excluded_urls = excluded_urls or set()
    included_urls = included_urls or set()
    for link in wrapper.cssselect("a[href]"):
        url = Base.clean_url(urljoin(base_url, (link.get("href") or "").strip()))
        ext = attachment_extension(url)
        if not url or url in excluded_urls or (not ext and url not in included_urls):
            continue
        if url in placeholders:
            link.set("href", placeholders[url])
            continue
        item = {
            "url": url,
            "placeholder": f"{{{{attachment_{len(out)}}}}}",
            "type": ext.lstrip(".") or "link",
        }
        label = re.sub(r"\s+", " ", link.text_content()).strip()
        if label:
            item["label"] = label
        out.append(item)
        placeholders[url] = item["placeholder"]
        link.set("href", item["placeholder"])
    return Base.dedupe_media_items(out)


def extract_attachment_links(html: str, base_url: str) -> list[dict[str, str]]:
    from lxml import html as lxml_html

    if not html:
        return []
    try:
        wrapper = lxml_html.fragment_fromstring(html, create_parent="div")
    except Exception:
        return []
    return extract_attachments_from_wrapper(wrapper, base_url)


def _extract_media_from_nodes(
    nodes: list[Any],
    base_url: str,
    *,
    kind: str,
    placeholder_prefix: str,
) -> list[dict[str, str]]:
    Base = _base()
    items = []
    placeholders = {}
    for node in nodes:
        raw = (node.get("src") or "").strip()
        if raw.startswith("{{") and raw.endswith("}}"):
            continue
        url = Base.clean_url(urljoin(base_url, raw))
        if not url:
            continue
        if url in placeholders:
            node.set("src", placeholders[url])
            for attr in ("data-src", "data-video-url", "data-url"):
                node.attrib.pop(attr, None)
            continue
        item = {
            "url": url,
            "placeholder": f"{{{{{placeholder_prefix}_{len(items)}}}}}",
            "type": kind,
        }
        cap = _caption(node)
        if node.get("type"):
            item["media_type"] = node.get("type").strip()
        if node.get("title") and kind == "embed":
            item["label"] = re.sub(r"\s+", " ", node.get("title")).strip()
        if cap:
            item["caption"] = cap
        items.append(item)
        placeholders[url] = item["placeholder"]
        node.set("src", item["placeholder"])
        for attr in ("data-src", "data-video-url", "data-url"):
            node.attrib.pop(attr, None)
    return Base.dedupe_media_items(items)


def extract_videos_from_wrapper(wrapper: Any, base_url: str) -> list[dict[str, str]]:
    return _extract_media_from_nodes(
        wrapper.cssselect("video[src], video source[src]"),
        base_url,
        kind="video",
        placeholder_prefix="video",
    )


def extract_iframes_from_wrapper(wrapper: Any, base_url: str) -> list[dict[str, str]]:
    return _extract_media_from_nodes(
        wrapper.cssselect("iframe[src]"),
        base_url,
        kind="embed",
        placeholder_prefix="iframe",
    )


async def process_content_html(
    adapter: Any, record: dict[str, Any], base_url: str
) -> None:
    from lxml import html as lxml_html

    content = str(record.get("content_html") or "").strip()
    if not content:
        return
    try:
        wrapper = lxml_html.fragment_fromstring(content, create_parent="div")
    except Exception:
        return
    Base = _base()
    candidate_external_urls = set(adapter.extract_external_links(content, base_url))
    images = extract_images_from_wrapper(wrapper, base_url)
    if images:
        record["images"] = Base.dedupe_media_items(images)
    videos = extract_videos_from_wrapper(wrapper, base_url)
    if videos:
        record["videos"] = videos
    iframe = extract_iframes_from_wrapper(wrapper, base_url)
    if iframe:
        record["iframe"] = iframe
    tagged_media_urls = {
        str(item.get("url") or "")
        for items in (images, videos, iframe)
        for item in items
        if isinstance(item, dict)
    }
    attachments = extract_attachments_from_wrapper(
        wrapper,
        base_url,
        excluded_urls=tagged_media_urls,
        included_urls=candidate_external_urls,
    )
    if attachments:
        record["attachments"] = Base.dedupe_media_items(attachments)
    record["content_html"] = _wrapper_html(wrapper)
    record.pop("external_links", None)
