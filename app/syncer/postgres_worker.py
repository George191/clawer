"""Relay committed PostgreSQL CDC snapshots and offsets in Kafka transactions."""

from __future__ import annotations

import argparse
import asyncio
import re

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import ProducerFenced

from app.base.kafka import build_consumer_kwargs, build_producer_kwargs
from app.config.settings import settings
from app.etl.cdc import cdc_schemas, committed_row, row_outputs
from app.logger import get_logger, setup_service_logging
from app.syncer.postgres_cdc import postgres_cdc_identity

logger = get_logger(__name__)


class PostgresSyncWorker:
    """One fenced relay scope publishes exact CDC rows without querying Postgres.

    A transaction includes every business output plus source offsets. Aborts and
    poison events never advance offsets; downstream consumers use read_committed.
    The stable transactional ID intentionally permits one active relay instance.
    """

    def __init__(self, *, batch_size: int = 50) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._batch_size = batch_size
        self._identity = postgres_cdc_identity()
        self.group_id = f"{self._identity['prefix']}-relay"
        self._consumer = None
        self._producer = None
        self._running = False

    async def _publish_batch(self, partition, messages) -> None:
        """Publish all derived outputs and the partition offset atomically."""
        async with self._producer.transaction():
            for message in messages:
                event = committed_row(message.value)
                if event is None:
                    continue
                layer, _, row = event
                for topic, value in row_outputs(layer, row):
                    await self._producer.send_and_wait(topic, value=value, key=row["record_id"])
            await self._producer.send_offsets_to_transaction(
                {partition: messages[-1].offset + 1}, self.group_id,
            )

    async def run(self) -> None:
        """Consume bounded batches; source messages remain durable during failures."""
        self._producer = AIOKafkaProducer(**build_producer_kwargs(
            transactional_id=self.group_id, enable_idempotence=True,
            transaction_timeout_ms=60000,
        ))
        self._consumer = AIOKafkaConsumer(**build_consumer_kwargs(
            group_id=self.group_id, isolation_level="read_committed", metadata_max_age_ms=10000,
            max_partition_fetch_bytes=16 * 1024 * 1024,
        ))
        schemas = "|".join(re.escape(schema) for schema in cdc_schemas())
        self._consumer.subscribe(
            pattern=rf"^{re.escape(self._identity['prefix'])}\.(?:{schemas})\.[a-z][a-z0-9_]*$",
        )
        self._running = True
        try:
            await self._producer.start()
            await self._consumer.start()
            while self._running:
                batches = await self._consumer.getmany(timeout_ms=1000, max_records=self._batch_size)
                for partition, messages in batches.items():
                    if not messages:
                        continue
                    try:
                        await self._publish_batch(partition, messages)
                    except ProducerFenced:
                        # A replacement owns the stable transaction ID. The old
                        # process must exit instead of competing for ownership.
                        raise
                    except Exception as exc:
                        # Schema/value failures are visible and retried, never skipped.
                        logger.error(
                            "Postgres CDC batch retained topic=%s partition=%s offset=%s error=%s",
                            partition.topic, partition.partition, messages[0].offset, type(exc).__name__,
                        )
                        if partition in self._consumer.assignment():
                            self._consumer.seek(partition, messages[0].offset)
                        else:
                            # An old assignment must not publish the next fetched batch.
                            break
                        await asyncio.sleep(1)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Release consumer and producer even after a transaction/startup failure."""
        self._running = False
        consumer, self._consumer = self._consumer, None
        producer, self._producer = self._producer, None
        try:
            if consumer is not None:
                await consumer.stop()
        finally:
            if producer is not None:
                await producer.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=int, default=50)
    args = parser.parse_args()
    setup_service_logging("postgres-syncer", settings.log_level)
    asyncio.run(PostgresSyncWorker(batch_size=args.batch).run())


if __name__ == "__main__":
    main()
