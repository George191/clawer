"""MongoDB 存储后端 — 基于 motor 异步驱动的数据持久化层。

功能
----
- 记录 CRUD：支持单条/批量保存、去重（按 record_id）
- 下载状态管理：pending → downloaded / no_assets / failed
- 同步状态管理：pending → synced
- 查询接口：按 sample_id / download_status 筛选记录
- 索引自动创建：record_id 唯一索引、download_status/sync_status 查询索引
"""

from __future__ import annotations

from functools import reduce
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ReturnDocument

from app.config.settings import settings
from app.storage.file_storage import StorageBackend
from app.utils.path import get_nested_value

logger = logging.getLogger(__name__)

DOWNLOAD_CLAIM_TIMEOUT = timedelta(hours=2)


class MongoStorage(StorageBackend):
    def __init__(self) -> None:
        self._client = None
        self._db = None

    def _get_collection_name(self, template_name: str) -> str:
        return template_name

    async def _ensure_connection(self) -> None:
        if self._db is not None:
            return
        try:
            from motor.motor_asyncio import AsyncIOMotorClient

            self._client = AsyncIOMotorClient(settings.db_url)
            self._db = self._client[settings.db_name]
            await self._client.admin.command("ping")
            logger.info("Connected to MongoDB: %s/%s", settings.db_url, settings.db_name)
        except Exception as e:
            logger.error("Failed to connect to MongoDB: %s", e)
            raise

    async def _get_collection(self, template_name: str):
        await self._ensure_connection()
        collection_name = self._get_collection_name(template_name)
        collection = self._db[collection_name]
        await collection.create_index("_meta.record_id", unique=True)
        await collection.create_index("_meta.download_status")
        await collection.create_index("_meta.download_claimed_at")
        await collection.create_index("_meta.sync_status")
        return collection

    def _resolve_record_id(self, record: dict[str, Any]) -> str:
        content = json.dumps(record, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(content.encode()).hexdigest()

    @staticmethod
    def _has_meaningful_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict, set, tuple)):
            return len(value) > 0
        return True

    @classmethod
    def _merge_non_empty_fields(
        cls,
        existing: Any,
        incoming: Any,
    ) -> Any:
        if isinstance(existing, dict) and isinstance(incoming, dict):
            merged = dict(existing)
            for key, value in incoming.items():
                if key in merged:
                    merged[key] = cls._merge_non_empty_fields(merged[key], value)
                    continue
                if cls._has_meaningful_value(value):
                    merged[key] = value
            return merged
        if cls._has_meaningful_value(incoming):
            return incoming
        return existing

    async def save_record(self, template_name: str, data_type: str, dedup_fields: list[str], record: dict[str, Any]) -> str:
        collection = await self._get_collection(template_name)
        search_params = record.pop("_meta_search_params", None) or {}

        record_id = self._resolve_record_id({f: get_nested_value(record, f) for f in dedup_fields})

        record_with_meta = {
            **record,
            "_meta": {
                "template": template_name,
                "data_type": data_type,
                "record_id": record_id,
                "download_status": "pending",
                "sync_status": "pending",
                "search_params": search_params,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            },
        }

        existing = await collection.find_one({"_meta.record_id": record_id})
        if existing:
            existing_meta = existing.get("_meta", {})
            merged_record = self._merge_non_empty_fields(existing, record_with_meta)
            record_with_meta = merged_record if isinstance(merged_record, dict) else record_with_meta
            existing_meta = existing.get("_meta", {})
            record_with_meta["_meta"]["created_at"] = existing_meta.get(
                "created_at", datetime.now(timezone.utc)
            )
            record_with_meta["_meta"]["download_status"] = existing_meta.get(
                "download_status", "pending"
            )
            record_with_meta["_meta"]["sync_status"] = existing_meta.get(
                "sync_status", "pending"
            )
            record_with_meta["_meta"]["updated_at"] = datetime.now(timezone.utc)
            await collection.replace_one(
                {"_meta.record_id": record_id},
                record_with_meta,
                upsert=True,
            )
            logger.debug("Updated record in MongoDB: %s", record_id)
        else:
            await collection.insert_one(record_with_meta)
            logger.debug("Inserted record in MongoDB: %s", record_id)

        return record_id

    async def save_records(
        self, template_name: str, data_type: str, dedup_fields: list[str], records: list[dict[str, Any]]
    ) -> list[str]:
        ids: list[str] = []
        for record in records:
            record_id = await self.save_record(template_name, data_type, dedup_fields, record)
            ids.append(record_id)
        logger.info("Saved %d records to MongoDB for %s/%s", len(ids), template_name, data_type)
        return ids

    async def update_file_status(
        self,
        template_name: str,
        record_id: str,
        download_status: str,
    ) -> None:
        collection = await self._get_collection(template_name)
        await collection.update_one(
            {"_meta.record_id": record_id},
            {
                "$set": {
                    "_meta.download_status": download_status,
                    "_meta.updated_at": datetime.now(timezone.utc),
                }
            },
        )
        logger.debug("Updated file_status for %s: %s", record_id, download_status)

    async def update_record_fields(
        self,
        template_name: str,
        record_id: str,
        updates: dict[str, Any],
    ) -> None:
        collection = await self._get_collection(template_name)
        set_fields = {
            **updates,
            "_meta.updated_at": datetime.now(timezone.utc),
        }

        await collection.update_one(
            {"_meta.record_id": record_id},
            {"$set": set_fields},
        )
        logger.debug("Updated %d fields for %s", len(updates), record_id)

    async def update_sync_status(
        self,
        template_name: str,
        data_type: str,
        record_id: str,
        sync_status: str,
    ) -> None:
        collection = await self._get_collection(template_name)
        await collection.update_one(
            {"_meta.record_id": record_id},
            {
                "$set": {
                    "_meta.sync_status": sync_status,
                    "_meta.updated_at": datetime.now(timezone.utc),
                }
            },
        )
        logger.debug("Updated sync_status for %s: %s", record_id, sync_status)

    async def get_unsynced_records(
        self,
        template_name: str,
        data_type: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        collection = await self._get_collection(template_name)
        cursor = collection.find(
            {"$or": [
                {"_meta.sync_status": {"$ne": "synced"}},
                {"_meta.sync_status": {"$exists": False}},
            ]}
        ).limit(limit)
        results: list[dict[str, Any]] = []
        async for doc in cursor:
            doc.pop("_id", None)
            results.append(doc)
        return results

    async def get_ready_to_sync(
        self,
        template_name: str | None = None,
        data_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        await self._ensure_connection()

        filter_query: dict[str, Any] = {
            "$and": [
                {
                    "$or": [
                        {"_meta.sync_status": {"$ne": "synced"}},
                        {"_meta.sync_status": {"$exists": False}},
                    ],
                },
                {
                    "$or": [
                        {"_meta.download_status": "downloaded"},
                        {"_meta.download_status": "no_assets"},
                    ],
                },
            ],
        }

        if template_name:
            collection = await self._get_collection(template_name)
            cursor = collection.find(filter_query).limit(limit)
            results: list[dict[str, Any]] = []
            async for doc in cursor:
                doc.pop("_id", None)
                results.append(doc)
            return results

        # 跨所有集合查询
        results = []
        for coll_name in await self._db.list_collection_names():
            collection = self._db[coll_name]
            cursor = collection.find(filter_query).limit(limit)
            async for doc in cursor:
                doc.pop("_id", None)
                results.append(doc)
                if len(results) >= limit:
                    return results
        return results

    async def get_pending_downloads(
        self,
        template_name: str | None = None,
        limit: int = 50,
        balanced: bool = True,
    ) -> list[dict[str, Any]]:
        """获取待下载记录。

        Args:
            template_name: 指定模板名，None 表示扫描所有模板
            limit: 最大返回记录数
            balanced: 是否均衡分配（轮询各集合，避免单集合独占批次）

        Returns:
            待下载记录列表，每条记录包含 _meta 字段（含 template）
        """
        await self._ensure_connection()

        stale_before = datetime.now(timezone.utc) - DOWNLOAD_CLAIM_TIMEOUT
        filter_query = self._download_claim_filter(stale_before)

        if template_name:
            collection = await self._get_collection(template_name)
            return await self._claim_pending_downloads_from_collection(
                collection, filter_query, limit,
            )

        # 全库扫描
        coll_names = await self._db.list_collection_names()
        if not coll_names:
            return []

        if balanced:
            return await self._balanced_pending_downloads(
                coll_names, filter_query, limit,
            )
        else:
            # 贪婪模式：第一个集合填充整个批次
            results = []
            for coll_name in coll_names:
                collection = self._db[coll_name]
                claimed = await self._claim_pending_downloads_from_collection(
                    collection, filter_query, limit - len(results),
                )
                results.extend(claimed)
                if len(results) >= limit:
                    return results
            return results

    @staticmethod
    def _download_claim_filter(stale_before: datetime) -> dict[str, Any]:
        return {
            "$or": [
                {"_meta.download_status": "pending"},
                {
                    "$and": [
                        {"_meta.download_status": "downloading"},
                        {
                            "$or": [
                                {"_meta.download_claimed_at": {"$lt": stale_before}},
                                {"_meta.download_claimed_at": {"$exists": False}},
                            ]
                        },
                    ]
                },
            ]
        }

    async def _claim_pending_downloads_from_collection(
        self,
        collection: Any,
        filter_query: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        results = []
        for _ in range(max(0, limit)):
            now = datetime.now(timezone.utc)
            doc = await collection.find_one_and_update(
                filter_query,
                {
                    "$set": {
                        "_meta.download_status": "downloading",
                        "_meta.download_claimed_at": now,
                        "_meta.updated_at": now,
                    },
                    "$inc": {"_meta.download_attempts": 1},
                },
                sort=[("_meta.updated_at", 1), ("_id", 1)],
                return_document=ReturnDocument.AFTER,
            )
            if doc is None:
                break
            doc.pop("_id", None)
            results.append(doc)
        return results

    async def _balanced_pending_downloads(
        self,
        coll_names: list[str],
        filter_query: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        """均衡轮询各集合，避免某个集合的挂起记录独占整个批次。"""
        results = []
        # 每轮从每个集合取若干条（按集合数均分 + 至少 1 条）
        per_coll = max(1, limit // len(coll_names))

        # 记录每个集合的游标，按需取
        for coll_name in coll_names:
            if len(results) >= limit:
                break
            collection = self._db[coll_name]
            claimed = await self._claim_pending_downloads_from_collection(
                collection, filter_query, min(per_coll, limit - len(results)),
            )
            results.extend(claimed)

        return results

    async def get_collection_stats(self, template_name: str | None = None) -> list[dict[str, Any]]:
        """获取所有集合的概览统计。

        Returns:
            [{"name": "planet", "total": 100, "pending_download": 5, "downloaded": 80, ...}, ...]
        """
        await self._ensure_connection()
        stats = []
        coll_names = (
            [template_name] if template_name
            else await self._db.list_collection_names()
        )
        for coll_name in coll_names:
            collection = self._db[coll_name]
            total = await collection.count_documents({})
            pending = await collection.count_documents({
                "_meta.download_status": {"$in": ["pending", "downloading"]},
            })
            downloaded = await collection.count_documents({
                "_meta.download_status": "downloaded",
            })
            no_assets = await collection.count_documents({
                "_meta.download_status": "no_assets",
            })
            failed = await collection.count_documents({
                "_meta.download_status": "failed",
            })
            stats.append({
                "name": coll_name,
                "total": total,
                "pending_download": pending,
                "downloaded": downloaded,
                "no_assets": no_assets,
                "failed": failed,
            })
        return stats

    async def close(self) -> None:
        if self._client:
            self._client.close()
