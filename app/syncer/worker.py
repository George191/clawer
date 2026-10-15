"""同步 Worker — 监听 MongoDB，将新增或修改的记录推送至 Kafka。

工作流程
--------
1. 轮询 MongoDB 中 sync_status 未标为 synced 的记录，不等待下载
2. 批量推送记录到 Kafka 指定主题
3. 推送成功后，将对应版本的 sync_status 标为 synced

设计原则
--------
- 与采集、下载完全解耦，独立运行
- 增量同步：采集或下载完成时将 sync_status 重置为 pending
- 批量处理：通过 batch_size 控制每次推送数量
"""

from __future__ import annotations

import asyncio

from app.base.kafka import KafkaProducer
from app.base.mongo import MongoClient
from app.config.settings import settings
from app.engine.template_loader import TemplateLoader
from app.logger import get_logger
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
        self._kafka: KafkaProducer | None = None
        self._mongo = MongoClient()
        self._running = False

    async def run(self) -> None:
        self._running = True
        self._kafka = KafkaProducer(topic=self._topic)

        logger.info(
            "SyncWorker started (poll=%ds, batch=%d, template=%s, type=%s, topic=%s)",
            self._poll_interval,
            self._batch_size,
            self._template_name,
            self._data_type,
            self._topic,
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
        synced_by_task: dict[str, int] = {}
        results = await asyncio.gather(*(self._sync_record(record) for record in ready))
        for success, task_id in results:
            if success:
                sent_count += 1
                if task_id:
                    synced_by_task[task_id] = synced_by_task.get(task_id, 0) + 1

        for task_id, count in synced_by_task.items():
            if await ai_collect_store.get_task_control(task_id) is None:
                continue
            await ai_collect_store.increment_task_stats(task_id, synced=count)
            await ai_collect_store.append_task_log(
                task_id,
                "ok",
                f"同步完成：records={count}",
            )

        logger.info("SyncWorker: synced %d records to Kafka", sent_count)
        return sent_count

    async def _read_ready_records(self) -> list[dict]:
        if self._data_type is None:
            return await self._mongo.get_ready_to_sync(
                template_name=self._template_name, limit=self._batch_size,
            )
        names = await TemplateLoader().list_names_by_data_type(self._data_type)
        records = []
        for name in names:
            if self._template_name and name != self._template_name:
                continue
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
            data_type = self._data_type or TWITTER_COLLECTION_TYPES.get(template) or meta.get("data_type", "")
            if not record_id or not template:
                return False, ""
            if self._data_type and meta.get("data_type") != data_type:
                record = {**record, "_meta": {**meta, "data_type": data_type}}
            try:
                await self._kafka.send_record(record)
                await self._mongo.update_sync_status(
                    template, data_type, record_id, "synced",
                    updated_at=meta.get("updated_at"),
                )
                task_id = str((meta.get("search_params") or {}).get("__workspace_task_id") or "")
                return True, task_id
            except Exception:
                logger.exception("SyncWorker: sync failed for %s", record_id)
                return False, ""

    async def stop(self) -> None:
        self._running = False
        if self._kafka:
            await self._kafka.close()
        await self._mongo.close()
        logger.info("SyncWorker stopped")
