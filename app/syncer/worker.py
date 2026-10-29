"""同步 Worker — 监听 MongoDB，将新增或修改的记录推送至 Kafka。

工作流程
--------
1. 低频扫描 MongoDB，同步下载回写，并补偿采集直发 Kafka 时遗漏的记录
2. 按记录 data_type 将记录推送至对应 Kafka 主题
3. Kafka ACK 后，将对应版本的 sync_status 标为 synced（RDS/ODS 另行异步消费）

设计原则
--------
- 与采集、下载完全解耦，独立运行
- 增量同步：采集或下载完成时将 sync_status 重置为 pending
- 批量处理：通过 batch_size 控制每次推送数量
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from time import monotonic

from app.config.settings import settings
from app.logger import get_logger
from app.records.collection_routes import collection_route_store
from app.records.mongo_store import MongoRecordStore
from app.syncer.publisher import RecordPublisher

logger = get_logger(__name__)


class SyncWorker:
    def __init__(
        self,
        poll_interval: int = 60,
        batch_size: int = 50,
        template_name: str | None = None,
        data_type: str | None = None,
        concurrency: int = 10,
    ) -> None:
        if poll_interval < 0 or batch_size < 1 or concurrency < 1:
            raise ValueError("poll_interval must be non-negative; batch_size and concurrency must be positive")
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._template_name = template_name
        self._data_type = data_type
        self._topic = f"{settings.kafka_topic}-{data_type}" if data_type else settings.kafka_topic
        self._concurrency = concurrency
        self._kafka: RecordPublisher | None = None
        self._mongo = MongoRecordStore()
        self._running = False
        self._collection_cursor = 0
        self._collection_names: list[str] = []
        self._routes_refreshed_at: float | None = None

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
                if count < self._batch_size:
                    await asyncio.sleep(max(self._poll_interval, 0.5))
            except Exception:
                logger.exception("SyncWorker loop error")
                await asyncio.sleep(max(self._poll_interval, 0.5))

    async def _process_batch(self) -> int:
        ready = await self._read_ready_records()
        if not ready:
            return 0

        logger.debug("SyncWorker: pushing %d records to Kafka", len(ready))

        records = []
        for record in ready:
            data_type = (record.get("_meta") or {}).get("data_type")
            if self._data_type and data_type != self._data_type:
                logger.warning("SyncWorker: skipped record with type=%s (expected %s)", data_type, self._data_type)
                continue
            records.append(record)
        return await self._kafka.sync_records(
            records, self._mongo, concurrency=self._concurrency,
        )

    async def _get_collection_names(self) -> list[str]:
        if self._routes_refreshed_at is None or monotonic() - self._routes_refreshed_at >= 10:
            routes = await collection_route_store.list_by_data_type(
                self._data_type, self._template_name,
            )
            self._collection_names = list(dict.fromkeys(
                route["collection_name"] for route in routes
            ))
            self._routes_refreshed_at = monotonic()
        return self._collection_names

    async def _read_ready_records(self) -> list[dict]:
        names = await self._get_collection_names()
        records = []
        before = datetime.now(timezone.utc) - timedelta(seconds=self._poll_interval)
        if names:
            start = self._collection_cursor % len(names)
            names = names[start:] + names[:start]
            self._collection_cursor = (start + 1) % len(names)
        for name in names:
            records.extend(await self._mongo.get_ready_to_sync(
                template_name=name, limit=self._batch_size - len(records),
                before=before,
            ))
            if len(records) >= self._batch_size:
                break
        return records

    async def stop(self) -> None:
        self._running = False
        if self._kafka:
            await self._kafka.close()
        await self._mongo.close()
        logger.info("SyncWorker stopped")
