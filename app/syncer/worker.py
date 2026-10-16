"""同步 Worker — 监听 MongoDB，将新增或修改的记录推送至 Kafka。

工作流程
--------
1. 轮询 MongoDB 中 sync_status 未标为 synced 的记录，不等待下载
2. 按记录 data_type 将记录推送至对应 Kafka 主题
3. 推送成功后，将对应版本的 sync_status 标为 synced

设计原则
--------
- 与采集、下载完全解耦，独立运行
- 增量同步：采集或下载完成时将 sync_status 重置为 pending
- 批量处理：通过 batch_size 控制每次推送数量
"""

from __future__ import annotations

import asyncio

from app.config.settings import settings
from app.logger import get_logger
from app.records.collection_routes import collection_route_store
from app.records.mongo_store import MongoRecordStore
from app.syncer.publisher import RecordPublisher
from app.utils.twitter_record import TWITTER_COLLECTION_TYPES
from app.web.services.ai_collect_store import ai_collect_store

logger = get_logger(__name__)


class SyncWorker:
    def __init__(
        self,
        poll_interval: int = 10,
        batch_size: int = 50,
        template_name: str | None = None,
        data_type: str | None = None,
        concurrency: int = 10,
    ) -> None:
        if batch_size < 1 or concurrency < 1:
            raise ValueError("batch_size and concurrency must be positive")
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._template_name = template_name
        self._data_type = data_type
        self._topic = f"{settings.kafka_topic}-{data_type}" if data_type else settings.kafka_topic
        self._semaphore = asyncio.Semaphore(concurrency)
        self._kafka: RecordPublisher | None = None
        self._mongo = MongoRecordStore()
        self._running = False

    async def run(self) -> None:
        self._running = True
        self._kafka = RecordPublisher(topic=self._topic)

        logger.info(
            "SyncWorker started (poll=%ds, batch=%d, template=%s, type=%s, topic=%s)",
            self._poll_interval,
            self._batch_size,
            self._template_name,
            self._data_type,
            self._topic if self._data_type else f"{settings.kafka_topic}-*",
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
        ready = await self._read_ready_records()
        if not ready:
            return 0

        logger.info("SyncWorker: pushing %d records to Kafka", len(ready))

        sent_count = 0
        synced_by_run: dict[tuple[str, str], int] = {}
        results = await asyncio.gather(*(self._sync_record(record) for record in ready))
        for record, (success, task_id) in zip(ready, results):
            if success:
                sent_count += 1
                run_id = str(((record.get("_meta") or {}).get("search_params") or {}).get("__workspace_run_id") or "")
                if task_id and run_id:
                    owner = (task_id, run_id)
                    synced_by_run[owner] = synced_by_run.get(owner, 0) + 1

        for (task_id, run_id), count in synced_by_run.items():
            await ai_collect_store.increment_run_stats(task_id, run_id, synced=count)
            await ai_collect_store.append_task_log(
                task_id,
                "ok",
                f"同步完成：records={count}",
                run_id=run_id,
            )

        logger.info("SyncWorker: synced %d records to Kafka", sent_count)
        return sent_count

    async def _read_ready_records(self) -> list[dict]:
        if self._data_type is None and self._template_name is None:
            return await self._mongo.get_ready_to_sync(
                template_name=self._template_name, limit=self._batch_size,
            )
        routes = await collection_route_store.list_by_data_type(
            self._data_type, self._template_name,
        )
        records = []
        for name in dict.fromkeys(route["collection_name"] for route in routes):
            records.extend(await self._mongo.get_ready_to_sync(
                template_name=name, limit=self._batch_size - len(records),
            ))
            if len(records) >= self._batch_size:
                break
        return records

    async def _sync_record(self, record: dict) -> tuple[bool, str]:
        async with self._semaphore:
            meta = record.get("_meta", {})
            record_id = meta.get("record_id", "")
            template = meta.get("template", "")
            data_type = meta.get("data_type") or TWITTER_COLLECTION_TYPES.get(template, "")
            if not record_id or not template:
                return False, ""
            if not data_type:
                logger.warning("SyncWorker: skipped %s without data_type", record_id)
                return False, ""
            if self._data_type and data_type != self._data_type:
                logger.warning(
                    "SyncWorker: skipped %s with type=%s (expected %s)",
                    record_id, data_type, self._data_type,
                )
                return False, ""
            try:
                await self._kafka.send_record(
                    record, topic=f"{settings.kafka_topic}-{data_type}",
                )
                acknowledged = await self._mongo.update_sync_status(
                    template, data_type, record_id, "synced",
                    updated_at=meta.get("updated_at"),
                    download_version=meta.get("download_version", 0),
                )
                task_id = str((meta.get("search_params") or {}).get("__workspace_task_id") or "")
                return True, task_id if acknowledged else ""
            except Exception:
                logger.exception("SyncWorker: sync failed for %s", record_id)
                return False, ""

    async def stop(self) -> None:
        self._running = False
        if self._kafka:
            await self._kafka.close()
        await self._mongo.close()
        logger.info("SyncWorker stopped")
