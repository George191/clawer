"""ETL 基础组件 — ETLBase 抽象基类。

核心特性：
- Kafka 生产者 / 消费者封装（自动重连、Offset Redis 持久化）
- 反射机制：_handler_<表名> 方法自动发现为表处理器
- 分区表自动创建（按月分区）
- Schema 管理（自动建 schema + 表）
- 恢复器：支持从指定 offset 恢复消费

子类使用方式：
    class TsRds(ETLBase):
        _layer = "rds"
        _consumer_topics = [settings.etl_raw_topic]
        _consumer_group = settings.etl_rds_consumer_group
        _producer_topics = [settings.etl_rds_topic]

        def _handler_raw_records(self, message: dict) -> bool:
            ...

删除 _handler_raw_records 方法即删除该表的处理能力。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, ClassVar, TypeVar

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from aiokafka.errors import NodeNotReadyError, KafkaConnectionError, RequestTimedOutError

from app.config.settings import settings
from app.etl.offset_manager import get_offset_manager
from app.etl.table_layout import partition_bounds, schema_name_for
from app.storage.etl_metadata_store import (
    ensure_ddlregistry_table,
    get_registered_ddl,
    get_registered_ddl_record,
)
from app.storage.postgres_client import get_pg_client

logger = logging.getLogger(__name__)

KAFKA_RETRY_WAIT = 3
KAFKA_MAX_RETRY = 30
KAFKA_MAX_RETRY_DELAY = 60

_OFFSET_MANAGER = get_offset_manager()
T = TypeVar("T")


class ETLBase:
    _layer: ClassVar[str] = ""
    _table_role: ClassVar[str] = "current"
    _consumer_topics: ClassVar[list[str]] = []
    _consumer_group: ClassVar[str] = ""
    _producer_topic: ClassVar[str] = ""
    _producer_client_id: ClassVar[str] = ""

    def __init__(self, table_name: str = "") -> None:
        self._table_name = table_name
        self._log_prefix = f"[{self._layer.upper()}]"
        self._handlers = self._discover_handlers()
        self._consumer: AIOKafkaConsumer | None = None
        self._producer: AIOKafkaProducer | None = None
        self._pg = get_pg_client()
        self._running = False
        self._pending_offsets: dict[str, int] = {}

    @staticmethod
    def _offset_key(topic: str, partition: int) -> str:
        return f"{topic}:{partition}"

    # =================================================================
    #  反射机制: 自动发现 _handler_<表名> 方法
    # =================================================================
    def _discover_handlers(self) -> dict[str, Callable[[dict[str, Any]], Awaitable[bool]]]:
        handlers: dict[str, Callable[[dict[str, Any]], Awaitable[bool]]] = {}
        for attr in dir(self):
            if not attr.startswith("_handler_"):
                continue
            suffix = attr[len("_handler_"):]
            if not suffix:
                continue
            if self._table_name and suffix != self._table_name:
                continue
            handlers[suffix] = getattr(self, attr)
        logger.info(
            "%s Discovered %d table handler(s): %s",
            self._log_prefix,
            len(handlers),
            sorted(handlers.keys()),
        )
        return handlers

    def _table_fqn(self, table: str, table_role: str | None = None) -> str:
        role = table_role or self._table_role
        return f"{schema_name_for(self._layer, role)}.{self._layer}_{table}"

    # =================================================================
    #  Schema & 分区表
    # =================================================================
    async def _on_init_schema(self) -> None:
        await self._pg.execute(
            f"CREATE SCHEMA IF NOT EXISTS {schema_name_for(self._layer, self._table_role)}"
        )
        await ensure_ddlregistry_table(self._pg)
        for table in self._handlers:
            await self._ensure_registered_table(table, self._table_role)
            if self._table_role == "current":
                await self._ensure_registered_table(table, "history")

    async def _ddl_for_table(self, table: str) -> str:
        ddl_from_registry = await get_registered_ddl(
            self._pg,
            self._layer,
            table,
            self._table_role,
        )
        if ddl_from_registry:
            return ddl_from_registry

        logger.warning(
            "%s No DDL defined for table '%s' in registry",
            self._log_prefix,
            table,
        )
        return ""

    async def _ensure_registered_table(self, table: str, table_role: str) -> None:
        ddl_record = await get_registered_ddl_record(
            self._pg,
            self._layer,
            table,
            table_role,
        )
        if not ddl_record:
            if table_role == self._table_role:
                await self._ddl_for_table(table)
            return

        await self._pg.execute(
            f"CREATE SCHEMA IF NOT EXISTS {schema_name_for(self._layer, table_role)}"
        )
        await self._pg.init_schema([ddl_record["ddl_sql"]])
        logger.info(
            "%s Table ensured: %s",
            self._log_prefix,
            self._table_fqn(table, table_role),
        )
        await self._create_partitions(table, table_role=table_role, ddl_record=ddl_record)

    async def _create_partitions(
        self,
        table: str,
        table_role: str | None = None,
        ddl_record: dict[str, Any] | None = None,
    ) -> None:
        role = table_role or self._table_role
        if ddl_record is None:
            ddl_record = await get_registered_ddl_record(
                self._pg,
                self._layer,
                table,
                role,
            )
        if not ddl_record:
            return

        partition_type = ddl_record.get("partition_type")
        if partition_type == "hash":
            await self._create_hash_partitions(table, role, ddl_record)
            return

        if partition_type != "range":
            return

        granularity = ddl_record.get("partition_granularity")
        if granularity not in {"month", "year"}:
            logger.warning(
                "%s Unsupported partition granularity for %s: %s",
                self._log_prefix,
                self._table_fqn(table, role),
                granularity,
            )
            return

        if not await self._is_partitioned_table(table, role):
            logger.warning(
                "%s Partitioned DDL exists but physical table is not partitioned: %s",
                self._log_prefix,
                self._table_fqn(table, role),
            )
            return

        now = datetime.now(timezone.utc)
        range_start, range_end, suffix = partition_bounds(now, granularity)
        schema_name = schema_name_for(self._layer, role)
        partition_name = f"{schema_name}.{self._layer}_{table}_{suffix}"
        partition_sql = (
            f"CREATE TABLE IF NOT EXISTS {partition_name} "
            f"PARTITION OF {self._table_fqn(table, role)} "
            f"FOR VALUES FROM ('{range_start:%Y-%m-%d}') TO ('{range_end:%Y-%m-%d}')"
        )
        try:
            await self._pg.execute(partition_sql)
            default_partition_sql = (
                f"CREATE TABLE IF NOT EXISTS {schema_name}.{self._layer}_{table}_default "
                f"PARTITION OF {self._table_fqn(table, role)} DEFAULT"
            )
            await self._pg.execute(default_partition_sql)
            logger.debug("%s Partition ensured: %s", self._log_prefix, partition_name)
        except Exception as e:
            logger.info(
                "%s Partition skip for %s (maybe not a partitioned table): %s",
                self._log_prefix, self._table_fqn(table, role), e,
            )

    async def _create_hash_partitions(
        self,
        table: str,
        table_role: str,
        ddl_record: dict[str, Any],
    ) -> None:
        partition_count = ddl_record.get("partition_count")
        if not isinstance(partition_count, int) or partition_count <= 0:
            logger.warning(
                "%s Unsupported hash partition count for %s: %s",
                self._log_prefix,
                self._table_fqn(table, table_role),
                partition_count,
            )
            return

        if not await self._is_partitioned_table(table, table_role):
            logger.warning(
                "%s Hash-partitioned DDL exists but physical table is not partitioned: %s",
                self._log_prefix,
                self._table_fqn(table, table_role),
            )
            return

        schema_name = schema_name_for(self._layer, table_role)
        parent_table = self._table_fqn(table, table_role)
        width = max(2, len(str(partition_count - 1)))
        for remainder in range(partition_count):
            partition_name = (
                f"{schema_name}.{self._layer}_{table}_p{remainder:0{width}d}"
            )
            partition_sql = (
                f"CREATE TABLE IF NOT EXISTS {partition_name} "
                f"PARTITION OF {parent_table} "
                f"FOR VALUES WITH (MODULUS {partition_count}, REMAINDER {remainder})"
            )
            await self._pg.execute(partition_sql)

    async def _recover_registered_table(
        self,
        table: str,
        table_role: str | None = None,
    ) -> None:
        role = table_role or self._table_role
        await self._ensure_registered_table(table, role)
        if role == "current":
            await self._ensure_registered_table(table, "history")

    @staticmethod
    def _schema_error_messages(exc: BaseException) -> list[str]:
        messages: list[str] = []
        seen: set[int] = set()
        current: BaseException | None = exc

        while current is not None and id(current) not in seen:
            seen.add(id(current))
            text = str(current).strip()
            if text:
                messages.append(text)

            nested = getattr(current, "orig", None)
            if isinstance(nested, BaseException) and id(nested) not in seen:
                current = nested
                continue

            current = current.__cause__ or current.__context__

        return messages

    def _is_recoverable_table_error(self, exc: BaseException) -> bool:
        message = " | ".join(self._schema_error_messages(exc)).lower()
        return (
            "no partition of relation" in message
            or "undefinedtableerror" in message
            or ('relation "' in message and '" does not exist' in message)
        )

    async def _execute_with_table_recovery(
        self,
        table: str,
        operation: Callable[[], Awaitable[T]],
        *,
        table_role: str | None = None,
    ) -> T:
        role = table_role or self._table_role
        try:
            return await operation()
        except Exception as exc:
            if not self._is_recoverable_table_error(exc):
                raise

            logger.warning(
                "%s Recovering table=%s role=%s after schema error: %s",
                self._log_prefix,
                table,
                role,
                self._schema_error_messages(exc)[0] if self._schema_error_messages(exc) else exc,
            )
            await self._recover_registered_table(table, role)
            return await operation()

    async def _is_partitioned_table(self, table: str, table_role: str | None = None) -> bool:
        role = table_role or self._table_role
        row = await self._pg.fetch_one(
            """
            SELECT 1
            FROM pg_partitioned_table pt
            JOIN pg_class c ON c.oid = pt.partrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :schema_name
              AND c.relname = :table_name
            LIMIT 1
            """,
            {
                "schema_name": schema_name_for(self._layer, role),
                "table_name": f"{self._layer}_{table}",
            },
        )
        return row is not None

    # =================================================================
    #  Kafka 生产者
    # =================================================================
    async def _connect_producer(self) -> None:
        brokers = _brokers()
        retryable = (NodeNotReadyError, KafkaConnectionError, RequestTimedOutError, ConnectionError, OSError)

        for attempt in range(KAFKA_MAX_RETRY):
            try:
                self._producer = AIOKafkaProducer(
                    bootstrap_servers=brokers,
                    client_id=self._producer_client_id,
                    value_serializer=lambda v: json.dumps(v, ensure_ascii=False, default=str).encode("utf-8"),
                    key_serializer=lambda k: k.encode("utf-8") if k else None,
                    acks="all",
                    request_timeout_ms=30000,
                )
                await self._producer.start()
                logger.info("%s Kafka producer connected: client=%s", self._log_prefix, self._producer_client_id)
                return
            except retryable as e:
                delay = min(KAFKA_RETRY_WAIT * (2 ** min(attempt, 5)), KAFKA_MAX_RETRY_DELAY)
                logger.warning("%s Kafka producer retry %d/%d: %s", self._log_prefix, attempt + 1, KAFKA_MAX_RETRY, e)
                await asyncio.sleep(delay)

        raise ConnectionError(f"{self._log_prefix} Kafka producer connection FAILED")

    async def _emit(
            self, 
            value: dict[str, Any], 
            record_id: str | None = None, 
            data_source: str | None = None,
            data_type: str | None = None,
            topic: str | None = None,
        ) -> None:
        if not self._producer:
            await self._connect_producer()

        await self._producer.send_and_wait(
            topic=self._producer_topic,
            value=value,
            key=record_id,
        )
        logger.info(
            "%s Emitted → %s record_id=%s source=%s type=%s",
            self._log_prefix, self._producer_topic, record_id, data_source, data_type,
        )

    # =================================================================
    #  Kafka 消费者 & 恢复器
    # =================================================================
    async def _connect_consumer(self) -> None:
        brokers = _brokers()
        retryable = (NodeNotReadyError, KafkaConnectionError, RequestTimedOutError, ConnectionError, OSError)

        for attempt in range(KAFKA_MAX_RETRY):
            try:
                self._consumer = AIOKafkaConsumer(
                    *self._consumer_topics,
                    bootstrap_servers=brokers,
                    group_id=self._consumer_group,
                    value_deserializer=lambda v: json.loads(v.decode("utf-8")) if v else None,
                    key_deserializer=lambda k: k.decode("utf-8") if k else None,
                    auto_offset_reset="earliest",
                    enable_auto_commit=False,
                    max_poll_records=100,
                    session_timeout_ms=30000,
                    heartbeat_interval_ms=10000,
                )
                await self._consumer.start()
                logger.info(
                    "%s Kafka consumer connected: topics=%s group=%s",
                    self._log_prefix, self._consumer_topics, self._consumer_group,
                )
                await self._resume_from_redis()
                return
            except retryable as e:
                delay = min(KAFKA_RETRY_WAIT * (2 ** min(attempt, 5)), KAFKA_MAX_RETRY_DELAY)
                logger.warning(
                    "%s Kafka consumer retry %d/%d: %s",
                    self._log_prefix, attempt + 1, KAFKA_MAX_RETRY, e,
                )
                await asyncio.sleep(delay)

        raise ConnectionError(f"{self._log_prefix} Kafka consumer connection FAILED")

    async def _resume_from_redis(self) -> None:
        for topic in self._consumer_topics:
            saved = await _OFFSET_MANAGER.load_offsets(self._consumer_group, topic)

            if not saved:
                logger.info("%s No saved offsets for topic=%s", self._log_prefix, topic)
                continue

            for partition, offset in saved.items():
                tp = TopicPartition(topic, partition)
                try:
                    self._consumer.seek(tp, offset)
                    logger.info(
                        "%s Resume: topic=%s partition=%d → kafka_offset=%d",
                        self._log_prefix, topic, partition, offset,
                    )
                except Exception as e:
                    logger.warning("%s Seek failed for topic=%s partition=%d: %s", self._log_prefix, topic, partition, e)

    async def resume(self, offset: int, partition: int = 0, topic: str = "") -> None:
        topic = topic or self._consumer_topics[0]
        tp = TopicPartition(topic, partition)
        self._consumer.seek(tp, offset)
        await _OFFSET_MANAGER.reset_offsets(self._consumer_group, topic)
        logger.info("%s Manual resume: topic=%s partition=%d → offset=%d", self._log_prefix, topic, partition, offset)

    async def _commit_and_save(self, topic: str, partition: int, offset: int) -> None:
        await self._consumer.commit()
        key = self._offset_key(topic, partition)
        self._pending_offsets[key] = offset + 1
        for pending_key, off in self._pending_offsets.items():
            t, p = pending_key.split(":", 1)
            await _OFFSET_MANAGER.save_offset(
                self._consumer_group, t, int(p), off,
            )
        self._pending_offsets.clear()

    # =================================================================
    #  消息消费 & 分发循环
    # =================================================================
    async def run(self) -> None:
        self._running = True

        logger.info("%s Connecting to Postgres...", self._log_prefix)
        await self._pg.connect()
        logger.info("%s Postgres connected", self._log_prefix)

        logger.info("%s Connecting to Kafka...", self._log_prefix)
        await self._connect_consumer()
        await self._connect_producer()

        logger.info("%s Initializing schema...", self._log_prefix)
        await self._on_init_schema()

        handler_count = len(self._handlers)
        logger.info(
            "%s STARTED: consume=%s → produce=%s [%d table(s)]",
            self._log_prefix, self._consumer_topics, self._producer_topic, handler_count,
        )

        await self._consume_loop()

    async def _consume_loop(self) -> None:
        if not self._consumer:
            await self._connect_consumer()

        while self._running:
            try:
                records = await self._consumer.getmany(timeout_ms=5000, max_records=50)
                for _tp, msgs in records.items():
                    for msg in msgs:
                        if msg.value is None:
                            continue
                        try:
                            msg.value["_kafka_meta"] = {
                                "kafka_offset": msg.offset,
                                "kafka_partition": msg.partition,
                                "kafka_topic": msg.topic,
                            }
                            success = await self._dispatch(msg.value)
                            if success:
                                await self._commit_and_save(msg.topic, msg.partition, msg.offset)
                        except Exception:
                            logger.exception("%s Handler error", self._log_prefix)
            except Exception:
                logger.exception("%s Consumer loop error, reconnecting...", self._log_prefix)
                await asyncio.sleep(5)

    async def _dispatch(self, message: dict[str, Any]) -> bool:
        meta = extract_meta(message)
        data_type = meta.get("data_type") or message.get("data_type")

        # 优先 topic-aware handler: _handler_{data_type}_{topic_hint}
        kafka_meta = message.get("_kafka_meta", {}) or {}
        topic = kafka_meta.get("kafka_topic", "")

        # fallback 通用 handler: _handler_{data_type}
        handler = self._handlers.get(data_type)
        if handler:
            return await handler(message)

        logger.warning(
            "%s No handler for data_type='%s' topic='%s', available handlers: %s",
            self._log_prefix,
            data_type,
            topic,
            sorted(self._handlers.keys()),
        )
        return False

    # =================================================================
    #  优雅关闭
    # =================================================================
    async def stop(self) -> None:
        self._running = False
        if self._consumer:
            try:
                await self._consumer.stop()
            except Exception:
                pass
        if self._producer:
            try:
                await self._producer.flush()
            except Exception:
                pass
            try:
                await self._producer.stop()
            except Exception:
                pass
        try:
            await self._pg.close()
        except Exception:
            pass
        logger.info("%s Stopped", self._log_prefix)


def _brokers() -> list[str]:
    return [b.strip() for b in settings.kafka_brokers.split(",") if b.strip()]


PIPELINE_VERSION = "2.0"


def extract_meta(message: dict[str, Any]) -> dict[str, Any]:
    raw_meta = message.get("_meta", {}) or {}
    return {
        "data_source": raw_meta.get("data_source", "") or raw_meta.get("template", ""),
        "data_type": raw_meta.get("data_type", ""),
        "record_id": raw_meta.get("record_id", ""),
    }
