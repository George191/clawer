"""Relay committed PostgreSQL CDC snapshots and offsets in Kafka transactions."""

from __future__ import annotations

import argparse
import asyncio
import re

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import ProducerFenced

from app.base.kafka import (
    build_consumer_kwargs,
    build_producer_kwargs,
    consumer_subscription_pattern,
)
from app.config.settings import settings
from app.etl.cdc import CDC_LAYERS, committed_row, row_outputs
from app.logger import get_logger, setup_service_logging
from app.storage.business_database import BusinessPostgresClient, LayerBindingChanged, layer_binding_lease
from app.storage.postgres_client import get_pg_client
from app.syncer.postgres_cdc import (
    internal_cdc_clients,
    postgres_cdc_identity,
    published_cdc_objects,
)

logger = get_logger(__name__)


class PostgresSyncWorker:
    """One fenced relay scope publishes exact CDC rows without re-reading business rows.

    A transaction includes every business output plus source offsets. Aborts and
    poison events never advance offsets; downstream consumers use read_committed.
    The stable transactional ID intentionally permits one active relay instance.
    """

    def __init__(self, pg: BusinessPostgresClient, identity: dict[str, str], *, batch_size: int = 50) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._batch_size = batch_size
        self._pg = pg
        self._identity = identity
        self._topics = {}
        self.group_id = f"{self._identity['prefix']}-relay"
        self._consumer = None
        self._producer = None
        self._running = False

    async def _publish_batch(self, partition, messages) -> None:
        """Publish all derived outputs and the partition offset atomically."""
        schema, table, layer, data_type = self._topics[partition.topic]
        async with self._producer.transaction():
            for message in messages:
                event = committed_row(message.value, identity=self._identity, schema_name=schema,
                                      table=table, layer=layer, data_type=data_type)
                if event is None:
                    continue
                layer, _, row = event
                for topic, value in row_outputs(layer, row):
                    await self._producer.send_and_wait(topic, value=value, key=row["record_id"])
            await self._producer.send_offsets_to_transaction(
                {partition: messages[-1].offset + 1}, self.group_id,
            )

    async def _refresh_subscription(self) -> None:
        objects = await asyncio.wait_for(published_cdc_objects(self._pg, self._identity), timeout=12)
        topics = {f"{self._identity['prefix']}.{schema}.{table}": (schema, table, layer, data_type)
                  for (schema, table), (layer, data_type) in objects.items()}
        if topics == self._topics:
            return
        self._topics = topics
        alternatives = "|".join(re.escape(topic) for topic in sorted(topics))
        pattern = rf"^(?:{alternatives})\Z" if alternatives else r"(?!)"
        self._consumer.subscribe(pattern=consumer_subscription_pattern(pattern))

    async def run(self, bindings) -> None:
        """Consume bounded batches; source messages remain durable during failures."""
        self._producer = AIOKafkaProducer(**build_producer_kwargs(
            transactional_id=self.group_id, enable_idempotence=True,
            transaction_timeout_ms=60000,
        ))
        self._consumer = AIOKafkaConsumer(**build_consumer_kwargs(
            group_id=self.group_id, isolation_level="read_committed", metadata_max_age_ms=10000,
            max_partition_fetch_bytes=16 * 1024 * 1024,
        ))
        self._running = True
        try:
            await self._producer.start()
            await self._consumer.start()
            next_refresh = 0
            loop = asyncio.get_running_loop()
            while self._running:
                if loop.time() >= next_refresh:
                    async with layer_binding_lease(CDC_LAYERS, expected=bindings):
                        await self._refresh_subscription()
                    next_refresh = loop.time() + 10
                if not self._consumer.assignment():
                    await asyncio.sleep(1)
                    continue
                batches = await self._consumer.getmany(timeout_ms=1000, max_records=self._batch_size)
                for partition, messages in batches.items():
                    if not messages or partition.topic not in self._topics:
                        continue
                    try:
                        async with layer_binding_lease(CDC_LAYERS, expected=bindings):
                            await self._publish_batch(partition, messages)
                    except (ProducerFenced, LayerBindingChanged):
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
    parser.add_argument("--layer", choices=CDC_LAYERS, help="Limit the relay to this layer's saved connection")
    args = parser.parse_args()
    setup_service_logging("postgres-syncer", settings.log_level)
    async def run():
        clients = []
        try:
            async with layer_binding_lease(CDC_LAYERS) as bindings:
                clients = await internal_cdc_clients(args.layer)
                workers = []
                for pg in clients:
                    await pg.connect(max_retries=1)
                    workers.append(PostgresSyncWorker(pg, await postgres_cdc_identity(pg), batch_size=args.batch))
            async with asyncio.TaskGroup() as relays:
                for worker in workers:
                    relays.create_task(worker.run(bindings))
        finally:
            await asyncio.gather(*(pg.close() for pg in clients))
            await get_pg_client().close()

    asyncio.run(run())


if __name__ == "__main__":
    main()
