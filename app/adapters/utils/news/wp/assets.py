"""WordPress news media helpers."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.adapters.utils.news.assets import process_content_html
from app.downloader.http_client import HttpClient
from app.logger import get_adapter_logger

logger = get_adapter_logger(__name__, "wp_assets")


def _normalize_featured_media(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    normalized = {
        key: value[key]
        for key in ("source_url", "date", "author", "mime_type")
        if value.get(key) not in (None, "")
    }
    details = value.get("media_details")
    if isinstance(details, dict):
        normalized_details = {
            key: details[key]
            for key in ("width", "height", "filesize", "sizes")
            if details.get(key) not in (None, "")
        }
        if normalized_details:
            normalized["media_details"] = normalized_details
    return normalized or None


def _extract_embedded_author(record: dict[str, Any]) -> dict[str, Any] | None:
    embedded = record.get("_embedded")
    if not isinstance(embedded, dict):
        return None
    authors = embedded.get("author")
    if not isinstance(authors, list) or not authors or not isinstance(authors[0], dict):
        return None

    source = authors[0]
    author = {
        key: source[key]
        for key in ("id", "name", "link")
        if source.get(key) not in (None, "")
    }
    avatars = source.get("avatar_urls")
    if isinstance(avatars, dict):
        avatar = next(
            (avatars.get(size) for size in ("96", "48", "24") if avatars.get(size)),
            None,
        )
        if avatar:
            author["avatar"] = avatar
    return author or None


def _normalize_author(record: dict[str, Any]) -> dict[str, Any] | None:
    embedded_author = _extract_embedded_author(record)
    if embedded_author:
        return embedded_author

    author: dict[str, Any] = {}
    author_id = record.get("author_id")
    if author_id not in (None, "", 0):
        author["id"] = author_id
    existing = record.get("author")
    if isinstance(existing, str) and existing.strip():
        author["name"] = existing.strip()
    return author or None


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
    """从 _embedded.wp:featuredmedia 提取精简封面图对象。

    WP REST API 加 _embed=1 后返回的结构：
        "_embedded": {
            "wp:featuredmedia": [
                {"source_url": "https://.../cover.jpg", "media_details": {...}}
            ]
        }
    返回仅包含下载与业务需要字段的 media 对象。
    """
    embedded = record.get("_embedded")
    if not isinstance(embedded, dict):
        return None
    media_list = embedded.get("wp:featuredmedia")
    if not isinstance(media_list, list) or not media_list:
        return None
    first = media_list[0]
    if isinstance(first, dict) and first.get("source_url"):
        return _normalize_featured_media(first)
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


def _extract_og_image_media(record: dict[str, Any]) -> dict[str, Any] | None:
    """Map Yoast og_image metadata to the common featured_media shape."""
    og = record.get("og_image")
    if og is None:
        yoast = record.get("yoast_head_json")
        if isinstance(yoast, dict):
            og = yoast.get("og_image")
    if isinstance(og, list):
        og = og[0] if og else None
    if isinstance(og, str):
        og = {"url": og}
    if not isinstance(og, dict) or not og.get("url"):
        return None

    media: dict[str, Any] = {"source_url": og["url"]}
    if og.get("type"):
        media["mime_type"] = og["type"]
    details = {
        key: og[key]
        for key in ("width", "height")
        if og.get(key) not in (None, "")
    }
    if details:
        media["media_details"] = details
    return media


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
        og_media = _extract_og_image_media(record)
        if og_media:
            record["featured_media"] = og_media
            continue
        # 3. 兜底：media API
        media_id = int(record.get("featured_media") or 0)
        if media_id:
            pending.append((record, media_id))
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
            record["featured_media"] = (
                _normalize_featured_media(cover_obj) or cover_obj
            )

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
    author = _normalize_author(record)
    if author:
        record["author"] = author

    featured_media = _normalize_featured_media(record.get("featured_media"))
    if featured_media:
        record["featured_media"] = featured_media

    for key in (
        "category_ids", "tag_ids", "source_ids", "author_id", "_embedded",
        "og_image", "yoast_head_json",
    ):
        record.pop(key, None)
