"""Backfill Satellite Today featured_media from authoritative WP media API.

Targets:
- records where `featured_media` is still an int;
- optional medium-confidence single-image rows previously filled locally.

For medium-confidence rows, the script first resolves the original media id
from the WordPress post endpoint, then resolves the URL from the media endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from curl_cffi import requests
from motor.motor_asyncio import AsyncIOMotorClient

from app.config.settings import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://www.satellitetoday.com"
COLLECTION = "satellite_today"
IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
SRC_RE = re.compile(r"\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)
CLASS_ID_RE = re.compile(r"wp-image-(\d+)")
ATTACH_ID_RE = re.compile(r"attachment[_-](\d+)")


def _remote_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    url = value.strip()
    return url if url.startswith(("http://", "https://")) else ""


def _first_string(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_medium_confidence_single_image(doc: dict[str, Any]) -> bool:
    featured_media = doc.get("featured_media")
    if not isinstance(featured_media, dict):
        return False
    source_url = _remote_url(featured_media.get("source_url"))
    if not source_url:
        return False
    images = [item for item in (doc.get("images") or []) if isinstance(item, dict)]
    if len(images) != 1 or _remote_url(images[0].get("url")) != source_url:
        return False
    placeholders = {
        str(item.get("placeholder")): _remote_url(item.get("url"))
        for item in images
        if item.get("placeholder") and item.get("url")
    }
    html = str(doc.get("content_html") or "")
    for match in IMG_TAG_RE.finditer(html):
        tag = match.group(0)
        if not (CLASS_ID_RE.search(tag) or ATTACH_ID_RE.search(tag)):
            continue
        src_match = SRC_RE.search(tag)
        if not src_match:
            continue
        src = src_match.group(1)
        if _remote_url(placeholders.get(src) or src) == source_url:
            return False
    return True


def _media_source_url(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    url = _remote_url(data.get("source_url"))
    if url:
        return url
    media_details = data.get("media_details")
    if isinstance(media_details, dict):
        sizes = media_details.get("sizes")
        if isinstance(sizes, dict):
            full = sizes.get("full")
            if isinstance(full, dict):
                return _remote_url(full.get("source_url"))
    return ""


def _request_json_with_retry(
    session: requests.Session,
    url: str,
    *,
    timeout: float,
    infinite_retry: bool,
    retry_delay: float,
) -> tuple[Any | None, str]:
    attempt = 0
    while True:
        attempt += 1
        try:
            response = session.get(url, timeout=timeout)
            if response.status_code == 404:
                return None, "not_found"
            if response.status_code == 200:
                try:
                    return response.json(), "ok"
                except Exception as exc:
                    error = f"json_error={exc}"
            else:
                error = f"status={response.status_code}"
        except Exception as exc:
            error = str(exc)

        if not infinite_retry:
            return None, error
        logger.warning("Retrying request attempt=%d error=%s url=%s", attempt, error[:200], url)
        time.sleep(retry_delay)


def _fetch_media_url(
    session: requests.Session,
    media_id: int,
    timeout: float,
    *,
    infinite_retry: bool,
    retry_delay: float,
) -> tuple[str, str]:
    url = (
        f"{BASE_URL}/wp-json/wp/v2/media/{media_id}"
        "?_fields=source_url,media_details.sizes.full.source_url"
    )
    data, status = _request_json_with_retry(
        session,
        url,
        timeout=timeout,
        infinite_retry=infinite_retry,
        retry_delay=retry_delay,
    )
    if status != "ok":
        return "", status
    resolved = _media_source_url(data)
    return (resolved, "remote_media") if resolved else ("", "missing_source_url")


def _fetch_post_media_id(
    session: requests.Session,
    post_id: int,
    timeout: float,
    *,
    infinite_retry: bool,
    retry_delay: float,
) -> tuple[int | None, str]:
    url = f"{BASE_URL}/wp-json/wp/v2/posts/{post_id}?_fields=id,featured_media"
    data, status = _request_json_with_retry(
        session,
        url,
        timeout=timeout,
        infinite_retry=infinite_retry,
        retry_delay=retry_delay,
    )
    if status != "ok":
        return None, status
    if isinstance(data, dict) and isinstance(data.get("featured_media"), int):
        media_id = int(data["featured_media"])
        return (media_id, "post_media_id") if media_id > 0 else (None, "empty_media_id")
    return None, "missing_featured_media"


async def backfill(
    *,
    dry_run: bool,
    include_medium_confidence: bool,
    infinite_retry: bool,
    retry_delay: float,
    limit: int,
    timeout: float,
) -> dict[str, int]:
    client = AsyncIOMotorClient(settings.db_url, serverSelectionTimeoutMS=5000)
    col = client[settings.db_name][COLLECTION]
    session = requests.Session(
        impersonate="chrome120",
        timeout=timeout,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Referer": f"{BASE_URL}/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        },
        verify=False,
    )

    stats = {
        "scanned": 0,
        "updated": 0,
        "remote": 0,
        "post_id_resolved": 0,
        "int_targets": 0,
        "medium_targets": 0,
        "unresolved": 0,
    }

    try:
        query: dict[str, Any]
        if include_medium_confidence:
            query = {
                "$or": [
                    {"featured_media": {"$type": "int", "$gt": 0}},
                    {"featured_media.source_url": {"$exists": True, "$ne": ""}},
                ],
            }
        else:
            query = {"featured_media": {"$type": "int", "$gt": 0}}
        projection = {
            "id": 1,
            "featured_media": 1,
            "images": 1,
            "content_html": 1,
            "url": 1,
            "title": 1,
            "_meta.download_status": 1,
        }
        cursor = col.find(query, projection=projection).sort("id", 1)
        if limit > 0:
            cursor = cursor.limit(limit)

        async for doc in cursor:
            featured_media = doc.get("featured_media")
            target_kind = ""
            media_id: int | None = None

            if isinstance(featured_media, int) and featured_media > 0:
                target_kind = "int"
                media_id = int(featured_media)
                stats["int_targets"] += 1
            elif include_medium_confidence and _is_medium_confidence_single_image(doc):
                target_kind = "medium"
                stats["medium_targets"] += 1
                post_id = doc.get("id")
                if not isinstance(post_id, int) or post_id <= 0:
                    stats["unresolved"] += 1
                    logger.warning("Unresolved medium row without post id url=%s", doc.get("url"))
                    continue
                media_id, reason = await asyncio.to_thread(
                    _fetch_post_media_id,
                    session,
                    int(post_id),
                    timeout,
                    infinite_retry=infinite_retry,
                    retry_delay=retry_delay,
                )
                if media_id is None:
                    stats["unresolved"] += 1
                    logger.warning(
                        "Unresolved post_id=%s reason=%s url=%s",
                        post_id,
                        reason,
                        doc.get("url"),
                    )
                    continue
                stats["post_id_resolved"] += 1
            else:
                continue

            stats["scanned"] += 1
            source_url, reason = await asyncio.to_thread(
                _fetch_media_url,
                session,
                int(media_id),
                timeout,
                infinite_retry=infinite_retry,
                retry_delay=retry_delay,
            )

            if not source_url:
                stats["unresolved"] += 1
                logger.warning(
                    "Unresolved kind=%s media_id=%d post_id=%s reason=%s url=%s",
                    target_kind,
                    media_id,
                    doc.get("id"),
                    reason,
                    doc.get("url"),
                )
                continue

            stats["remote"] += 1
            if dry_run:
                continue

            result = await col.update_one(
                {"_id": doc["_id"]},
                {
                    "$set": {
                        "featured_media": {
                            "id": int(media_id),
                            "source_url": source_url,
                            "updated_at": datetime.now(timezone.utc),
                            "backfill_source": "wp_media_api",
                        }
                    }
                },
            )
            stats["updated"] += int(result.modified_count)

    finally:
        session.close()
        client.close()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Satellite Today featured_media URLs")
    parser.add_argument("--apply", action="store_true", help="Write updates to MongoDB")
    parser.add_argument("--dry-run", action="store_true", help="Do not write updates")
    parser.add_argument("--include-medium-confidence", action="store_true", help="Also re-fetch single-image medium-confidence rows")
    parser.add_argument("--finite-retry", action="store_true", help="Do not retry forever on 403/timeouts")
    parser.add_argument("--retry-delay", type=float, default=10.0, help="Delay between infinite retry attempts")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of records to process")
    parser.add_argument("--timeout", type=float, default=20.0, help="Per-request timeout for media endpoint")
    args = parser.parse_args()

    if args.apply and args.dry_run:
        raise SystemExit("--apply and --dry-run are mutually exclusive")

    dry_run = not args.apply if not args.dry_run else True

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [BACKFILL] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stats = asyncio.run(
            backfill(
            dry_run=dry_run,
            include_medium_confidence=args.include_medium_confidence,
            infinite_retry=not args.finite_retry,
            retry_delay=args.retry_delay,
            limit=args.limit,
            timeout=args.timeout,
        )
    )

    logger.info(
        "Done: scanned=%d updated=%d remote=%d int_targets=%d medium_targets=%d "
        "post_id_resolved=%d unresolved=%d",
        stats["scanned"],
        stats["updated"],
        stats["remote"],
        stats["int_targets"],
        stats["medium_targets"],
        stats["post_id_resolved"],
        stats["unresolved"],
    )


if __name__ == "__main__":
    main()
