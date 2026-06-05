"""Kafka 生产者 — 封装消息发送、连接管理和序列化。

支持：
- 延迟连接：首次 send 时自动建立连接
- 重试机制：NodeNotReadyError / 连接超时自动指数退避重试
- 批量发送：send_records 一次性推送多条记录
- JSON 序列化：自动处理 datetime 等类型
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)

KAFKA_CONNECT_RETRY_WAIT = 3
KAFKA_CONNECT_MAX_RETRY_DELAY = 600


class KafkaProducer:
    def __init__(self) -> None:
        self._producer = None
        self._topic = settings.kafka_topic
        self._connected = False

    async def _ensure_connection(self) -> None:
        if self._producer is not None and self._connected:
            return

        from aiokafka import AIOKafkaProducer
        from aiokafka.errors import (
            NodeNotReadyError,
            KafkaConnectionError,
            RequestTimedOutError,
        )

        brokers = [b.strip() for b in settings.kafka_brokers.split(",") if b.strip()]
        retryable = (NodeNotReadyError, KafkaConnectionError, RequestTimedOutError, ConnectionError, OSError)

        attempt = 0
        while True:
            try:
                self._producer = AIOKafkaProducer(
                    bootstrap_servers=brokers,
                    client_id=settings.kafka_client_id,
                    value_serializer=lambda v: json.dumps(
                        v, ensure_ascii=False, default=str
                    ).encode("utf-8"),
                    key_serializer=lambda k: k.encode("utf-8") if k else None,
                    acks="all",
                    enable_idempotence=settings.kafka_enable_idempotence,
                    max_request_size=1048576,
                    request_timeout_ms=30000,
                    connections_max_idle_ms=540000,
                )
                await self._producer.start()

                await asyncio.sleep(1)

                await self._producer.partitions_for(self._topic)
                self._connected = True
                logger.info("Connected to Kafka: %s, topic: %s", brokers, self._topic)
                return

            except retryable as e:
                self._connected = False
                if self._producer is not None:
                    try:
                        await self._producer.stop()
                    except Exception:
                        pass
                    self._producer = None

                delay = min(KAFKA_CONNECT_RETRY_WAIT * (2 ** min(attempt, 5)), KAFKA_CONNECT_MAX_RETRY_DELAY)
                logger.warning(
                    "Kafka not ready (attempt %d): %s. Retrying in %ds...",
                    attempt + 1, e, delay,
                )
                await asyncio.sleep(delay)
                attempt += 1

            except Exception as e:
                self._connected = False
                if self._producer is not None:
                    try:
                        await self._producer.stop()
                    except Exception:
                        pass
                    self._producer = None
                logger.error("Failed to connect to Kafka: %s", e)
                raise

    async def send_record(
        self,
        record: dict[str, Any],
        key: str | None = None,
        topic: str | None = None,
    ) -> None:
        await self._ensure_connection()

        target_topic = topic or self._topic
        message_key = key or record.get("_meta", {}).get("record_id", "")

        message = {
            **record,
            "_kafka_meta": {
                "topic": target_topic,
                "produced_at": datetime.now(timezone.utc).isoformat(),
            },
        }

        try:
            await self._producer.send_and_wait(
                topic=target_topic,
                value=message,
                key=message_key,
            )
            logger.info(
                "KafkaProducer: sent → %s record_id=%s",
                target_topic, message_key,
            )
        except Exception as e:
            logger.error("Failed to send record to Kafka: key=%s topic=%s error=%s", message_key, target_topic, e)
            raise

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

    async def flush(self) -> None:
        if self._producer:
            await self._producer.flush()

    async def close(self) -> None:
        self._connected = False
        if self._producer:
            await self._producer.flush()
            await self._producer.stop()
            logger.info("Kafka producer closed")
