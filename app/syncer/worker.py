"""同步 Worker — 监听 MongoDB，将新增或修改的记录推送至 Kafka。

工作流程
--------
1. 根据 Redis DB 2 中各集合的时间阈值，按 updated_at 升序读取记录，不等待下载
2. 批量推送记录到 Kafka 指定主题
3. 整批成功后，将各集合最后一条记录的时间和分页 ID 存入 Redis

设计原则
--------
- 与采集、下载完全解耦，独立运行
- 增量同步：只根据 updated_at 与 Redis 阈值筛选，不判断同步状态
- 批量处理：通过 batch_size 控制每次推送数量
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from app.base.kafka import KafkaProducer
from app.base.mongo import MongoClient
from app.base.redis_connection import RedisConnection
from app.config.settings import settings
from app.logger import get_logger
from app.web.services.ai_collect_store import ai_collect_store

logger = get_logger(__name__)


class SyncWorker:
    def __init__(
        self,
        poll_interval: int = 10,
        batch_size: int = 50,
        template_name: str | None = None,
    ) -> None:
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._template_name = template_name
        self._kafka: KafkaProducer | None = None
        self._mongo = MongoClient()
        self._redis = RedisConnection(settings.etl_redis_url)
        self._watermark_key = f"kafka:syncer:{settings.kafka_topic}:{settings.db_name}"
        self._running = False

    async def run(self) -> None:
        self._running = True
        self._kafka = KafkaProducer()

        logger.info(
            "SyncWorker started (poll=%ds, batch=%d, template=%s)",
            self._poll_interval,
            self._batch_size,
            self._template_name,
        )

        while self._running:
            try:
                count = await self._process_batch()
                if count == 0:
                    await asyncio.sleep(self._poll_interval)
            except Exception:
                logger.exception("SyncWorker loop error")
                await asyncio.sleep(self._poll_interval)

    async def _process_batch(self) -> int:
        redis = await self._redis.ensure_connected()
        if redis is None:
            raise RuntimeError("Syncer requires Redis DB 2 for its time threshold")
        cached = await redis.hgetall(self._watermark_key)
        watermarks = {}
        for collection, value in cached.items():
            updated_at, record_id = json.loads(value)
            watermarks[collection] = (datetime.fromisoformat(updated_at).replace(tzinfo=None), record_id)
        ready = await self._mongo.get_ready_to_sync(
            template_name=self._template_name,
            limit=self._batch_size,
            watermarks=watermarks,
        )
        if not ready:
            return 0

        logger.info("SyncWorker: pushing %d records to Kafka", len(ready))

        sent_count = 0
        synced_by_task: dict[str, int] = {}
        for record in ready:
            record_meta = record.get("_meta", {})
            record_id = record_meta.get("record_id", "")
            template_name = record_meta.get("template", "")
            if record_id and template_name:
                try:
                    await self._kafka.send_record(record)
                    sent_count += 1
                    workspace_task_id = str(
                        (record_meta.get("search_params") or {}).get(
                            "__workspace_task_id"
                        )
                        or ""
                    )
                    if workspace_task_id:
                        synced_by_task[workspace_task_id] = (
                            synced_by_task.get(workspace_task_id, 0) + 1
                        )
                except Exception:
                    logger.exception("SyncWorker: sync failed for %s", record_id)

        if sent_count == len(ready):
            thresholds = {}
            for record in ready:
                meta = record["_meta"]
                thresholds[meta["template"]] = json.dumps([
                    meta["updated_at"].replace(tzinfo=timezone.utc).isoformat(),
                    meta["record_id"],
                ])
            await redis.hset(self._watermark_key, mapping=thresholds)

        for task_id, count in synced_by_task.items():
            await ai_collect_store.increment_task_stats(task_id, synced=count)
            await ai_collect_store.append_task_log(
                task_id,
                "ok",
                f"同步完成：records={count}",
            )

        logger.info("SyncWorker: synced %d records to Kafka", sent_count)
        return sent_count

    async def stop(self) -> None:
        self._running = False
        if self._kafka:
            await self._kafka.close()
        await self._mongo.close()
        await self._redis.close()
        logger.info("SyncWorker stopped")
