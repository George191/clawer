"""MongoDB → Elasticsearch 同步脚本。

功能
----
1. 读取指定 Mongo 集合（纯读取，不回写任何字段）
2. 写入指定 ES 索引（按 _meta.record_id upsert，幂等）
3. 批量写入，分页扫描

用法
----
    python -m scripts.sync_mongo_to_es --collection sealagom_navwarn --index sealagom_navwarn

ES / Mongo 连接配置在本文件顶部硬编码。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

# ══════════════════════════════════════════════════════════════════════════════
# 硬编码配置
# ══════════════════════════════════════════════════════════════════════════════

ES_CONFIG: dict[str, Any] = {
    "hosts": "http://space-data.cn:9200",
    "username": "elastic",
    "password": "spacejson2es**",
    "request_timeout": 120,
    "max_retries": 3,
    "retry_on_timeout": True,
    "batch_size": 50,
}

MONGO_CONFIG: dict[str, Any] = {
    "url": "mongodb://localhost:32796",
    "db_name": "raw_data",
}

# ══════════════════════════════════════════════════════════════════════════════

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from elasticsearch import Elasticsearch, helpers

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────────
# 数据类型归一化
# ───────────────────────────────────────────────────────────────────────────────

def _normalize_for_es(doc: dict[str, Any]) -> dict[str, Any]:
    from bson import ObjectId
    from datetime import datetime

    result: dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            continue
        result[key] = _normalize_value(value)
    return result


def _normalize_value(value: Any) -> Any:
    from bson import ObjectId
    from datetime import datetime

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    return str(value)


# ───────────────────────────────────────────────────────────────────────────────
# ES 客户端
# ───────────────────────────────────────────────────────────────────────────────

def get_es_client() -> Elasticsearch:
    client = Elasticsearch(
        hosts=ES_CONFIG["hosts"],
        http_auth=(ES_CONFIG.get("username", ""), ES_CONFIG.get("password", "")),
        request_timeout=ES_CONFIG.get("request_timeout", 120),
        max_retries=ES_CONFIG.get("max_retries", 3),
        retry_on_timeout=ES_CONFIG.get("retry_on_timeout", True),
    )
    return client


def ensure_es_index(es: Elasticsearch, index: str) -> None:
    import time
    for attempt in range(3):
        try:
            if not es.indices.exists(index=index):
                es.indices.create(index=index, body={
                    "settings": {
                        "number_of_shards": 1,
                        "number_of_replicas": 0,
                    },
                    "mappings": {
                        "dynamic": "true",
                        "date_detection": True,
                        "numeric_detection": True,
                    },
                })
                logger.info("Created Elasticsearch index: %s", index)
            return
        except Exception as exc:
            wait = 2 ** attempt
            logger.warning("ensure_es_index attempt %d failed: %s, waiting %ds", attempt + 1, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"ensure_es_index failed after 3 retries for index={index}")


# ───────────────────────────────────────────────────────────────────────────────
# Mongo 连接（轻量，纯读取）
# ───────────────────────────────────────────────────────────────────────────────

class LiteMongoClient:
    def __init__(self, mongo_url: str, db_name: str) -> None:
        self._mongo_url = mongo_url
        self._db_name = db_name
        self._client = None
        self._db = None

    async def connect(self) -> None:
        if self._db is not None:
            return
        from motor.motor_asyncio import AsyncIOMotorClient
        self._client = AsyncIOMotorClient(self._mongo_url)
        self._db = self._client[self._db_name]
        await self._client.admin.command("ping")
        logger.info("MongoDB 已连接: %s/%s", self._mongo_url, self._db_name)

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._db = None

    async def get_docs_page(
        self,
        collection_name: str,
        limit: int,
        skip: int,
    ) -> list[dict[str, Any]]:
        await self.connect()
        coll = self._db[collection_name]
        cursor = coll.find({}).skip(skip).limit(limit)
        docs = []
        async for doc in cursor:
            docs.append(doc)
        return docs

    async def get_count(self, collection_name: str) -> int:
        await self.connect()
        coll = self._db[collection_name]
        return await coll.count_documents({})


# ───────────────────────────────────────────────────────────────────────────────
# 同步逻辑
# ───────────────────────────────────────────────────────────────────────────────

async def sync_collection(
    mongo: LiteMongoClient,
    es: Elasticsearch,
    collection_name: str,
    es_index: str,
    batch_size: int,
) -> dict[str, int]:
    ensure_es_index(es, es_index)

    total = await mongo.get_count(collection_name)
    if total == 0:
        logger.info("  %s: 无数据，跳过", collection_name)
        return {"synced": 0, "failed": 0, "processed": 0}

    stats = {"synced": 0, "failed": 0, "processed": 0}
    skip = 0

    while skip < total:
        docs = await mongo.get_docs_page(collection_name, batch_size, skip)
        if not docs:
            break

        skip += len(docs)
        stats["processed"] += len(docs)

        bulk_actions: list[dict[str, Any]] = []
        for doc in docs:
            doc_id = None
            try:
                meta = doc.get("_meta", {})
                doc_id = meta.get("record_id")
                if not doc_id:
                    logger.warning(
                        "  %s: doc missing record_id, skip",
                        collection_name,
                    )
                    continue

                es_doc = _normalize_for_es(doc)
                bulk_actions.append({
                    "_op_type": "update",
                    "_index": es_index,
                    "_id": str(doc_id),
                    "doc": es_doc,
                    "doc_as_upsert": True,
                })
            except Exception:
                stats["failed"] += 1
                logger.exception(
                    "  sync: %s doc=%s failed",
                    collection_name, doc_id,
                )

        if bulk_actions:
            import time
            success = 0
            last_error: str | None = None
            for attempt in range(3):
                try:
                    success, errors = helpers.bulk(
                        es, bulk_actions, raise_on_error=False, stats_only=False,
                    )
                    if errors:
                        logger.warning(
                            "  本批次 bulk attempt=%d, errors=%d (first 3: %s)",
                            attempt + 1, len(errors), errors[:3],
                        )
                    else:
                        pass
                    last_error = None
                    break
                except Exception as exc:
                    last_error = str(exc)
                    wait = 2 ** attempt
                    logger.warning(
                        "  本批次 bulk attempt=%d failed: %s, waiting %ds and retrying",
                        attempt + 1, exc, wait,
                    )
                    time.sleep(wait)
                    continue

            stats["synced"] += success
            if last_error:
                stats["failed"] += len(bulk_actions) - success
                logger.error(
                    "  本批次 bulk 重试 3 次仍失败: %s, skipped %d docs",
                    last_error, len(bulk_actions) - success,
                )

        logger.info(
            "  %s: %d/%d (累计 synced=%d, failed=%d)",
            collection_name, min(skip, total), total,
            stats["synced"], stats["failed"],
        )

    return stats


async def run_sync(collection_name: str, es_index: str) -> None:
    batch_size = ES_CONFIG.get("batch_size", 200)
    mongo_url = MONGO_CONFIG["url"]
    db_name = MONGO_CONFIG["db_name"]

    logger.info("MongoDB: %s/%s", mongo_url, db_name)
    logger.info("ES: %s → index=%s", ES_CONFIG["hosts"], es_index)
    logger.info("Collection: %s (batch=%d)", collection_name, batch_size)

    mongo = LiteMongoClient(mongo_url, db_name)
    await mongo.connect()
    es = get_es_client()

    try:
        count = await mongo.get_count(collection_name)
        logger.info("▶ [%s] 共 %d 条记录待同步", collection_name, count)

        stats = await sync_collection(mongo, es, collection_name, es_index, batch_size)

        logger.info(
            "\n═══════════════════════════════════════\n"
            "  完成: processed=%d, synced=%d, failed=%d\n"
            "═══════════════════════════════════════",
            stats["processed"], stats["synced"], stats["failed"],
        )
    finally:
        await mongo.close()


# ───────────────────────────────────────────────────────────────────────────────
# CLI
# ───────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MongoDB → Elasticsearch 同步脚本（Mongo 纯读取，不回写字段）",
    )
    parser.add_argument(
        "--collection",
        type=str,
        required=True,
        help="MongoDB 集合名（如 sealagom_navwarn）",
    )
    parser.add_argument(
        "--index",
        type=str,
        required=True,
        help="Elasticsearch 索引名",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        asyncio.run(run_sync(args.collection, args.index))
    except KeyboardInterrupt:
        logger.info("用户中断，退出")


if __name__ == "__main__":
    main()
