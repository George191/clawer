"""Generic media extraction helpers shared by all news adapters.

WordPress adapters re-export these functions for backwards compatibility, but
the implementation is intentionally independent of WordPress.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from lxml import etree

_NAV_PATTERNS = re.compile(
    r"^(mailto:|tel:|javascript:|#|/login|/signup|/subscribe|/rss|/feed)",
    re.IGNORECASE,
)

def clean_url(url: str) -> str:
    """Return an absolute HTTP(S) URL without fragments."""
    value = str(url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if parsed.query:
        clean += f"?{parsed.query}"
    return clean


def _dedupe_by_url(items: list[Any]) -> list[Any]:
    deduped: list[Any] = []
    seen: set[str] = set()
    for item in items:
        url = item if isinstance(item, str) else item.get("url") if isinstance(item, dict) else ""
        clean = clean_url(url)
        if clean and clean not in seen:
            seen.add(clean)
            if isinstance(item, dict):
                item = dict(item)
                item["url"] = clean
            else:
                item = clean
            deduped.append(item)
    return deduped


def _caption(node: Any) -> str:
    figure = node.xpath("ancestor::figure[1]")
    if figure:
        captions = figure[0].cssselect("figcaption")
        if captions:
            return re.sub(r"\s+", " ", captions[0].text_content()).strip()
    return ""


def _url_identity(url: str) -> str:
    try:
        parsed = urlparse(url)
        path = unicodedata.normalize("NFC", unquote(parsed.path))
        query = unicodedata.normalize("NFC", unquote(parsed.query))
        return parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            path=path,
            query=query,
            fragment="",
        ).geturl()
    except Exception:
        return unicodedata.normalize("NFC", unquote(str(url or "")))


def _nodes_html(nodes: Any) -> str:
    html = "".join(
        etree.tostring(node, encoding="unicode", method="html") for node in nodes
    )
    return html.strip()


def _extract_resource_nodes(
    nodes: list[Any],
    base_url: str,
    *,
    kind: str,
    placeholder_prefix: str,
    source_getter: Any = None,
    skip: Any = None,
    extra_fields: Any = None,
) -> list[dict[str, str]]:
    items = []
    placeholders = {}
    for node in nodes:
        if skip and skip(node):
            continue
        raw = source_getter(node) if source_getter else (node.get("src") or "").strip()
        if not raw or (raw.startswith("{{") and raw.endswith("}}")):
            continue
        url = clean_url(urljoin(base_url, raw))
        if not url:
            continue
        if url in placeholders:
            node.set("src", placeholders[url])
            node.attrib.pop("srcset", None)
            node.attrib.pop("data-src", None)
            continue
        item = {"url": url, "placeholder": f"{{{{{placeholder_prefix}_{len(items)}}}}}", "type": kind}
        if extra_fields:
            extra_fields(node, item)
        items.append(item)
        placeholders[url] = item["placeholder"]
        node.set("src", item["placeholder"])
        for attr in ("srcset", "data-src", "data-video-url", "data-url"):
            node.attrib.pop(attr, None)
    return _dedupe_by_url(items)


def extract_images_from_nodes(nodes: Any, base_url: str) -> list[dict[str, str]]:
    def _image_src(img: Any) -> str:
        srcset = (img.get("srcset") or "").strip()
        first = srcset.split(",", 1)[0].strip().split(" ", 1)[0] if srcset else ""
        return (img.get("src") or img.get("data-src") or first or "").strip()

    def _has_slide_ancestor(img: Any) -> bool:
        node = img
        while node is not None:
            if node.get("data-spider-slide") == "1":
                return True
            node = node.getparent()
        return False
    
    def _is_ignored_image(src: str) -> bool:
        return (
            not src
            or src.startswith("data:")
            or (src.startswith("{{") and src.endswith("}}"))
            or "/emoji/" in src
            or "emoji" in src.lower()
        )

    def fields(node: Any, item: dict[str, str]) -> None:
        item["alt"] = (node.get("alt") or "").strip()
        if caption := _caption(node):
            item["caption"] = caption
    return _extract_resource_nodes(
        nodes.cssselect("img"), base_url, kind="image", placeholder_prefix="img",
        source_getter=_image_src,
        skip=lambda node: _has_slide_ancestor(node) or _is_ignored_image(_image_src(node)),
        extra_fields=fields,
    )


def extract_anchor_links_from_nodes(
    nodes: Any,
    base_url: str,
    excluded_urls: set[str] | None = None,
    included_urls: set[str] | None = None,
) -> list[dict[str, str]]:
    out = []
    placeholders = {}
    excluded_keys = {_url_identity(url) for url in (excluded_urls or set())}
    included_keys = {_url_identity(url) for url in (included_urls or set())}
    for link in nodes.cssselect("a[href]"):
        url = clean_url(urljoin(base_url, (link.get("href") or "").strip()))
        identity = _url_identity(url)
        if (
            not url
            or identity in excluded_keys
            or (included_urls is not None and identity not in included_keys)
        ):
            continue
        if identity in placeholders:
            link.set("href", placeholders[identity])
            continue
        item = {
            "url": url,
            "placeholder": f"{{{{attachment_{len(out)}}}}}",
            "type": "link",
        }
        label = re.sub(r"\s+", " ", link.text_content()).strip()
        if label:
            item["label"] = label
        out.append(item)
        placeholders[identity] = item["placeholder"]
        link.set("href", item["placeholder"])
    return _dedupe_by_url(out)


def _extract_media_from_nodes(
    nodes: list[Any],
    base_url: str,
    *,
    kind: str,
    placeholder_prefix: str,
) -> list[dict[str, str]]:
    def fields(node: Any, item: dict[str, str]) -> None:
        cap = _caption(node)
        if node.get("type"):
            item["media_type"] = node.get("type").strip()
        if node.get("title") and kind == "embed":
            item["label"] = re.sub(r"\s+", " ", node.get("title")).strip()
        if cap:
            item["caption"] = cap
    return _extract_resource_nodes(nodes, base_url, kind=kind, placeholder_prefix=placeholder_prefix, extra_fields=fields)


def extract_videos_from_nodes(nodes: Any, base_url: str) -> list[dict[str, str]]:
    return _extract_media_from_nodes(
        nodes.cssselect("video[src], video source[src]"),
        base_url,
        kind="video",
        placeholder_prefix="video",
    )


def extract_audios_from_nodes(nodes: Any, base_url: str) -> list[dict[str, str]]:
    return _extract_media_from_nodes(
        nodes.cssselect("audio[src], audio source[src]"),
        base_url,
        kind="audio",
        placeholder_prefix="audio",
    )


def extract_iframes_from_nodes(nodes: Any, base_url: str) -> list[dict[str, str]]:
    return _extract_media_from_nodes(
        nodes.cssselect("iframe[src]"),
        base_url,
        kind="embed",
        placeholder_prefix="iframe",
    )


def extract_videos_from_html(html: str, base_url: str) -> list[dict[str, str]]:
    from lxml import html as lxml_html

    if not html:
        return []
    try:
        nodes = lxml_html.fragment_fromstring(html, create_parent="div")
    except Exception:
        return []
    return extract_videos_from_nodes(nodes, base_url)


def extract_iframes_from_html(html: str, base_url: str) -> list[dict[str, str]]:
    from lxml import html as lxml_html

    if not html:
        return []
    try:
        nodes = lxml_html.fragment_fromstring(html, create_parent="div")
    except Exception:
        return []
    return extract_iframes_from_nodes(nodes, base_url)


def extract_anchor_links(
    html: str,
    base_url: str,
    *,
    external_only: bool = False,
    site_domain: str = "",
) -> list[dict[str, str]]:
    from lxml import html as lxml_html

    if not html:
        return []
    try:
        nodes = lxml_html.fragment_fromstring(html, create_parent="div")
    except Exception:
        return []
    items = extract_anchor_links_from_nodes(nodes, base_url)
    if external_only:
        site_domain = site_domain.lower().removeprefix("www.")
        items = [
            item for item in items
            if (domain := urlparse(item["url"]).netloc.lower().removeprefix("www."))
            and domain != site_domain
        ]
    return items


def process_content(record: dict[str, Any], base_url: str, content_field: str = "content_html") -> None:
    from copy import deepcopy
    from lxml import html as lxml_html

    content = str(record.get(content_field) or "").strip()
    if not content:
        for key in ("images", "videos", "audios", "iframe", "attachments"):
            if isinstance(record.get(key), list) and not record[key]:
                record.pop(key, None)
        record.pop("external_links", None)
        return
    try:
        nodes = lxml_html.fragment_fromstring(content, create_parent="div")
    except Exception:
        return
    preview = deepcopy(nodes)
    extracted = {
        "images": extract_images_from_nodes(preview, base_url),
        "videos": extract_videos_from_nodes(preview, base_url),
        "audios": extract_audios_from_nodes(preview, base_url),
        "iframe": extract_iframes_from_nodes(preview, base_url),
    }
    record[content_field] = _nodes_html(nodes)
    for key, values in extracted.items():
        merged = list(record.get(key)) if isinstance(record.get(key), list) else []
        merged.extend(values)
        if merged:
            record[key] = _dedupe_by_url(merged)
        else:
            record.pop(key, None)
    links = [item["url"] for item in extract_anchor_links(content, base_url)]
    if links:
        record["external_links"] = _dedupe_by_url(
            list(record.get("external_links") or []) + links
        )
    else:
        record.pop("external_links", None)
