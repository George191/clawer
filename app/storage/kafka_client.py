"""Kafka 客户端 — 管理连接、重试和消息传输。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from app.base.kafka import build_producer_kwargs
from app.config.settings import settings
from app.logger import get_logger

logger = get_logger(__name__)

KAFKA_CONNECT_RETRY_WAIT = 3
KAFKA_CONNECT_MAX_RETRY_DELAY = 600


class KafkaClient:
    def __init__(self, topic: str | None = None) -> None:
        self._producer = None
        self._topic = topic or settings.kafka_topic
        self._connected = False
        self._connect_lock = asyncio.Lock()

    async def _ensure_connection(self) -> None:
        async with self._connect_lock:
            await self._connect()

    async def _connect(self) -> None:
        if self._producer is not None and self._connected:
            return

        from aiokafka import AIOKafkaProducer
        from aiokafka.errors import (
            KafkaConnectionError,
            NodeNotReadyError,
            RequestTimedOutError,
        )

        retryable = (NodeNotReadyError, KafkaConnectionError, RequestTimedOutError, ConnectionError, OSError)

        attempt = 0
        while True:
            try:
                self._producer = AIOKafkaProducer(
                    **build_producer_kwargs(
                        client_id=settings.kafka_client_id,
                        enable_idempotence=settings.kafka_enable_idempotence,
                    )
                )
                await self._producer.start()

                await asyncio.sleep(1)

                try:
                    await self._producer.partitions_for(self._topic)
                except Exception:
                    await asyncio.sleep(2)
                    try:
                        await self._producer.partitions_for(self._topic)
                    except Exception:
                        logger.warning("Topic %s not found, waiting for auto-creation...", self._topic)
                        await asyncio.sleep(3)

                self._connected = True
                logger.info("Connected to Kafka: %s, topic: %s", settings.kafka_brokers, self._topic)
                return

            except retryable as e:
                self._connected = False
                if self._producer is not None:
                    with suppress(Exception):
                        await self._producer.stop()
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
                    with suppress(Exception):
                        await self._producer.stop()
                    self._producer = None
                logger.error("Failed to connect to Kafka: %s", e)
                raise

    async def send(
        self,
        value: Any,
        key: str | None = None,
        topic: str | None = None,
    ) -> None:
        await self._ensure_connection()
        target_topic = topic or self._topic
        producer = self._producer
        try:
            await producer.send_and_wait(topic=target_topic, value=value, key=key)
        except Exception as e:
            from aiokafka.errors import OutOfOrderSequenceNumber

            if isinstance(e, OutOfOrderSequenceNumber):
                async with self._connect_lock:
                    # A late failure from the old epoch must not close its replacement.
                    if self._producer is producer:
                        self._producer = None
                        self._connected = False
                        with suppress(Exception):
                            await producer.stop()
            logger.error("Failed to send Kafka message: key=%s topic=%s error=%s", key, target_topic, e)
            raise

    async def flush(self) -> None:
        if self._producer:
            await self._producer.flush()

    async def close(self) -> None:
        self._connected = False
        if self._producer:
            await self._producer.flush()
            await self._producer.stop()
            logger.info("Kafka producer closed")
