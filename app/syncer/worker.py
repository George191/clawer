"""同步 Worker — 监听 MongoDB，将已下载完成的记录推送至 Kafka。

工作流程
--------
1. 轮询 MongoDB 中 `download_status=downloaded` 且 `sync_status=pending` 的记录
2. 批量推送记录到 Kafka 指定主题
3. 推送成功后更新 MongoDB 中记录的 `sync_status` 为 synced

设计原则
--------
- 与采集、下载完全解耦，独立运行
- 幂等性：通过 sync_status 状态字段保证不重复推送
- 批量处理：通过 batch_size 控制每次推送数量
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.base.kafka import KafkaProducer
from app.base.mongo import MongoClient
from app.config.settings import settings

logger = logging.getLogger(__name__)


class SyncWorker:
    def __init__(
        self,
        poll_interval: int = 10,
        batch_size: int = 50,
    ) -> None:
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._kafka: KafkaProducer | None = None
        self._mongo = MongoClient()
        self._running = False

    async def run(self) -> None:
        self._running = True
        self._kafka = KafkaProducer()

        logger.info(
            "SyncWorker started (poll=%ds, batch=%d)",
            self._poll_interval,
            self._batch_size,
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
        ready = await self._mongo.get_ready_to_sync(limit=self._batch_size)
        if not ready:
            return 0

        logger.info("SyncWorker: pushing %d records to Kafka", len(ready))

        try:
            sent_count = await self._kafka.send_records(ready)
        except Exception:
            logger.exception("SyncWorker: Kafka send failed")
            return 0

        for record in ready:
            record_meta = record.get("_meta", {})
            record_id = record_meta.get("record_id", "")
            template_name = record_meta.get("template", "")
            data_type = record_meta.get("data_type", "")
            if record_id and template_name:
                try:
                    await self._mongo.update_sync_status(
                        template_name, data_type, record_id, "synced",
                    )
                except Exception:
                    logger.exception("SyncWorker: sync status update failed for %s", record_id)

        logger.info("SyncWorker: synced %d records to Kafka", sent_count)
        return sent_count

    async def stop(self) -> None:
        self._running = False
        if self._kafka:
            await self._kafka.close()
        await self._mongo.close()
        logger.info("SyncWorker stopped")