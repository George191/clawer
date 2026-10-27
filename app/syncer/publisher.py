"""发布采集记录，维护记录消息格式和 Kafka 分区键。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.logger import get_logger
from app.records.field_names import normalize_record_fields
from app.records.values import require_canonical_record
from app.storage.kafka_client import KafkaClient

logger = get_logger(__name__)


class RecordPublisher(KafkaClient):
    async def send_record(
        self,
        record: dict[str, Any],
        key: str | None = None,
        topic: str | None = None,
    ) -> None:
        record = normalize_record_fields(record)
        require_canonical_record(record)
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
