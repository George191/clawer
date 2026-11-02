"""Debezium notifications wake workers; Mongo pending states remain the durable queue."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import defaultdict
from time import monotonic
from typing import Any

from aiokafka import AIOKafkaConsumer
from bson.codec_options import CodecOptions
from bson.json_util import loads

from app.base.kafka import build_consumer_kwargs
from app.config.settings import settings
from app.logger import get_logger
from app.storage.mongo_client import MongoClient

logger = get_logger(__name__)
MONGO_CDC_GROUP_PREFIX = "spider-mongo-cdc-"

# These writes maintain worker ownership/ACKs, without changing a business version.
_CONTROL_FIELDS = frozenset({
    "_meta.sync_status", "_meta.synced_at", "_meta.download_status",
    "_meta.download_claim_token", "_meta.download_claimed_at", "_meta.download_attempts",
})


def mongo_cdc_topic_prefix() -> str:
    return f"{settings.kafka_topic}-mongo-cdc"


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, (str, bytes)):
        value = json.loads(value)
    if not isinstance(value, dict):
        return {}
    value = value.get("payload") if "schema" in value else value
    return value if isinstance(value, dict) else {}


def _business_change(event: dict[str, Any]) -> bool:
    if event.get("op") not in {"c", "r", "u"}:
        # Physical deletes have no downstream deletion contract yet.
        return False
    description = event.get("updateDescription")
    if event["op"] != "u" or not description:
        # Mongo replace operations have no updateDescription.
        return True
    if not isinstance(description, dict):
        raise ValueError("Invalid update description")
    updated = description.get("updatedFields") or {}
    if isinstance(updated, str):
        updated = json.loads(updated)
    fields = set(updated) | set(description.get("removedFields") or [])
    return bool(description.get("truncatedArrays")) or bool(fields - _CONTROL_FIELDS)


class MongoChangeFeed(MongoClient):
    """Each role/scope has independent Kafka offsets and reads current Mongo versions.

    Offsets advance after Mongo reads, before downstream processing. A failed send,
    rejected claim, or process exit is recovered from Mongo pending/expired claims.
    Events are notifications, never authoritative snapshots or download ownership.
    """

    def __init__(
        self, *, role: str, data_type: str | None = None, template_name: str | None = None,
    ) -> None:
        super().__init__()
        if role not in {"downloader", "syncer"}:
            raise ValueError("Unknown Mongo CDC worker role")
        self._role = role
        self._data_type = data_type
        scope = json.dumps([mongo_cdc_topic_prefix(), settings.db_name, data_type, template_name])
        self.group_id = f"{MONGO_CDC_GROUP_PREFIX}{role}-{hashlib.sha256(scope.encode()).hexdigest()[:16]}"
        self._consumer: AIOKafkaConsumer | None = None
        self._startup: asyncio.Task | None = None
        self._paused_partitions: set = set()
        self._collections: tuple[str, ...] = ()
        self._retry_at = 0.0
        self.has_more = False

    async def receive(self, *, collections: list[str], limit: int) -> list[dict[str, Any]]:
        self.has_more = False
        if not collections or limit <= 0 or monotonic() < self._retry_at:
            return []
        try:
            # Kafka polling and Mongo reads must not hold up compensation scans.
            return await asyncio.wait_for(self._receive(collections, limit), timeout=2)
        except Exception as exc:
            await self._recover_connection(exc)
            return []

    async def keep_alive(self) -> None:
        """Poll without consuming while download slots are occupied."""
        if self._consumer is None:
            return
        try:
            if not await self._finish_startup():
                return
            await asyncio.wait_for(self._poll_paused(), timeout=2)
        except Exception as exc:
            await self._recover_connection(exc)

    async def _poll_paused(self) -> None:
        consumer = self._consumer
        partitions = consumer.assignment() - consumer.paused()
        consumer.pause(*partitions)
        self._paused_partitions.update(partitions)
        # Keep the assignment paused between polls. Briefly resuming each time
        # would repeatedly fetch and discard buffered records during long downloads.
        await consumer.getmany(timeout_ms=0)

    async def _recover_connection(self, exc: Exception) -> None:
        logger.warning(
            "Mongo CDC unavailable (%s); recovering from Mongo pending records",
            type(exc).__name__,
        )
        self._retry_at = monotonic() + 10
        await self._disconnect()

    async def _finish_startup(self) -> bool:
        if self._startup is not None:
            if not self._startup.done():
                return False
            await self._startup
            self._startup = None
        return True

    async def _receive(self, collections: list[str], limit: int) -> list[dict[str, Any]]:
        names = tuple(sorted(set(collections)))
        if self._consumer is None:
            self._consumer = AIOKafkaConsumer(**build_consumer_kwargs(
                group_id=self.group_id, metadata_max_age_ms=10000,
                max_partition_fetch_bytes=16 * 1024 * 1024,
                # Decode per record so malformed notifications cannot poison a partition.
                key_deserializer=None, value_deserializer=None,
            ))
            self._subscribe(names)
            # Group assignment can take several seconds. Start in the background
            # so normal Kafka rebalances do not delay Mongo compensation.
            self._startup = asyncio.create_task(
                asyncio.wait_for(self._consumer.start(), timeout=30),
            )
        elif names != self._collections:
            self._subscribe(names)
        if not await self._finish_startup():
            return []
        if self._paused_partitions:
            self._consumer.resume(*(self._paused_partitions & self._consumer.assignment()))
            self._paused_partitions.clear()

        batches = await self._consumer.getmany(timeout_ms=100, max_records=limit)
        self.has_more = sum(len(messages) for messages in batches.values()) >= limit
        ids: dict[str, list[Any]] = defaultdict(list)
        offsets = {}
        for partition, messages in batches.items():
            for message in messages:
                try:
                    event = _payload(message.value)
                    source = event.get("source") or {}
                    if not isinstance(source, dict):
                        raise ValueError("Invalid change source")
                    collection = source.get("collection")
                    if source.get("db") != settings.db_name or collection not in names:
                        continue
                    if not _business_change(event):
                        continue
                    key = _payload(message.key)
                    ids[collection].append(loads(key["id"]))
                except (KeyError, TypeError, ValueError):
                    # Invalid notifications must not block durable Mongo recovery.
                    logger.warning("Invalid Mongo CDC notification; Mongo scan will recover it")
            if messages:
                offsets[partition] = messages[-1].offset + 1

        records = []
        if ids:
            await self._ensure_connection()
            status_filter = (
                {"_meta.download_status": "pending"} if self._role == "downloader"
                else {"_meta.sync_status": {"$ne": "synced"}}
            )
            for name, document_ids in ids.items():
                query = {"_id": {"$in": document_ids}, **status_filter}
                if self._data_type:
                    query["_meta.data_type"] = self._data_type
                collection = self._db[name].with_options(codec_options=CodecOptions(tz_aware=True))
                async for record in collection.find(query):
                    meta = record.get("_meta") or {}
                    if meta.get("template") != name or not meta.get("record_id"):
                        continue
                    record.pop("_id", None)
                    records.append(record)
        if offsets:
            await self._consumer.commit(offsets)
        return records

    def _subscribe(self, names: tuple[str, ...]) -> None:
        prefix = re.escape(f"{mongo_cdc_topic_prefix()}.{settings.db_name}.")
        collections = "|".join(re.escape(name) for name in names)
        self._consumer.subscribe(pattern=rf"^{prefix}(?:{collections})$")
        self._collections = names

    async def _disconnect(self) -> None:
        self._paused_partitions.clear()
        startup, self._startup = self._startup, None
        if startup is not None:
            startup.cancel()
            await asyncio.gather(startup, return_exceptions=True)
        consumer, self._consumer = self._consumer, None
        if consumer is not None:
            try:
                await asyncio.wait_for(consumer.stop(), timeout=2)
            except Exception:
                logger.warning("Mongo CDC consumer close failed")

    async def close(self) -> None:
        try:
            await self._disconnect()
        finally:
            await super().close()
