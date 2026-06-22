"""Satellite Today 封面图回填脚本。

背景
----
satellite_today 采集时因反爬机制，`enrich_cover_images_batch` 未能成功将
`featured_media`（int 媒体 ID）替换为 WP 媒体对象（含 source_url）。
本脚本离线扫描已入库记录，按相同业务逻辑补全封面图数据。

用法
----
    python -m scripts.backfill_satellite_media [--dry-run] [--batch-size 50] [--concurrency 5]

逻辑参照
--------
    app/adapters/utils/news/wp/assets.py:
        - fetch_wp_media_url()     → 调用 /wp/v2/media/{id} 获取媒体对象
        - enrich_cover_images_batch() → 将 media_id 替换为媒体对象

"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from motor.motor_asyncio import AsyncIOMotorClient

from app.config.settings import settings
from app.downloader.http_client import HttpClient

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════════════════════════

TEMPLATE_NAME = "satellite_today"
BASE_URL = "https://www.satellitetoday.com"
MEDIA_API_TPL = f"{BASE_URL}/wp-json/wp/v2/media/{{media_id}}?_fields=source_url,media_details.sizes.full.source_url"


async def fetch_media_url(
    client: HttpClient,
    media_id: int,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any] | None:
    """调用 WP media API 获取媒体对象。

    与 assets.py:fetch_wp_media_url 逻辑一致：
    - 请求 /wp-json/wp/v2/media/{id}
    - 返回完整媒体对象（含 source_url）
    - 失败返回 None
    """
    async with semaphore:
        try:
            url = MEDIA_API_TPL.format(media_id=media_id)
            text = await client.request_page(url, anti_crawl_enabled=False)
            import json
            data = json.loads(text)
            if isinstance(data, dict):
                return data
            return None
        except Exception:
            logger.debug("Media %d fetch failed", media_id)
            return None


async def backfill(
    dry_run: bool = False,
    batch_size: int = 50,
    concurrency: int = 5,
) -> dict[str, int]:
    """扫描并回填 satellite_today 的 featured_media 字段。

    Returns:
        {"total": int, "updated": int, "skipped": int, "failed": int}
    """
    mongo_client = AsyncIOMotorClient(settings.db_url)
    db = mongo_client[settings.db_name]
    collection = db[TEMPLATE_NAME]

    http_client = HttpClient()
    semaphore = asyncio.Semaphore(concurrency)

    stats = {"total": 0, "updated": 0, "skipped": 0, "failed": 0}

    # 查询 featured_media 为 int 类型（media ID，尚未替换为媒体对象）的记录
    # featured_media 为 0 表示无封面图，跳过
    filter_query = {
        "featured_media": {"$type": "int", "$gt": 0},
    }

    total_count = await collection.count_documents(filter_query)
    logger.info("Found %d records with unresolved featured_media", total_count)

    if total_count == 0:
        logger.info("Nothing to backfill")
        return stats

    cursor = collection.find(filter_query).batch_size(batch_size)

    batch: list[dict[str, Any]] = []
    async for doc in cursor:
        batch.append(doc)
        if len(batch) >= batch_size:
            await _process_batch(
                collection, http_client, semaphore, batch, dry_run, stats,
            )
            batch = []

    if batch:
        await _process_batch(
            collection, http_client, semaphore, batch, dry_run, stats,
        )

    await http_client.close()
    mongo_client.close()

    return stats


async def _process_batch(
    collection: Any,
    client: HttpClient,
    semaphore: asyncio.Semaphore,
    batch: list[dict[str, Any]],
    dry_run: bool,
    stats: dict[str, int],
) -> None:
    """处理一批记录：获取媒体对象 + 更新 MongoDB。"""
    tasks = []
    for doc in batch:
        media_id = doc.get("featured_media")
        if not isinstance(media_id, int) or media_id <= 0:
            stats["skipped"] += 1
            continue
        tasks.append(_process_one(collection, client, semaphore, doc, media_id, dry_run, stats))

    await asyncio.gather(*tasks)


async def _process_one(
    collection: Any,
    client: HttpClient,
    semaphore: asyncio.Semaphore,
    doc: dict[str, Any],
    media_id: int,
    dry_run: bool,
    stats: dict[str, int],
) -> None:
    """处理单条记录。"""
    stats["total"] += 1
    record_id = doc.get("_meta", {}).get("record_id", "")

    media_obj = await fetch_media_url(client, media_id, semaphore)

    if media_obj is None:
        stats["failed"] += 1
        logger.warning("Failed to fetch media for record %s (media_id=%d)", record_id, media_id)
        return

    if dry_run:
        stats["updated"] += 1
        logger.info(
            "[DRY-RUN] Would update %s: featured_media %d → %s",
            record_id, media_id, media_obj.get("source_url", "?"),
        )
        return

    try:
        await collection.update_one(
            {"_meta.record_id": record_id},
            {"$set": {"featured_media": media_obj}},
        )
        stats["updated"] += 1
        logger.debug("Updated %s: featured_media %d → object", record_id, media_id)
    except Exception:
        stats["failed"] += 1
        logger.exception("Failed to update %s", record_id)


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill satellite_today featured_media from int IDs to WP media objects",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview only, do not write to MongoDB",
    )
    parser.add_argument(
        "--batch-size", type=int, default=50,
        help="Records per MongoDB batch (default: 50)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=5,
        help="Max concurrent media API requests (default: 5)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [BACKFILL] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.dry_run:
        logger.info("=== DRY RUN MODE ===")

    stats = asyncio.run(backfill(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        concurrency=args.concurrency,
    ))

    logger.info(
        "Done: total=%d, updated=%d, skipped=%d, failed=%d",
        stats["total"], stats["updated"], stats["skipped"], stats["failed"],
    )


if __name__ == "__main__":
    main()