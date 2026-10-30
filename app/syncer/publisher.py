"""发布采集记录，维护记录消息格式和 Kafka 分区键。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.config.settings import settings
from app.logger import get_logger
from app.records.field_names import normalize_record_fields
from app.records.values import require_canonical_record
from app.storage.kafka_client import KafkaClient
from app.web.services.ai_collect_store import ai_collect_store

if TYPE_CHECKING:
    from app.records.mongo_store import MongoRecordStore

logger = get_logger(__name__)
SYNC_TIMEOUT_SECONDS = 30


class RecordPublisher(KafkaClient):
    async def send_record(
        self,
        record: dict[str, Any],
        key: str | None = None,
        topic: str | None = None,
    ) -> None:
        record = normalize_record_fields({key: value for key, value in record.items() if key != "_id"})
        require_canonical_record(record)
        meta = dict(record.get("_meta") or {})
        # Mongo stores dates at millisecond precision; direct sends must match it.
        for field in ("created_at", "updated_at"):
            if isinstance(value := meta.get(field), datetime):
                meta[field] = value.replace(microsecond=value.microsecond // 1000 * 1000)
        record["_meta"] = meta
        target_topic = topic or self._topic
        message_key = key or record.get("_meta", {}).get("record_id", "")
        message = {
            **record,
            "_kafka_meta": {
                "topic": target_topic,
                "produced_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        await self.send(message, key=message_key, topic=target_topic)
        logger.info("RecordPublisher: sent → %s record_id=%s", target_topic, message_key)

    async def sync_records(
        self, records: list[dict[str, Any]], storage: MongoRecordStore, *, concurrency: int = 10,
    ) -> int:
        """Send persisted snapshots and acknowledge only their stored versions."""
        semaphore = asyncio.Semaphore(concurrency)
        completed: list[tuple[dict[str, Any], bool]] = []

        async def sync(record: dict[str, Any]) -> None:
            meta = record.get("_meta") or {}
            if meta.get("sync_status") == "synced":
                return
            if not all(meta.get(field) for field in ("record_id", "template", "data_type", "updated_at")):
                logger.warning("RecordPublisher: skipped record without sync metadata")
                return
            async with semaphore:
                try:
                    await self.send_record(record, topic=f"{settings.kafka_topic}-{meta['data_type']}")
                    acknowledged = await storage.update_sync_status(
                        meta["template"], meta["data_type"], meta["record_id"], "synced",
                        updated_at=meta["updated_at"], download_version=meta.get("download_version", 0),
                    )
                    completed.append((record, acknowledged))
                except Exception:
                    logger.exception("RecordPublisher: sync failed for %s", meta["record_id"])

        try:
            await asyncio.wait_for(
                asyncio.gather(*(sync(record) for record in records)), timeout=SYNC_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("RecordPublisher: sync batch timed out; Mongo will compensate pending records")

        synced_by_run: dict[tuple[str, str], int] = {}
        for record, acknowledged in completed:
            if not acknowledged:
                continue
            owner = ((record.get("_meta") or {}).get("search_params") or {})
            task_id = str(owner.get("__workspace_task_id") or "")
            run_id = str(owner.get("__workspace_run_id") or "")
            if task_id and run_id:
                identity = (task_id, run_id)
                synced_by_run[identity] = synced_by_run.get(identity, 0) + 1
        for (task_id, run_id), count in synced_by_run.items():
            try:
                await ai_collect_store.increment_run_stats(task_id, run_id, synced=count)
                await ai_collect_store.write_task_log(
                    task_id, run_id, "Syncer", logging.INFO,
                    "Kafka publish acknowledged: records=%d batch_records=%d; RDS/ODS consume asynchronously",
                    count, len(records),
                )
            except Exception:
                logger.exception("RecordPublisher: sync statistics failed for task=%s run=%s", task_id, run_id)
        logger.info("RecordPublisher: Kafka acknowledged %d/%d records", len(completed), len(records))
        return len(completed)

    async def send_records(
        self,
        records: list[dict[str, Any]],
        topic: str | None = None,
    ) -> int:
        sent_count = 0
        for record in records:
            try:
                await self.send_record(record, topic=topic)
                sent_count += 1
            except Exception as e:
                logger.error("Failed to send record: %s", e)
        logger.info("Sent %d/%d records to Kafka", sent_count, len(records))
        return sent_count
