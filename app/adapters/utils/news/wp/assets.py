"""WordPress news media helpers."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import etree

from app.adapters.utils.news import NewsBaseAdapter
from app.config.settings import settings
from app.downloader.http_client import HttpClient
from app.logger import get_adapter_logger
from app.models.template import RequestConfig

logger = get_adapter_logger(__name__, "wp_assets")


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
    placeholders: dict[str, str] = {}

    for img in wrapper.cssselect("img"):
        if _has_slide_ancestor(img):
            continue

        raw_src = _image_src(img)
        if _is_ignored_image(raw_src):
            continue

        image_url = NewsBaseAdapter.clean_url(urljoin(base_url, raw_src))
        if not image_url:
            continue
        placeholder = placeholders.get(image_url)
        if placeholder:
            img.set("src", placeholder)
            if "srcset" in img.attrib:
                del img.attrib["srcset"]
            if "data-src" in img.attrib:
                del img.attrib["data-src"]
            continue

        image = {
            "url": image_url,
            "placeholder": f"{{{{img_{len(images)}}}}}",
            "alt": (img.get("alt") or "").strip(),
        }
        caption = _caption_for_image(img)
        if caption:
            image["caption"] = caption
        images.append(image)
        placeholders[image_url] = image["placeholder"]

        img.set("src", image["placeholder"])
        if "srcset" in img.attrib:
            del img.attrib["srcset"]
        if "data-src" in img.attrib:
            del img.attrib["data-src"]

    return NewsBaseAdapter.dedupe_media_items(images)


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
        file_url = NewsBaseAdapter.clean_url(urljoin(base_url, href))
        if not file_url:
            continue
        if NewsBaseAdapter.is_image_url(file_url):
            continue
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

    return NewsBaseAdapter.dedupe_media_items(attachments)


def extract_video_links(
    wrapper: Any,
    base_url: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Extract downloadable video sources separately from embedded players."""
    videos: list[dict[str, str]] = []
    embeds: list[dict[str, str]] = []

    for node in wrapper.cssselect("video[src], video source[src]"):
        raw_src = (node.get("src") or "").strip()
        video_url = NewsBaseAdapter.clean_url(urljoin(base_url, raw_src))
        if not video_url:
            continue
        item = {"url": video_url, "type": "video"}
        media_type = (node.get("type") or "").strip()
        if media_type:
            item["media_type"] = media_type
        videos.append(item)

    for node in wrapper.cssselect("iframe[src]"):
        raw_src = (node.get("src") or "").strip()
        embed_url = NewsBaseAdapter.clean_url(urljoin(base_url, raw_src))
        if not embed_url:
            continue
        item = {"url": embed_url, "type": "embed"}
        title = re.sub(r"\s+", " ", node.get("title") or "").strip()
        if title:
            item["label"] = title
        embeds.append(item)

    return (
        NewsBaseAdapter.dedupe_media_items(videos),
        NewsBaseAdapter.dedupe_media_items(embeds),
    )


async def process_content_html(
    adapter: NewsBaseAdapter,
    record: dict[str, Any],
    base_url: str,
) -> None:
    """Process content_html into slides, body images, attachments, and links."""
    from lxml import html as lxml_html

    rid = record.get("id")
    content_html = str(record.get("content_html") or "").strip()
    if not content_html:
        logger.warning("[DEBUG pch] id=%s SKIP (empty content_html)", rid)
        return

    try:
        wrapper = lxml_html.fragment_fromstring(content_html, create_parent="div")
    except Exception as e:
        logger.warning("[DEBUG pch] id=%s lxml FAILED: %s", rid, e)
        return

    images = extract_images_from_wrapper(wrapper, base_url)
    if images:
        record["images"] = NewsBaseAdapter.dedupe_media_items(images)

    videos, video_embeds = extract_video_links(wrapper, base_url)
    if videos:
        record["videos"] = videos
    if video_embeds:
        record["video_embeds"] = video_embeds

    record["content_html"] = _wrapper_html(wrapper)

    attachments = extract_attachment_links(content_html, base_url)
    if attachments:
        record["attachments"] = NewsBaseAdapter.dedupe_media_items(attachments)

    adapter.merge_external_links_from_content(record, base_url)
    logger.warning("[DEBUG pch] id=%s DONE", rid)

async def wp_request_json(
    client: HttpClient,
    url: str,
    anti_crawl_enabled: bool = True,
    adapter_name: str | None = None,
) -> Any:
    """Request JSON from a WordPress REST endpoint.

    无限重试（每次 rotate_proxy 换 IP），不 sleep 立刻下次。
    依赖上层 enrich_cover_images_batch 的 gather 超时兜底，
    防止代理彻底坏掉时 Celery 任务永久挂死。
    """
    attempt = 0
    while True:
        try:
            text = await client.request_page(
                url,
                anti_crawl_enabled=anti_crawl_enabled,
                adapter_name=adapter_name,
                attempt=attempt,
                rotate_proxy=attempt > 0,
            )
            return json.loads(text)
        except Exception as exc:
            attempt += 1
            logger.warning(
                "WordPress JSON request failed, rotating proxy and retrying"
                "(attempt %d): %s",
                attempt,
                exc,
            )


async def fetch_wp_media_url(
    client: HttpClient,
    base_url: str,
    media_id: int,
    cache: dict[int, str],
    adapter_name: str | None = None,
) -> Any:
    """Fetch a WordPress media URL by ID."""
    if not media_id:
        return ""
    if media_id in cache:
        return cache[media_id]

    url = (
        f"{base_url}/wp-json/wp/v2/media/{media_id}"
        f"?_fields=source_url,media_details.sizes.full.source_url"
    )
    return await wp_request_json(
        client,
        url,
        anti_crawl_enabled=True,
        adapter_name=adapter_name,
    )


def _extract_embedded_media(record: dict[str, Any]) -> dict[str, Any] | None:
    """从 _embedded.wp:featuredmedia 提取封面图完整 media 对象。

    WP REST API 加 _embed=1 后返回的结构：
        "_embedded": {
            "wp:featuredmedia": [
                {"source_url": "https://.../cover.jpg", "media_details": {...}}
            ]
        }
    返回第一个 media 对象（dict），与 fetch_wp_media_url 返回结构一致。
    """
    embedded = record.get("_embedded")
    if not isinstance(embedded, dict):
        return None
    media_list = embedded.get("wp:featuredmedia")
    if not isinstance(media_list, list) or not media_list:
        return None
    first = media_list[0]
    if isinstance(first, dict) and first.get("source_url"):
        return first
    return None


def _extract_og_image_url(record: dict[str, Any]) -> str | None:
    """从 yoast_head_json.og_image 提取封面图 URL。

    支持两种数据来源：
    - record["og_image"]: list_fields 映射后的独立字段（selector=yoast_head_json.og_image）
    - record["yoast_head_json"]["og_image"]: 完整 yoast 对象（未在 list_fields 映射时）

    Yoast SEO 的 og_image 结构因版本而异：
    - 字符串: "https://example.com/img.jpg"
    - 字符串数组: ["https://example.com/img.jpg"]
    - 对象数组: [{"url": "https://example.com/img.jpg", "width": 1200, ...}]
    """
    og = record.get("og_image")
    if og is None:
        yoast = record.get("yoast_head_json")
        if isinstance(yoast, dict):
            og = yoast.get("og_image")
    if not og:
        return None
    if isinstance(og, str):
        return og or None
    if isinstance(og, list) and og:
        first = og[0]
        if isinstance(first, str):
            return first or None
        if isinstance(first, dict):
            return first.get("url") or None
    if isinstance(og, dict):
        return og.get("url") or None
    return None


async def enrich_cover_images_batch(
    client: HttpClient,
    base_url: str,
    records: list[dict[str, Any]],
    cache: dict[int, str],
    adapter_name: str | None = None,
) -> None:
    """Fetch cover image URLs and write the configured cover aliases.

    优先级：
    1. _embedded.wp:featuredmedia（WP 默认，_embed=1 时内联，无需额外请求）
    2. yoast_head_json.og_image（Yoast SEO 插件字段，URL 字符串）
    3. media API 兜底（wp-json/wp/v2/media/{id}，依赖代理轮换）
    """
    pending = []
    for record in records:
        # 1. WP 默认：_embedded
        embedded_media = _extract_embedded_media(record)
        if embedded_media:
            record["featured_media"] = embedded_media
            continue
        # 2. Yoast SEO 插件：og_image（list_fields 映射的独立字段 或 yoast_head_json 完整对象）
        og_url = _extract_og_image_url(record)
        if og_url:
            record["featured_media"] = {"source_url": og_url}
            continue
        # 3. 兜底：media API
        media_id = int(record.get("featured_media") or 0)
        if media_id:
            pending.append((record, media_id))
    logger.warning(
        "[DEBUG enrich] total=%d pending_media_api=%d",
        len(records), len(pending),
    )
    if not pending:
        return

    async def _fetch_one(record: dict[str, Any], media_id: int) -> None:
        cover_obj = await fetch_wp_media_url(
            client,
            base_url,
            media_id,
            cache,
            adapter_name,
        )
        if cover_obj:
            record["featured_media"] = cover_obj

    # 兜底超时：wp_request_json 无限重试换 IP 时，整个 gather 可能长时间不返回。
    # 给 10 分钟（约 120 次换 IP 重试）让代理轮换最终命中好 IP，
    # 超时后取消所有子任务并抛出 TimeoutError，防止 Celery 任务永久挂死。
    gather_timeout = 600
    await asyncio.wait_for(
        asyncio.gather(*(_fetch_one(record, mid) for record, mid in pending)),
        timeout=gather_timeout,
    )

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
