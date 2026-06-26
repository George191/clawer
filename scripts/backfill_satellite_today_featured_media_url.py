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
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import yaml
from motor.motor_asyncio import AsyncIOMotorClient

from app.anti_crawl import get_proxy_pool
from app.config.settings import settings
from app.downloader.http_client import HttpClient
from app.engine.template_loader import TemplateLoader
from app.models.template import RequestConfig, SiteTemplate

logger = logging.getLogger(__name__)

SOURCE_TEMPLATE_NAME = "satellite_today"
TEMP_TEMPLATE_PREFIX = "_tmp_satellite_today_featured_media_"
BASE_URL = "https://www.satellitetoday.com"
COLLECTION = "satellite_today"
POST_EMBED_FIELDS = "id,featured_media,_embedded.wp:featuredmedia"
IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
SRC_RE = re.compile(r"\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)
CLASS_ID_RE = re.compile(r"wp-image-(\d+)")
ATTACH_ID_RE = re.compile(r"attachment[_-](\d+)")


def _project_template_dir() -> Path:
    template_dir = Path(settings.template_dir)
    if not template_dir.is_absolute():
        template_dir = _PROJECT_ROOT / template_dir
    return template_dir


def _with_post_embed_fields(list_page: str) -> str:
    split = urlsplit(list_page)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query["_embed"] = "1"
    query["_fields"] = POST_EMBED_FIELDS
    encoded = urlencode(query, safe="{},:")
    return urlunsplit((split.scheme, split.netloc, split.path, encoded, split.fragment))


@contextmanager
def _temporary_backfill_template() -> Any:
    source_path = _project_template_dir() / f"{SOURCE_TEMPLATE_NAME}.yaml"
    raw = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Template file must contain a YAML mapping: {source_path}")

    temp_name = f"{TEMP_TEMPLATE_PREFIX}{uuid.uuid4().hex}"
    raw["name"] = temp_name
    raw["display_name"] = f"{raw.get('display_name') or SOURCE_TEMPLATE_NAME} (temporary featured_media backfill)"
    raw["list_page"] = _with_post_embed_fields(str(raw.get("list_page") or ""))

    with tempfile.TemporaryDirectory(prefix="spider_satellite_today_backfill_") as temp_dir:
        temp_path = Path(temp_dir) / f"{temp_name}.yaml"
        temp_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        logger.info("Using temporary template: %s", temp_path)
        template = TemplateLoader(template_dir=temp_dir).load(temp_name)
        try:
            yield template, temp_path
        finally:
            if temp_path.exists():
                temp_path.unlink()


def _request_config_from_template(template: SiteTemplate) -> RequestConfig:
    config = template.list_request.model_copy(deep=True)
    config.headers.setdefault("Accept", "application/json, text/plain, */*")
    config.headers.setdefault("Accept-Language", "en-US,en;q=0.9")
    config.headers.setdefault("Cache-Control", "no-cache")
    config.headers.setdefault("Referer", f"{BASE_URL}/")
    config.headers.setdefault(
        "User-Agent",
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    )
    return config


def _post_embed_fields_from_template(template: SiteTemplate) -> str:
    query = dict(parse_qsl(urlsplit(template.list_page).query, keep_blank_values=True))
    fields = query.get("_fields")
    return fields if fields else POST_EMBED_FIELDS


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


def _embedded_featured_media(data: Any) -> tuple[int | None, str]:
    if not isinstance(data, dict):
        return None, ""
    embedded = data.get("_embedded")
    if not isinstance(embedded, dict):
        return None, ""
    items = embedded.get("wp:featuredmedia")
    if not isinstance(items, list) or not items:
        return None, ""
    first = items[0]
    if not isinstance(first, dict):
        return None, ""
    media_id = first.get("id")
    return (media_id if isinstance(media_id, int) else None), _media_source_url(first)


def _mask_proxy(proxy_url: str | None) -> str:
    if not proxy_url:
        return "direct"
    return re.sub(r"//([^:@/]+):([^@/]+)@", "//***:***@", proxy_url)


def _is_not_found(reason: str) -> bool:
    normalized = reason.lower()
    return "status=404" in normalized or "(status=404)" in normalized or "not found" in normalized


async def _wait_for_proxy(
    proxy_pool: Any,
    *,
    proxy_retry_delay: float,
    wait_seconds: float,
    infinite_retry: bool,
) -> bool:
    deadline = asyncio.get_running_loop().time() + wait_seconds
    first_attempt = True
    while infinite_retry or first_attempt or asyncio.get_running_loop().time() < deadline:
        first_attempt = False
        await proxy_pool.ensure_loaded()
        if proxy_pool.healthy_count > 0:
            return True
        logger.warning(
            "Proxy pool empty, retrying proxy load",
        )
        await proxy_pool.reload()
        if proxy_pool.healthy_count > 0:
            return True
        if not infinite_retry:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(proxy_retry_delay, remaining))
        else:
            await asyncio.sleep(proxy_retry_delay)
    return proxy_pool.healthy_count > 0


async def _request_json(
    http_client: HttpClient,
    url: str,
    *,
    request_config: RequestConfig,
    timeout: float,
    infinite_retry: bool,
    retry_delay: float,
    max_attempts: int,
    proxy_pool: Any | None,
    proxy_wait_seconds: float,
    proxy_retry_delay: float,
) -> tuple[Any | None, str]:
    attempts = 0
    while infinite_retry or attempts < max_attempts:
        attempts += 1
        if proxy_pool is not None:
            proxy_ready = await _wait_for_proxy(
                proxy_pool,
                proxy_retry_delay=proxy_retry_delay,
                wait_seconds=proxy_wait_seconds,
                infinite_retry=infinite_retry,
            )
            if not proxy_ready:
                return None, "proxy_unavailable"

        try:
            text = await asyncio.wait_for(
                http_client.request_page(
                    url,
                    request_config,
                    anti_crawl_enabled=True,
                ),
                timeout=timeout,
            )
            proxy_url = getattr(http_client, "_last_proxy_url", None)
            if proxy_pool is not None and not proxy_url:
                reason = "proxy_not_used"
            else:
                return json.loads(text), "ok"
        except asyncio.TimeoutError:
            proxy_url = getattr(http_client, "_last_proxy_url", None)
            await http_client.mark_last_proxy_failed()
            reason = f"timeout after {timeout}s"
        except json.JSONDecodeError as exc:
            return None, f"json_error={exc}"
        except Exception as exc:
            proxy_url = getattr(http_client, "_last_proxy_url", None)
            reason = str(exc)
            if _is_not_found(reason):
                return None, "not_found"

        if not infinite_retry and attempts >= max_attempts:
            return None, reason

        logger.warning(
            "Retrying request attempt=%d reason=%s proxy=%s url=%s",
            attempts,
            reason[:200],
            _mask_proxy(proxy_url),
            url,
        )
        await asyncio.sleep(retry_delay)

    return None, "max_attempts_exhausted"


async def _fetch_media_url(
    http_client: HttpClient,
    media_id: int,
    timeout: float,
    *,
    request_config: RequestConfig,
    infinite_retry: bool,
    retry_delay: float,
    max_attempts: int,
    proxy_pool: Any | None,
    proxy_wait_seconds: float,
    proxy_retry_delay: float,
) -> tuple[str, str]:
    url = (
        f"{BASE_URL}/wp-json/wp/v2/media/{media_id}"
        "?_fields=source_url,media_details.sizes.full.source_url"
    )
    data, status = await _request_json(
        http_client,
        url,
        request_config=request_config,
        timeout=timeout,
        infinite_retry=infinite_retry,
        retry_delay=retry_delay,
        max_attempts=max_attempts,
        proxy_pool=proxy_pool,
        proxy_wait_seconds=proxy_wait_seconds,
        proxy_retry_delay=proxy_retry_delay,
    )
    if status != "ok":
        return "", status
    resolved = _media_source_url(data)
    return (resolved, "remote_media") if resolved else ("", "missing_source_url")


async def _fetch_post_embedded_media_url(
    http_client: HttpClient,
    post_id: int,
    expected_media_id: int,
    timeout: float,
    *,
    request_config: RequestConfig,
    post_embed_fields: str,
    infinite_retry: bool,
    retry_delay: float,
    max_attempts: int,
    proxy_pool: Any | None,
    proxy_wait_seconds: float,
    proxy_retry_delay: float,
) -> tuple[str, str]:
    url = (
        f"{BASE_URL}/wp-json/wp/v2/posts/{post_id}"
        f"?_embed=1&_fields={post_embed_fields}"
    )
    data, status = await _request_json(
        http_client,
        url,
        request_config=request_config,
        timeout=timeout,
        infinite_retry=infinite_retry,
        retry_delay=retry_delay,
        max_attempts=max_attempts,
        proxy_pool=proxy_pool,
        proxy_wait_seconds=proxy_wait_seconds,
        proxy_retry_delay=proxy_retry_delay,
    )
    if status != "ok":
        return "", status
    if not isinstance(data, dict):
        return "", "invalid_post_response"
    if data.get("featured_media") != expected_media_id:
        return "", "post_media_id_mismatch"
    embedded_media_id, source_url = _embedded_featured_media(data)
    if embedded_media_id is not None and embedded_media_id != expected_media_id:
        return "", "embedded_media_id_mismatch"
    return (source_url, "post_embedded") if source_url else ("", "missing_embedded_source_url")


async def _fetch_post_media_id(
    http_client: HttpClient,
    post_id: int,
    timeout: float,
    *,
    request_config: RequestConfig,
    infinite_retry: bool,
    retry_delay: float,
    max_attempts: int,
    proxy_pool: Any | None,
    proxy_wait_seconds: float,
    proxy_retry_delay: float,
) -> tuple[int | None, str]:
    url = f"{BASE_URL}/wp-json/wp/v2/posts/{post_id}?_fields=id,featured_media"
    data, status = await _request_json(
        http_client,
        url,
        request_config=request_config,
        timeout=timeout,
        infinite_retry=infinite_retry,
        retry_delay=retry_delay,
        max_attempts=max_attempts,
        proxy_pool=proxy_pool,
        proxy_wait_seconds=proxy_wait_seconds,
        proxy_retry_delay=proxy_retry_delay,
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
    max_attempts: int,
    limit: int,
    timeout: float,
    proxy_wait_seconds: float,
    proxy_retry_delay: float,
) -> dict[str, int]:
    client = AsyncIOMotorClient(settings.db_url, serverSelectionTimeoutMS=5000)
    col = client[settings.db_name][COLLECTION]
    http_client = HttpClient()
    proxy_pool: Any | None = None
    stats = {
        "scanned": 0,
        "updated": 0,
        "remote": 0,
        "post_embedded": 0,
        "post_id_resolved": 0,
        "int_targets": 0,
        "medium_targets": 0,
        "unresolved": 0,
        "proxy_enabled": int(bool(proxy_pool)),
        "temp_template_deleted": 0,
    }
    temp_template_ctx: Any | None = None
    temp_template_path: Path | None = None

    try:
        temp_template_ctx = _temporary_backfill_template()
        template, temp_template_path = temp_template_ctx.__enter__()
        request_config = _request_config_from_template(template)
        post_embed_fields = _post_embed_fields_from_template(template)
        proxy_pool = get_proxy_pool() if settings.anti_crawl_enabled else None
        stats["proxy_enabled"] = int(bool(proxy_pool))
        if proxy_pool and proxy_pool.enabled:
            proxy_ready = await _wait_for_proxy(
                proxy_pool,
                proxy_retry_delay=proxy_retry_delay,
                wait_seconds=proxy_wait_seconds,
                infinite_retry=infinite_retry,
            )
            if not proxy_ready:
                raise RuntimeError("Proxy pool unavailable; stop backfill to avoid direct requests")

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
                media_id, reason = await _fetch_post_media_id(
                    http_client=http_client,
                    post_id=int(post_id),
                    timeout=timeout,
                    request_config=request_config,
                    infinite_retry=infinite_retry,
                    retry_delay=retry_delay,
                    max_attempts=max_attempts,
                    proxy_pool=proxy_pool,
                    proxy_wait_seconds=proxy_wait_seconds,
                    proxy_retry_delay=proxy_retry_delay,
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
            source_url = ""
            reason = ""
            post_id = doc.get("id")
            if isinstance(post_id, int) and post_id > 0:
                source_url, reason = await _fetch_post_embedded_media_url(
                    http_client=http_client,
                    post_id=post_id,
                    expected_media_id=int(media_id),
                    timeout=timeout,
                    request_config=request_config,
                    post_embed_fields=post_embed_fields,
                    infinite_retry=infinite_retry,
                    retry_delay=retry_delay,
                    max_attempts=max_attempts,
                    proxy_pool=proxy_pool,
                    proxy_wait_seconds=proxy_wait_seconds,
                    proxy_retry_delay=proxy_retry_delay,
                )

            if not source_url:
                source_url, reason = await _fetch_media_url(
                    http_client=http_client,
                    media_id=int(media_id),
                    timeout=timeout,
                    request_config=request_config,
                    infinite_retry=infinite_retry,
                    retry_delay=retry_delay,
                    max_attempts=max_attempts,
                    proxy_pool=proxy_pool,
                    proxy_wait_seconds=proxy_wait_seconds,
                    proxy_retry_delay=proxy_retry_delay,
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
            if reason == "post_embedded":
                stats["post_embedded"] += 1
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
        await http_client.close()
        client.close()
        if temp_template_ctx is not None:
            temp_template_ctx.__exit__(None, None, None)
        if temp_template_path is not None:
            stats["temp_template_deleted"] = int(not temp_template_path.exists())

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Satellite Today featured_media URLs")
    parser.add_argument("--apply", action="store_true", help="Write updates to MongoDB")
    parser.add_argument("--dry-run", action="store_true", help="Do not write updates")
    parser.add_argument("--include-medium-confidence", action="store_true", help="Also re-fetch single-image medium-confidence rows")
    parser.add_argument("--finite-retry", action="store_true", help="Do not retry forever on 403/timeouts")
    parser.add_argument("--retry-delay", type=float, default=10.0, help="Delay between infinite retry attempts")
    parser.add_argument("--max-attempts", type=int, default=3, help="Max attempts per media/post request in finite retry mode")
    parser.add_argument("--proxy-wait-seconds", type=float, default=1, help="How long to wait for proxy pool availability")
    parser.add_argument("--proxy-retry-delay", type=float, default=1, help="Delay between proxy pool reload attempts")
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
            max_attempts=args.max_attempts,
            limit=args.limit,
            timeout=args.timeout,
            proxy_wait_seconds=args.proxy_wait_seconds,
            proxy_retry_delay=args.proxy_retry_delay,
        )
    )

    logger.info(
        "Done: scanned=%d updated=%d remote=%d int_targets=%d medium_targets=%d "
        "post_embedded=%d post_id_resolved=%d unresolved=%d",
        stats["scanned"],
        stats["updated"],
        stats["remote"],
        stats["int_targets"],
        stats["medium_targets"],
        stats["post_embedded"],
        stats["post_id_resolved"],
        stats["unresolved"],
    )


if __name__ == "__main__":
    main()
