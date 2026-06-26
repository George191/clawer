from __future__ import annotations

import argparse
import asyncio
import logging
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
from app.engine.template_loader import TemplateLoader
from app.etl.normalizers.base import build_asset_lookup
from app.utils.path import get_nested_value

logger = logging.getLogger(__name__)

TEMPLATE_NAME = "satellite_today"
BASE_URL = "https://www.satellitetoday.com"
MEDIA_API_TPL = (
    f"{BASE_URL}/wp-json/wp/v2/media/{{media_id}}"
    "?_fields=id,source_url,media_details.sizes.full.source_url"
)


def _remote_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    url = value.strip()
    return url if url.startswith(("http://", "https://")) else ""


def _has_download_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        for key in ("url", "href", "src", "source_url", "link", "pdf", "thumbnail", "full"):
            if _remote_url(value.get(key)) or (isinstance(value.get(key), str) and value.get(key).strip()):
                return True
        return False
    return value is not None


def _has_asset_prefix(asset_lookup: dict[str, str], selector: str) -> bool:
    prefix = f"{selector}."
    return any(key == selector or key.startswith(prefix) for key in asset_lookup)


def _selector_missing_entries(record: dict[str, Any], selector: str) -> list[str]:
    value = get_nested_value(record, selector)
    asset_lookup = build_asset_lookup(record.get("assets") or {})

    if isinstance(value, list):
        missing: list[str] = []
        for idx, item in enumerate(value):
            if not _has_download_value(item):
                continue
            item_selector = f"{selector}.{idx}"
            if not _has_asset_prefix(asset_lookup, item_selector):
                missing.append(item_selector)
        return missing

    if _has_download_value(value) and not _has_asset_prefix(asset_lookup, selector):
        return [selector]
    return []


def _featured_media_state(record: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    featured_media = record.get("featured_media")
    if isinstance(featured_media, int) and featured_media > 0:
        return "int", None

    if isinstance(featured_media, dict):
        source_url = _remote_url(featured_media.get("source_url"))
        if source_url:
            return "ok", {"id": featured_media.get("id"), "source_url": source_url}
        return "missing_source_url", None

    if _remote_url(featured_media):
        return "ok", {"source_url": _remote_url(featured_media)}

    return "missing", None


async def _resolve_featured_media(http_client: HttpClient, media_id: int) -> dict[str, Any] | None:
    text = await http_client.request_page(
        MEDIA_API_TPL.format(media_id=media_id),
        None,
        anti_crawl_enabled=settings.anti_crawl_enabled,
    )
    import json

    data = json.loads(text)
    if not isinstance(data, dict):
        return None

    source_url = _remote_url(data.get("source_url"))
    if not source_url:
        media_details = data.get("media_details") or {}
        sizes = media_details.get("sizes") or {}
        full = sizes.get("full") or {}
        source_url = _remote_url(full.get("source_url"))
    if not source_url:
        return None

    media_obj: dict[str, Any] = {"source_url": source_url}
    if isinstance(data.get("id"), int):
        media_obj["id"] = int(data["id"])
    else:
        media_obj["id"] = media_id
    return media_obj


def _load_download_selectors() -> list[str]:
    template = TemplateLoader().load(TEMPLATE_NAME)
    return [config.selector for config in template.download]


def _missing_download_requirements(
    record: dict[str, Any],
    selectors: list[str],
) -> dict[str, Any]:
    featured_media_status, _ = _featured_media_state(record)
    missing_selectors: list[str] = []
    for selector in selectors:
        missing_selectors.extend(_selector_missing_entries(record, selector))

    return {
        "featured_media_status": featured_media_status,
        "missing_selectors": missing_selectors,
        "needs_requeue": bool(missing_selectors) or featured_media_status == "int",
    }


async def run(*, apply: bool, limit: int, sample: int) -> dict[str, Any]:
    selectors = _load_download_selectors()
    client = AsyncIOMotorClient(settings.db_url)
    collection = client[settings.db_name][TEMPLATE_NAME]
    http_client = HttpClient()

    stats: dict[str, Any] = {
        "scanned": 0,
        "matched": 0,
        "requeued": 0,
        "resolved_featured_media": 0,
        "unresolved_featured_media": 0,
        "missing_featured_media_int": 0,
        "missing_featured_media_asset": 0,
        "missing_attachments": 0,
        "missing_images": 0,
        "samples": [],
    }

    try:
        cursor = collection.find(
            {"_meta.download_status": "downloaded"},
            {
                "_meta.record_id": 1,
                "_meta.download_status": 1,
                "_meta.updated_at": 1,
                "url": 1,
                "featured_media": 1,
                "attachments": 1,
                "images": 1,
                "assets": 1,
            },
        )
        if limit > 0:
            cursor = cursor.limit(limit)

        async for doc in cursor:
            stats["scanned"] += 1
            status = _missing_download_requirements(doc, selectors)
            if not status["needs_requeue"]:
                continue

            featured_media_status = status["featured_media_status"]
            missing_selectors = list(status["missing_selectors"])
            if featured_media_status == "int":
                stats["missing_featured_media_int"] += 1
            if any(selector.startswith("featured_media") for selector in missing_selectors):
                stats["missing_featured_media_asset"] += 1
            if any(selector.startswith("attachments") for selector in missing_selectors):
                stats["missing_attachments"] += 1
            if any(selector.startswith("images") for selector in missing_selectors):
                stats["missing_images"] += 1

            record_id = ((doc.get("_meta") or {}).get("record_id") or "")
            sample_item = {
                "record_id": record_id,
                "url": doc.get("url"),
                "featured_media_status": featured_media_status,
                "missing_selectors": missing_selectors,
            }
            if len(stats["samples"]) < sample:
                stats["samples"].append(sample_item)

            update_fields: dict[str, Any] = {
                "_meta.updated_at": datetime.now(timezone.utc),
            }

            if featured_media_status == "int":
                media_id = int(doc["featured_media"])
                resolved = None
                try:
                    resolved = await _resolve_featured_media(http_client, media_id)
                except Exception as exc:
                    logger.warning(
                        "Resolve featured_media failed record_id=%s media_id=%s error=%s",
                        record_id,
                        media_id,
                        exc,
                    )
                if resolved:
                    update_fields["featured_media"] = resolved
                    stats["resolved_featured_media"] += 1
                else:
                    stats["unresolved_featured_media"] += 1
                    if not missing_selectors:
                        continue

            stats["matched"] += 1
            if not apply:
                continue

            update_fields["_meta.download_status"] = "pending"
            update_fields["_meta.sync_status"] = "pending"
            result = await collection.update_one(
                {"_id": doc["_id"]},
                {"$set": update_fields},
            )
            stats["requeued"] += int(result.modified_count)

        return stats
    finally:
        await http_client.close()
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Requeue satellite_today downloaded records with missing assets",
    )
    parser.add_argument("--apply", action="store_true", help="Apply updates and requeue matched records")
    parser.add_argument("--limit", type=int, default=0, help="Limit scanned downloaded records")
    parser.add_argument("--sample", type=int, default=20, help="Number of sample records to print")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [SAT-ASSET] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stats = asyncio.run(
        run(
            apply=args.apply,
            limit=args.limit,
            sample=args.sample,
        )
    )

    logger.info(
        "Done: scanned=%d matched=%d requeued=%d resolved_featured_media=%d "
        "unresolved_featured_media=%d missing_featured_media_int=%d "
        "missing_featured_media_asset=%d missing_attachments=%d missing_images=%d",
        stats["scanned"],
        stats["matched"],
        stats["requeued"],
        stats["resolved_featured_media"],
        stats["unresolved_featured_media"],
        stats["missing_featured_media_int"],
        stats["missing_featured_media_asset"],
        stats["missing_attachments"],
        stats["missing_images"],
    )
    for item in stats["samples"]:
        logger.info("Sample: %s", item)


if __name__ == "__main__":
    main()
