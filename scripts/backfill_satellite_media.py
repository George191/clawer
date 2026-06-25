"""Backfill Satellite Today WordPress cover media.

Most legacy Satellite Today rows already contain the cover image in
``images``/``content_html``. Resolve those locally first, then use the
WordPress media API only for the small remainder.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from motor.motor_asyncio import AsyncIOMotorClient

from app.config.settings import settings
from app.downloader.http_client import HttpClient
from app.models.template import RequestConfig

logger = logging.getLogger(__name__)

TEMPLATE_NAME = "satellite_today"
BASE_URL = "https://www.satellitetoday.com"
MEDIA_API_TPL = (
    f"{BASE_URL}/wp-json/wp/v2/media/{{media_id}}"
    "?_fields=source_url,media_details.sizes.full.source_url"
)
BACKFILL_FIELD = "media_backfill"
FAILED_STATUSES = ("failed", "not_found")

FetchResult = tuple[dict[str, Any] | None, str, str]

IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
SRC_RE = re.compile(r"\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)


def _cover_doc(source_url: str) -> dict[str, Any]:
    return {"source_url": source_url}


def _first_string(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_embedded_cover(doc: dict[str, Any]) -> str:
    embedded = doc.get("_embedded")
    if not isinstance(embedded, dict):
        return ""
    media_items = embedded.get("wp:featuredmedia")
    if not isinstance(media_items, list) or not media_items:
        return ""
    first = media_items[0]
    if not isinstance(first, dict):
        return ""
    return _first_string(
        first.get("source_url"),
        (((first.get("media_details") or {}).get("sizes") or {}).get("full") or {}).get("source_url"),
    )


def _extract_image_cover(doc: dict[str, Any]) -> tuple[str, str]:
    media_id = doc.get("featured_media")
    images = doc.get("images") or []
    if not isinstance(media_id, int) or media_id <= 0:
        return "", "invalid_media_id"
    if not isinstance(images, list) or not images:
        return "", "no_images"

    image_items = [image for image in images if isinstance(image, dict)]
    by_placeholder = {
        str(image.get("placeholder")): str(image.get("url"))
        for image in image_items
        if image.get("placeholder") and image.get("url")
    }
    html = str(doc.get("content_html") or "")
    markers = (
        f"wp-image-{media_id}",
        f"attachment_{media_id}",
        f"attachment-{media_id}",
    )
    if html:
        for match in IMG_TAG_RE.finditer(html):
            tag = match.group(0)
            if not any(marker in tag for marker in markers):
                continue
            src_match = SRC_RE.search(tag)
            if src_match:
                src = src_match.group(1)
                if src in by_placeholder:
                    return by_placeholder[src], "matched_placeholder"
                if src.startswith("http"):
                    return src, "matched_src"
            if len(image_items) == 1:
                url = _first_string(image_items[0].get("url"))
                if url:
                    return url, "matched_single_fallback"

    if len(image_items) == 1:
        url = _first_string(image_items[0].get("url"))
        if url:
            return url, "single_image_fallback"

    return "", "multi_no_match"


def _extract_local_cover(doc: dict[str, Any]) -> tuple[str, str]:
    url = _extract_embedded_cover(doc)
    if url:
        return url, "embedded"

    direct_url = _first_string(doc.get("image_url"), doc.get("thumbnail"))
    if direct_url:
        return direct_url, "direct_field"

    return _extract_image_cover(doc)


def _media_request_config() -> RequestConfig:
    return RequestConfig(
        headers={
            "Accept": "application/json, */*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{BASE_URL}/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        },
    )


def _unresolved_filter(*, retry_failed: bool) -> dict[str, Any]:
    query: dict[str, Any] = {
        "featured_media": {"$type": "int", "$gt": 0},
    }
    if not retry_failed:
        query[f"{BACKFILL_FIELD}.status"] = {"$nin": list(FAILED_STATUSES)}
    return query


def _failure_doc(media_id: int, status: str, reason: str) -> dict[str, Any]:
    return {
        "status": status,
        "media_id": media_id,
        "reason": reason[:500],
        "updated_at": datetime.now(timezone.utc),
    }


def _success_update(media_obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "$set": {"featured_media": media_obj},
        "$unset": {BACKFILL_FIELD: ""},
    }


def _reset_download_status_update(media_obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "$set": {
            "featured_media": media_obj,
            "_meta.download_status": "pending",
        },
        "$unset": {BACKFILL_FIELD: ""},
    }


async def backfill_from_local_fields(
    collection: Any,
    *,
    dry_run: bool,
    batch_size: int,
) -> dict[str, int]:
    stats = {
        "scanned": 0,
        "updated": 0,
        "reset_download_status": 0,
        "embedded": 0,
        "direct_field": 0,
        "matched_placeholder": 0,
        "matched_src": 0,
        "matched_single_fallback": 0,
        "single_image_fallback": 0,
        "invalid_media_id": 0,
        "no_images": 0,
        "multi_no_match": 0,
    }
    query = {"featured_media": {"$type": "int", "$gt": 0}}
    projection = {
        "featured_media": 1,
        "_embedded": 1,
        "image_url": 1,
        "thumbnail": 1,
        "images": 1,
        "content_html": 1,
        "_meta.download_status": 1,
    }
    cursor = collection.find(query, projection=projection).batch_size(batch_size)

    async for doc in cursor:
        stats["scanned"] += 1
        source_url, reason = _extract_local_cover(doc)
        stats[reason] = stats.get(reason, 0) + 1
        if not source_url:
            continue

        media_obj = _cover_doc(source_url)
        download_status = ((doc.get("_meta") or {}).get("download_status") or "")
        update = (
            _reset_download_status_update(media_obj)
            if download_status in {"no_assets", "failed"}
            else _success_update(media_obj)
        )

        if dry_run:
            modified_count = 1
        else:
            result = await collection.update_one({"_id": doc["_id"]}, update)
            modified_count = result.modified_count

        stats["updated"] += int(modified_count)
        if download_status in {"no_assets", "failed"}:
            stats["reset_download_status"] += int(modified_count)

    return stats


def _extract_status_code(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    if response is not None:
        response_status = getattr(response, "status_code", None)
        if isinstance(response_status, int):
            return response_status
    return None


def _mask_proxy(proxy_url: str | None) -> str:
    if not proxy_url:
        return "direct"
    return re.sub(r"//([^:@/]+):([^@/]+)@", "//***:***@", proxy_url)


async def fetch_media_url(
    client: HttpClient,
    media_id: int,
    semaphore: asyncio.Semaphore,
    cache: dict[int, FetchResult],
    *,
    max_attempts: int,
    request_timeout: float,
    anti_crawl_enabled: bool,
    request_delay_min: float,
    request_delay_max: float,
) -> FetchResult:
    if media_id in cache:
        return cache[media_id]

    url = MEDIA_API_TPL.format(media_id=media_id)
    last_error = ""

    async with semaphore:
        for attempt in range(1, max_attempts + 1):
            try:
                text = await asyncio.wait_for(
                    client.request_page(
                        url,
                        _media_request_config(),
                        anti_crawl_enabled=anti_crawl_enabled,
                    ),
                    timeout=request_timeout,
                )
                data = json.loads(text)
                if isinstance(data, dict) and isinstance(data.get("source_url"), str):
                    data.setdefault("id", media_id)
                    result: FetchResult = (data, "ok", "")
                    cache[media_id] = result
                    logger.info(
                        "Media %d resolved via API proxy=%s",
                        media_id,
                        _mask_proxy(getattr(client, "_last_proxy_url", None)),
                    )
                    return result

                status = None
                if isinstance(data, dict) and isinstance(data.get("data"), dict):
                    status = data["data"].get("status")
                if status == 404:
                    result = (None, "not_found", "WordPress media API returned 404")
                    cache[media_id] = result
                    return result

                last_error = f"unexpected response: {str(data)[:300]}"

            except asyncio.TimeoutError:
                last_error = f"timeout after {request_timeout}s"
            except Exception as exc:
                status_code = _extract_status_code(exc)
                last_error = f"status={status_code} {str(exc)}" if status_code else str(exc)
                if "404" in last_error:
                    result = (None, "not_found", last_error)
                    cache[media_id] = result
                    return result

            if attempt < max_attempts:
                delay = random.uniform(request_delay_min, request_delay_max)
                await asyncio.sleep(max(delay, min(attempt * 2, 5)))

    result = (None, "failed", last_error or "unknown failure")
    cache[media_id] = result
    return result


async def backfill(
    *,
    dry_run: bool = False,
    batch_size: int = 100,
    concurrency: int = 1,
    max_attempts: int = 2,
    request_timeout: float = 12.0,
    retry_failed: bool = False,
    anti_crawl_enabled: bool = False,
    local_only: bool = True,
    limit_media: int = 0,
    request_delay_min: float = 4.0,
    request_delay_max: float = 9.0,
) -> dict[str, int]:
    mongo_client = AsyncIOMotorClient(settings.db_url)
    db = mongo_client[settings.db_name]
    collection = db[TEMPLATE_NAME]
    http_client = HttpClient()

    stats = {
        "total": 0,
        "local_scanned": 0,
        "local_updated": 0,
        "local_reset_download_status": 0,
        "media_total": 0,
        "media_checked": 0,
        "updated": 0,
        "failed": 0,
        "not_found": 0,
        "remaining_retryable": 0,
        "remaining_raw": 0,
    }

    try:
        await collection.create_index("featured_media")
        await collection.create_index(f"{BACKFILL_FIELD}.status")

        local_stats = await backfill_from_local_fields(
            collection,
            dry_run=dry_run,
            batch_size=batch_size,
        )
        stats["local_scanned"] = local_stats["scanned"]
        stats["local_updated"] = local_stats["updated"]
        stats["local_reset_download_status"] = local_stats["reset_download_status"]
        logger.info(
            "Local backfill: scanned=%d updated=%d reset_download_status=%d "
            "matched_placeholder=%d single_image_fallback=%d no_images=%d multi_no_match=%d",
            local_stats["scanned"],
            local_stats["updated"],
            local_stats["reset_download_status"],
            local_stats.get("matched_placeholder", 0),
            local_stats.get("single_image_fallback", 0),
            local_stats.get("no_images", 0),
            local_stats.get("multi_no_match", 0),
        )

        if local_only:
            stats["remaining_retryable"] = await collection.count_documents(
                _unresolved_filter(retry_failed=False),
            )
            stats["remaining_raw"] = await collection.count_documents(
                {"featured_media": {"$type": "int", "$gt": 0}},
            )
            return stats

        query = _unresolved_filter(retry_failed=retry_failed)
        stats["total"] = await collection.count_documents(query)
        logger.info("Found %d retryable records with unresolved featured_media", stats["total"])
        if stats["total"] == 0:
            stats["remaining_raw"] = await collection.count_documents(
                {"featured_media": {"$type": "int", "$gt": 0}},
            )
            return stats

        media_ids = [
            media_id
            for media_id in await collection.distinct("featured_media", query)
            if isinstance(media_id, int) and media_id > 0
        ]
        media_ids.sort()
        if limit_media > 0:
            media_ids = media_ids[:limit_media]
        stats["media_total"] = len(media_ids)
        logger.info("Processing %d unique media IDs", len(media_ids))

        semaphore = asyncio.Semaphore(concurrency)
        cache: dict[int, FetchResult] = {}
        stats_lock = asyncio.Lock()

        for start in range(0, len(media_ids), batch_size):
            batch = media_ids[start:start + batch_size]
            await _process_media_batch(
                collection,
                http_client,
                semaphore,
                cache,
                batch,
                dry_run=dry_run,
                stats=stats,
                stats_lock=stats_lock,
                max_attempts=max_attempts,
                request_timeout=request_timeout,
                anti_crawl_enabled=anti_crawl_enabled,
                request_delay_min=request_delay_min,
                request_delay_max=request_delay_max,
            )
            logger.info(
                "Progress: media=%d/%d updated=%d failed=%d not_found=%d",
                stats["media_checked"],
                stats["media_total"],
                stats["updated"],
                stats["failed"],
                stats["not_found"],
            )

        stats["remaining_retryable"] = await collection.count_documents(
            _unresolved_filter(retry_failed=False),
        )
        stats["remaining_raw"] = await collection.count_documents(
            {"featured_media": {"$type": "int", "$gt": 0}},
        )
        return stats
    finally:
        await http_client.close()
        mongo_client.close()


async def _process_media_batch(
    collection: Any,
    client: HttpClient,
    semaphore: asyncio.Semaphore,
    cache: dict[int, FetchResult],
    media_ids: list[int],
    *,
    dry_run: bool,
    stats: dict[str, int],
    stats_lock: asyncio.Lock,
    max_attempts: int,
    request_timeout: float,
    anti_crawl_enabled: bool,
    request_delay_min: float,
    request_delay_max: float,
) -> None:
    await asyncio.gather(*(
        _process_media_id(
            collection,
            client,
            semaphore,
            cache,
            media_id,
            dry_run=dry_run,
            stats=stats,
            stats_lock=stats_lock,
            max_attempts=max_attempts,
            request_timeout=request_timeout,
            anti_crawl_enabled=anti_crawl_enabled,
            request_delay_min=request_delay_min,
            request_delay_max=request_delay_max,
        )
        for media_id in media_ids
    ))


async def _process_media_id(
    collection: Any,
    client: HttpClient,
    semaphore: asyncio.Semaphore,
    cache: dict[int, FetchResult],
    media_id: int,
    *,
    dry_run: bool,
    stats: dict[str, int],
    stats_lock: asyncio.Lock,
    max_attempts: int,
    request_timeout: float,
    anti_crawl_enabled: bool,
    request_delay_min: float,
    request_delay_max: float,
) -> None:
    media_obj, status, reason = await fetch_media_url(
        client,
        media_id,
        semaphore,
        cache,
        max_attempts=max_attempts,
        request_timeout=request_timeout,
        anti_crawl_enabled=anti_crawl_enabled,
        request_delay_min=request_delay_min,
        request_delay_max=request_delay_max,
    )

    if media_obj is not None:
        if dry_run:
            modified_count = await collection.count_documents({"featured_media": media_id})
        else:
            result = await collection.update_many(
                {"featured_media": media_id},
                _reset_download_status_update(media_obj),
            )
            modified_count = result.modified_count
        async with stats_lock:
            stats["media_checked"] += 1
            stats["updated"] += int(modified_count)
        return

    failure = _failure_doc(media_id, status, reason)
    if dry_run:
        matched_count = await collection.count_documents({"featured_media": media_id})
    else:
        result = await collection.update_many(
            {"featured_media": media_id},
            {"$set": {BACKFILL_FIELD: failure}},
        )
        matched_count = result.matched_count

    async with stats_lock:
        stats["media_checked"] += 1
        if status == "not_found":
            stats["not_found"] += int(matched_count)
        else:
            stats["failed"] += int(matched_count)
    logger.warning("Media %d marked %s for %d records: %s", media_id, status, matched_count, reason[:200])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill satellite_today featured_media from WordPress media IDs",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview only, do not write to MongoDB")
    parser.add_argument("--batch-size", type=int, default=500, help="Mongo scan batch size / remote media IDs per batch")
    parser.add_argument("--remote", action="store_true", help="Also call the WordPress media API for rows that cannot be resolved locally")
    parser.add_argument("--concurrency", type=int, default=1, help="Concurrent remote media API requests")
    parser.add_argument("--max-attempts", type=int, default=2, help="Attempts per media ID")
    parser.add_argument("--request-timeout", type=float, default=12.0, help="Seconds per media API attempt")
    parser.add_argument("--retry-failed", action="store_true", help="Retry records previously marked failed")
    parser.add_argument("--anti-crawl", action="store_true", help="Use anti-crawl headers/proxy layer")
    parser.add_argument("--limit-media", type=int, default=0, help="Limit unique media IDs this run")
    parser.add_argument("--request-delay-min", type=float, default=4.0, help="Minimum delay between remote API retries")
    parser.add_argument("--request-delay-max", type=float, default=9.0, help="Maximum delay between remote API retries")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [BACKFILL] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stats = asyncio.run(backfill(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        concurrency=args.concurrency,
        max_attempts=args.max_attempts,
        request_timeout=args.request_timeout,
        retry_failed=args.retry_failed,
        anti_crawl_enabled=args.anti_crawl,
        local_only=not args.remote,
        limit_media=args.limit_media,
        request_delay_min=args.request_delay_min,
        request_delay_max=args.request_delay_max,
    ))

    logger.info(
        "Done: local_scanned=%d local_updated=%d local_reset_download_status=%d "
        "remote_total=%d media_total=%d media_checked=%d remote_updated=%d failed=%d "
        "not_found=%d remaining_retryable=%d remaining_raw=%d",
        stats["local_scanned"],
        stats["local_updated"],
        stats["local_reset_download_status"],
        stats["total"],
        stats["media_total"],
        stats["media_checked"],
        stats["updated"],
        stats["failed"],
        stats["not_found"],
        stats["remaining_retryable"],
        stats["remaining_raw"],
    )


if __name__ == "__main__":
    main()
