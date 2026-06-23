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
from app.models.template import RequestConfig

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
    cache: dict[int, dict[str, Any] | None],
) -> dict[str, Any] | None:
    """调用 WP media API 获取媒体对象（带缓存去重）。

    404 → 永久失败，直接跳过
    """
    import json

    # 缓存命中：多个 record 共享同一 media_id 时避免重复请求
    if media_id in cache:
        return cache[media_id]

    async with semaphore:
        attempt = 1
        while attempt:
            try:
                url = MEDIA_API_TPL.format(media_id=media_id)
                text = await client.request_page(
                    url,
                    RequestConfig(
                        headers={
                            "Accept": "application/json, */*;q=0.8",
                            "Accept-Language": "en-US,en;q=0.9",
                            "User-Agent": (
                                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/125.0.0.0 Safari/537.36"
                            ),
                        },
                    ),
                    anti_crawl_enabled=True,
                )
                data = json.loads(text)
                if isinstance(data, dict):
                    cache[media_id] = data
                    return data
                logger.warning("Media %d: unexpected response type=%s", media_id, data)
            except Exception as e:
                err_str = str(e)
                if "404" in err_str:
                    cache[media_id] = None  # 缓存失败结果，避免重复请求
                    return None
                attempt += 1

    return None


async def backfill(
    dry_run: bool = False,
    batch_size: int = 50,
    concurrency: int = 30,
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
    media_cache: dict[int, dict[str, Any] | None] = {}
    stats_lock = asyncio.Lock()

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
                collection, http_client, semaphore, media_cache, batch, dry_run, stats, stats_lock
            )
            batch = []

    if batch:
        await _process_batch(
            collection, http_client, semaphore, media_cache, batch, dry_run, stats, stats_lock
        )

    await http_client.close()
    mongo_client.close()

    return stats


async def _process_batch(
    collection: Any,
    client: HttpClient,
    semaphore: asyncio.Semaphore,
    cache: dict[int, dict[str, Any] | None],
    batch: list[dict[str, Any]],
    dry_run: bool,
    stats: dict[str, int],
    stats_lock: asyncio.Lock,
) -> None:
    """处理一批记录：获取媒体对象 + 更新 MongoDB。"""
    tasks = []
    for doc in batch:
        media_id = doc.get("featured_media")
        if not isinstance(media_id, int) or media_id <= 0:
            async with stats_lock:
                stats["skipped"] += 1
            continue
        tasks.append(_process_one(collection, client, semaphore, cache, doc, media_id, dry_run, stats, stats_lock))

    await asyncio.gather(*tasks)


async def _process_one(
    collection: Any,
    client: HttpClient,
    semaphore: asyncio.Semaphore,
    cache: dict[int, dict[str, Any] | None],
    doc: dict[str, Any],
    media_id: int,
    dry_run: bool,
    stats: dict[str, int],
    stats_lock: asyncio.Lock,
) -> None:
    """处理单条记录。"""
    async with stats_lock:
        stats["total"] += 1
    doc_id = doc.get("_id")

    media_obj = await fetch_media_url(client, media_id, semaphore, cache)

    if media_obj is None:
        async with stats_lock:
            stats["failed"] += 1
        return

    if dry_run:
        async with stats_lock:
            stats["updated"] += 1
        logger.info(
            "[DRY-RUN] Would update %s: featured_media %d → %s",
            doc_id, media_id, media_obj.get("source_url", "?"),
        )
        return

    try:
        result = await collection.update_one(
            {"_id": doc_id},
            {"$set": {"featured_media": media_obj}},
        )
        if result.matched_count == 1 and result.modified_count == 1:
            async with stats_lock:
                stats["updated"] += 1
            logger.debug(
                "Updated %s: featured_media %d → object (%s)",
                doc_id, media_id, media_obj.get("source_url", "?"),
            )
        else:
            async with stats_lock:
                stats["failed"] += 1
            logger.warning(
                "Update failed for %s: matched=%d modified=%d (media_id=%d)",
                doc_id, result.matched_count, result.modified_count, media_id,
            )
    except Exception:
        async with stats_lock:
            stats["failed"] += 1
        logger.exception("Failed to update %s", doc_id)


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
        "--concurrency", type=int, default=30,
        help="Max concurrent media API requests (default: 30)",
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