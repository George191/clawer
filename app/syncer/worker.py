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
from typing import Any

from app.base.kafka import KafkaProducer
from app.base.mongo import MongoClient
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
        ready = await self._mongo.get_ready_to_sync(
            template_name=self._template_name,
            limit=self._batch_size,
        )
        if not ready:
            return 0

        eligible: list[dict[str, Any]] = []
        for record in ready:
            task_id = str(
                (record.get("_meta", {}).get("search_params") or {}).get(
                    "__workspace_task_id"
                )
                or ""
            )
            if not task_id:
                eligible.append(record)
                continue
            task_control = await ai_collect_store.get_task_control(task_id)
            if task_control is None:
                record.get("_meta", {}).get("search_params", {}).pop(
                    "__workspace_task_id",
                    None,
                )
                eligible.append(record)
            elif task_control.get("sync_state") == "running":
                eligible.append(record)
        if not eligible:
            return 0

        logger.info("SyncWorker: pushing %d records to Kafka", len(eligible))

        try:
            sent_count = await self._kafka.send_records(eligible)
        except Exception:
            logger.exception("SyncWorker: Kafka send failed")
            return 0

        synced_by_task: dict[str, int] = {}
        for record in eligible:
            record_meta = record.get("_meta", {})
            record_id = record_meta.get("record_id", "")
            template_name = record_meta.get("template", "")
            data_type = record_meta.get("data_type", "")
            if record_id and template_name:
                try:
                    await self._mongo.update_sync_status(
                        template_name, data_type, record_id, "synced",
                    )
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
                    logger.exception("SyncWorker: sync status update failed for %s", record_id)

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
        logger.info("SyncWorker stopped")
