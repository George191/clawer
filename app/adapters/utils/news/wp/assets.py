"""WordPress news media helpers."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.adapters.utils.news.assets import process_content_html
from app.downloader.http_client import HttpClient
from app.logger import get_adapter_logger

logger = get_adapter_logger(__name__, "wp_assets")


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
