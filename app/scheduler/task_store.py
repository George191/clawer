"""任务持久化存储 — 基于 MongoDB 的任务状态追踪。

记录每次任务执行的完整生命周期：
    created → started → success / failed / retry

查询接口支持：
    - 按任务名称查询历史
    - 按时间范围查询
    - 按状态查询
    - 获取最近一次执行结果
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection

from app.config.settings import settings

logger = logging.getLogger(__name__)

# 任务状态常量
STATUS_CREATED = "created"
STATUS_STARTED = "started"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_RETRY = "retry"

# 集合名
COLLECTION_NAME = "_scheduler_tasks"


class TaskStore:
    """任务执行记录的持久化存储。

    每条记录结构:
    {
        "_id": str,              # task_id (Celery task id)
        "task_name": str,         # 任务名称
        "status": str,            # created/started/success/failed/retry
        "params": dict,           # 任务参数
        "result": dict | None,    # 执行结果
        "error": str | None,      # 错误信息
        "retry_count": int,       # 重试次数
        "created_at": datetime,
        "started_at": datetime | None,
        "finished_at": datetime | None,
        "duration_ms": int | None,
    }
    """

    def __init__(self, mongo_client: AsyncIOMotorClient | None = None) -> None:
        self._client = mongo_client
        self._collection: AsyncIOMotorCollection | None = None

    async def _get_collection(self) -> AsyncIOMotorCollection:
        """延迟获取 MongoDB collection。"""
        if self._collection is None:
            if self._client is None:
                self._client = AsyncIOMotorClient(settings.db_url)
            db = self._client[settings.db_name]
            self._collection = db[COLLECTION_NAME]

            # 创建索引（幂等操作）
            await self._collection.create_index("task_name")
            await self._collection.create_index("status")
            await self._collection.create_index([("task_name", 1), ("created_at", -1)])

        return self._collection

    async def record_created(
        self,
        task_id: str,
        task_name: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """记录任务创建。"""
        coll = await self._get_collection()
        now = datetime.now(timezone.utc)
        await coll.update_one(
            {"_id": task_id},
            {"$set": {
                "task_name": task_name,
                "status": STATUS_CREATED,
                "params": params or {},
                "result": None,
                "error": None,
                "retry_count": 0,
                "created_at": now,
                "started_at": None,
                "finished_at": None,
                "duration_ms": None,
            }},
            upsert=True,
        )
        logger.debug("Task recorded as created: %s (%s)", task_name, task_id)

    async def record_started(self, task_id: str) -> None:
        """记录任务开始执行。"""
        coll = await self._get_collection()
        now = datetime.now(timezone.utc)
        await coll.update_one(
            {"_id": task_id},
            {"$set": {
                "status": STATUS_STARTED,
                "started_at": now,
            }},
        )
        logger.debug("Task started: %s", task_id)

    async def record_success(
        self,
        task_id: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        """记录任务成功完成。"""
        coll = await self._get_collection()
        now = datetime.now(timezone.utc)
        await coll.update_one(
            {"_id": task_id},
            {"$set": {
                "status": STATUS_SUCCESS,
                "result": result,
                "finished_at": now,
            }},
        )
        await self._update_duration(task_id)
        logger.info("Task succeeded: %s", task_id)

    async def record_failure(
        self,
        task_id: str,
        error: str,
        retry_count: int = 0,
    ) -> None:
        """记录任务失败。"""
        coll = await self._get_collection()
        now = datetime.now(timezone.utc)
        status = STATUS_RETRY if retry_count > 0 else STATUS_FAILED
        await coll.update_one(
            {"_id": task_id},
            {"$set": {
                "status": status,
                "error": error,
                "retry_count": retry_count,
                "finished_at": now,
            }},
        )
        await self._update_duration(task_id)
        logger.warning("Task %s: %s (retry_count=%d)", status, task_id, retry_count)

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """获取单条任务记录。"""
        coll = await self._get_collection()
        return await coll.find_one({"_id": task_id})

    async def get_latest(
        self,
        task_name: str,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        """获取指定任务名称的最近一条记录。"""
        coll = await self._get_collection()
        query: dict[str, Any] = {"task_name": task_name}
        if status:
            query["status"] = status
        cursor = coll.find(query).sort("created_at", -1).limit(1)
        docs = await cursor.to_list(length=1)
        return docs[0] if docs else None

    async def get_history(
        self,
        task_name: str | None = None,
        status: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """查询任务执行历史。"""
        coll = await self._get_collection()
        query: dict[str, Any] = {}
        if task_name:
            query["task_name"] = task_name
        if status:
            query["status"] = status
        if start or end:
            query["created_at"] = {}
            if start:
                query["created_at"]["$gte"] = start
            if end:
                query["created_at"]["$lte"] = end
        cursor = coll.find(query).sort("created_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def get_stats(self, task_name: str | None = None) -> dict[str, Any]:
        """获取任务执行统计。"""
        coll = await self._get_collection()
        match: dict[str, Any] = {}
        if task_name:
            match["task_name"] = task_name

        pipeline = [
            {"$match": match},
            {"$group": {
                "_id": "$status",
                "count": {"$sum": 1},
            }},
        ]
        cursor = coll.aggregate(pipeline)
        results = await cursor.to_list(length=100)

        stats = {doc["_id"]: doc["count"] for doc in results}
        stats["total"] = sum(stats.values())
        return stats

    async def _update_duration(self, task_id: str) -> None:
        """计算并更新任务执行时长（毫秒）。"""
        coll = await self._get_collection()
        doc = await coll.find_one({"_id": task_id}, {"started_at": 1, "finished_at": 1})
        if not doc:
            return
        started = doc.get("started_at")
        finished = doc.get("finished_at")
        if started and finished:
            duration_ms = int((finished - started).total_seconds() * 1000)
            await coll.update_one(
                {"_id": task_id},
                {"$set": {"duration_ms": duration_ms}},
            )

    async def close(self) -> None:
        """关闭 MongoDB 连接。"""
        if self._client:
            self._client.close()


# ══════════════════════════════════════════════════════════════════════
#  全局单例
# ══════════════════════════════════════════════════════════════════════

_store: TaskStore | None = None


def get_task_store() -> TaskStore:
    """获取全局 TaskStore 单例。"""
    global _store
    if _store is None:
        _store = TaskStore()
    return _store
