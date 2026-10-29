"""同步 Worker — 监听 MongoDB，将新增或修改的记录推送至 Kafka。

工作流程
--------
1. 优先接收主动投递的完整记录，低频扫描 MongoDB 补偿遗漏，不等待下载
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
import logging
from datetime import datetime, timedelta, timezone
from time import monotonic

from app.config.settings import settings
from app.logger import get_logger
from app.records.collection_routes import collection_route_store
from app.records.delivery import RecordDelivery
from app.records.mongo_store import MongoRecordStore
from app.syncer.publisher import RecordPublisher
from app.web.services.ai_collect_store import ai_collect_store

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
        self._semaphore = asyncio.Semaphore(concurrency)
        self._kafka: RecordPublisher | None = None
        self._mongo = MongoRecordStore()
        self._delivery = RecordDelivery()
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

        next_scan = 0.0
        while self._running:
            try:
                ready = await self._read_queued_records()
                if ready:
                    await self._process_batch(ready)
                if monotonic() >= next_scan:
                    count = await self._process_batch()
                    next_scan = monotonic() + (
                        self._poll_interval if count < self._batch_size else 0
                    )
                if not ready:
                    await asyncio.sleep(0.5)
            except Exception:
                logger.exception("SyncWorker loop error")
                next_scan = monotonic() + self._poll_interval
                await asyncio.sleep(0.5)

    async def _process_batch(self, ready: list[dict] | None = None) -> int:
        if ready is None:
            ready = await self._read_ready_records()
        if not ready:
            return 0

        logger.debug("SyncWorker: pushing %d records to Kafka", len(ready))

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
            await ai_collect_store.write_task_log(
                task_id, run_id, "Syncer", logging.INFO,
                "Kafka publish acknowledged: records=%d batch_records=%d; RDS/ODS consume asynchronously",
                count, len(ready),
            )

        logger.info(
            "SyncWorker: Kafka acknowledged %d/%d records; RDS/ODS consume asynchronously",
            sent_count, len(ready),
        )
        return sent_count

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

    async def _read_queued_records(self) -> list[dict]:
        names = await self._get_collection_names()
        if not names:
            return []
        return await self._delivery.receive(
            kind="sync", collections=names, limit=self._batch_size,
        )

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

    async def _sync_record(self, record: dict) -> tuple[bool, str]:
        async with self._semaphore:
            meta = record.get("_meta", {})
            record_id = meta.get("record_id", "")
            template = meta.get("template", "")
            data_type = meta.get("data_type") or ""
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
                await asyncio.wait_for(
                    self._kafka.send_record(
                        record, topic=f"{settings.kafka_topic}-{data_type}",
                    ),
                    timeout=30,
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
        await self._delivery.close()
        if self._kafka:
            await self._kafka.close()
        await self._mongo.close()
        logger.info("SyncWorker stopped")
