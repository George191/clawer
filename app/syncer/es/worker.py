"""Project versioned CDC into ES; fill absent documents with bounded keyset backfills.

Live writes use committed PostgreSQL LSNs as external versions. Historical rows
are create-only, so a concurrent backfill cannot overwrite newer CDC documents.
Backfill checkpoints include timestamp and all three business identity columns.
"""

from __future__ import annotations

import asyncio
import time
import re
from typing import Any

from aiokafka import AIOKafkaConsumer
from aiokafka.admin import AIOKafkaAdminClient

from app.config.settings import settings
from app.base.kafka import build_admin_client_kwargs, build_consumer_kwargs
from app.logger import get_logger
from app.storage.postgres_client import PostgresClient, get_pg_client
from app.syncer.es.config import SyncConfig, TableSyncConfig, build_doc_from_row
from app.syncer.es.metrics import SyncMetrics
from app.syncer.es.watermark import BackfillCursor, WatermarkStore

logger = get_logger(__name__)


def _cdc_version(row: dict[str, Any]) -> int | None:
    """Only positive signed-long LSNs may advance an externally versioned ES document."""
    value = (row.get("_cdc_meta") or {}).get("lsn")
    if value is not None and (type(value) is not int or not 0 < value < 2**63):
        raise ValueError("ES CDC requires a positive signed-long LSN")
    return value


class EsSyncWorker:
    """ES 同步 Worker — RDS → Elasticsearch 增量同步。

    用法:
        worker = EsSyncWorker()
        await worker.run()  # 阻塞，直到 stop()

    对标 SyncWorker:
        SyncWorker:  MongoDB → Kafka
        EsSyncWorker: RDS(PG) → Elasticsearch
    """

    def __init__(
        self,
        config: SyncConfig | None = None,
        pg: PostgresClient | None = None,
        es: Any = None,
    ) -> None:
        self._config = config or SyncConfig.from_settings()
        self._config.__post_init__()
        self._pg = pg
        self._es = es
        self._watermark_store = WatermarkStore(pg=pg)
        self._metrics = SyncMetrics(syncer_name=self._config.syncer_name)
        self._running = False
        self._consumer = None
        self._ready_indices: set[str] = set()

    # ── 依赖管理（与 SyncWorker 一致的延迟初始化） ────────────────────

    def _get_pg(self) -> PostgresClient:
        if self._pg is None:
            self._pg = get_pg_client()
        return self._pg

    def _get_es(self) -> Any:
        if self._es is None:
            from elasticsearch import AsyncElasticsearch

            if not self._config.es_url:
                raise ValueError("Elasticsearch URL is required")
            auth = ((self._config.es_username, self._config.es_password)
                    if self._config.es_username else None)
            self._es = AsyncElasticsearch(self._config.es_url, basic_auth=auth)
        return self._es

    # ── 主循环（与 SyncWorker.run() 结构一致） ────────────────────────

    async def _run_cdc(self) -> None:
        """Project committed RDS messages into ES, acknowledging only complete batches."""
        routes = {f"{settings.etl_rds_topic}-{table.table_name}": table for table in self._config.tables}
        if not routes or any(table.schema_name != "ts_rds" for table in self._config.tables):
            raise ValueError("ES CDC requires configured RDS source tables")
        await self._validate_source_topics(set(routes))
        if not await self._get_es().ping():
            raise ConnectionError("Elasticsearch health check failed")
        self._consumer = AIOKafkaConsumer(**build_consumer_kwargs(
            group_id=f"{settings.kafka_topic}-es-{self._config.syncer_name}",
            isolation_level="read_committed",
            auto_offset_reset="latest",
        ))
        self._consumer.subscribe(pattern="^(?:" + "|".join(re.escape(topic) for topic in routes) + ")$")
        self._running = True
        try:
            await self._consumer.start()
            logger.info("ES CDC consumer assigned; create-only backfill may now fill missing documents")
            while self._running:
                batches = await self._consumer.getmany(timeout_ms=1000, max_records=self._config.batch_size)
                for partition, messages in batches.items():
                    if not messages:
                        continue
                    try:
                        rows = [message.value for message in messages]
                        if any(_cdc_version(row) is None for row in rows):
                            logger.error("ES CDC rejected unversioned messages; initialize a new cutover group and rebuild the index")
                            raise ValueError("ES CDC messages must carry a committed source LSN")
                        await self._bulk_upsert_with_retry(routes[partition.topic], rows)
                        await self._consumer.commit({partition: messages[-1].offset + 1})
                    except Exception as exc:
                        logger.error("ES CDC batch retained topic=%s offset=%s error=%s",
                                     partition.topic, messages[0].offset, type(exc).__name__)
                        if partition in self._consumer.assignment():
                            self._consumer.seek(partition, messages[0].offset)
                        await asyncio.sleep(self._config.poll_interval)
        finally:
            await self.stop()

    async def _validate_source_topics(self, topics: set[str]) -> None:
        """A new latest-offset group must not skip records from topics created after startup."""
        admin = AIOKafkaAdminClient(**build_admin_client_kwargs(request_timeout_ms=10000))
        try:
            await asyncio.wait_for(admin.start(), timeout=15)
            available = await asyncio.wait_for(admin.list_topics(), timeout=15)
            if topics - set(available):
                raise ValueError("Create every configured RDS Kafka topic before starting ES CDC")
        finally:
            await asyncio.wait_for(admin.close(), timeout=5)

    async def run(self) -> None:
        """启动同步主循环。"""
        if settings.etl_output_mode != "debezium":
            raise ValueError("Continuous ES sync requires Debezium LSNs; direct rollback needs an isolated index rebuild")
        await self._run_cdc()

    async def run_once(self) -> None:
        """Drain a bounded keyset backfill; existing documents are never overwritten."""
        await self._init_dependencies()
        for table_config in self._config.tables:
            upper_bound = await self._backfill_bound(table_config)
            if upper_bound is None:
                continue
            while True:
                count = await self._sync_table(table_config, upper_bound=upper_bound, raise_on_error=True)
                if count < self._config.batch_size:
                    break

    async def _init_dependencies(self) -> None:
        """初始化依赖（PG/ES/水位线/指标）。"""
        pg = self._get_pg()
        await pg.connect()
        await self._watermark_store.ensure_table()
        es = self._get_es()
        if not await es.ping():
            raise ConnectionError("Elasticsearch health check failed")

        # 初始化指标
        for table_config in self._config.tables:
            metrics = self._metrics.get_or_create_table(table_config)
            metrics.target_index = table_config.resolve_index_name(self._config.es_index_prefix)
            watermark = await self._watermark_store.get(
                self._config.syncer_name, table_config.source_table
            )
            metrics.initial_watermark = watermark
            metrics.last_watermark = watermark

        logger.info(
            "[ES-Syncer] Started (poll=%ds, batch=%d, tables=%d)",
            self._config.poll_interval,
            self._config.batch_size,
            len(self._config.tables),
        )
        for tc in self._config.tables:
            metrics = self._metrics.tables[tc.table_name]
            logger.info(
                "[ES-Syncer]   %s → %s (watermark=%s)",
                tc.source_table, tc.resolve_index_name(self._config.es_index_prefix),
                metrics.last_watermark.isoformat() if metrics.last_watermark else "initial",
            )

    async def stop(self) -> None:
        """停止同步并清理资源。"""
        self._running = False
        if self._consumer:
            await self._consumer.stop()
            self._consumer = None
        if self._es is not None:
            await self._es.close()
            self._es = None
        self._metrics.log_summary()
        logger.info("[ES-Syncer] Stopped")

    # ── 单表同步 ──────────────────────────────────────────────────────

    async def _sync_table(
        self, table_config: TableSyncConfig, *, upper_bound: BackfillCursor | None = None,
        raise_on_error: bool = False,
    ) -> int:
        """同步单张表的一批次数据。

        Returns:
            本批次同步的记录数
        """
        metrics = self._metrics.get_or_create_table(table_config)
        metrics.target_index = table_config.resolve_index_name(self._config.es_index_prefix)
        start_time = time.monotonic()
        pending_count = 0  # 已取出但尚未确认写入 ES 的记录数

        try:
            # 1. 查询增量数据
            rows, cursor = await self._fetch_incremental(table_config, upper_bound=upper_bound)
            if not rows:
                return 0
            pending_count = len(rows)

            # 2. 批量写入 ES（带重试）
            synced = await self._bulk_upsert_with_retry(table_config, rows)

            # 3. 更新水位线
            await self._watermark_store.update(
                syncer_name=self._config.syncer_name,
                source_table=table_config.source_table,
                target_index=table_config.resolve_index_name(self._config.es_index_prefix),
                watermark=cursor.watermark,
                cursor_identity=cursor.identity,
                sync_count=synced,
            )

            # 4. 记录指标
            latency_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_batch(
                table_name=table_config.table_name,
                count=synced,
                latency_ms=latency_ms,
                watermark=cursor.watermark,
            )
            return synced

        except Exception as e:
            latency_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_batch(
                table_name=table_config.table_name,
                count=pending_count,
                latency_ms=latency_ms,
                error=type(e).__name__,
            )
            logger.exception(
                "[ES-Syncer] %s sync failed", table_config.table_name
            )
            if raise_on_error:
                raise
            return 0

    def _cursor(self, table: TableSyncConfig, row: dict[str, Any]) -> BackfillCursor:
        return BackfillCursor(
            row[table.watermark_column], tuple(row[key] for key in ("record_id", "data_source", "data_type")),
        )

    async def _backfill_bound(self, table: TableSyncConfig) -> BackfillCursor | None:
        """Capture a finite upper tuple before reading batches, even during concurrent writes."""
        row = await self._get_pg().fetch_one(
            f"SELECT {table.watermark_column}, record_id, data_source, data_type FROM {table.source_table} "
            f"ORDER BY {table.watermark_column} DESC, record_id DESC, data_source DESC, data_type DESC LIMIT 1",
        )
        return self._cursor(table, row) if row else None

    async def _fetch_incremental(
        self, table_config: TableSyncConfig, *, upper_bound: BackfillCursor | None = None,
    ) -> tuple[list[dict[str, Any]], BackfillCursor | None]:
        """Read stable timestamp/identity pages; legacy checkpoints replay the whole boundary."""
        pg = self._get_pg()
        cursor = await self._watermark_store.get_cursor(
            self._config.syncer_name, table_config.source_table,
            target_index=table_config.resolve_index_name(self._config.es_index_prefix),
        )

        sql = f"""
            SELECT record_id, data_source, data_type, raw_data,
                   kafka_offset, kafka_partition, kafka_topic,
                   created_at, updated_at, {table_config.watermark_column} AS backfill_watermark
            FROM {table_config.source_table}
        """
        params: dict[str, Any] = {"limit": self._config.batch_size}

        conditions = []
        columns = f"({table_config.watermark_column}, record_id, data_source, data_type)"
        if cursor is not None:
            params["watermark"] = cursor.watermark
            if cursor.identity is None:
                conditions.append(f"{table_config.watermark_column} >= :watermark")
            else:
                conditions.append(f"{columns} > (:watermark, :record_id, :data_source, :data_type)")
                params.update(zip(("record_id", "data_source", "data_type"), cursor.identity, strict=True))
        if upper_bound is not None:
            conditions.append(f"{columns} <= (:upper_watermark, :upper_record_id, :upper_data_source, :upper_data_type)")
            params.update(zip(("upper_record_id", "upper_data_source", "upper_data_type"), upper_bound.identity, strict=True))
            params["upper_watermark"] = upper_bound.watermark
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += f" ORDER BY {table_config.watermark_column}, record_id, data_source, data_type LIMIT :limit"

        rows = await pg.fetch_all(sql, params)
        if not rows:
            return [], None

        final = rows[-1]
        cursor = BackfillCursor(final["backfill_watermark"], tuple(final[key] for key in ("record_id", "data_source", "data_type")))
        return rows, cursor

    async def _bulk_upsert_with_retry(
        self,
        table_config: TableSyncConfig,
        rows: list[dict[str, Any]],
    ) -> int:
        """批量 upsert 到 ES，带重试。

        重试策略:
        - ES 写入失败时，等待 backoff ^ attempt 秒后重试
        - 达到 max_retries 后抛出异常（由上层处理，不前进水位线）
        """
        es = self._get_es()
        index = table_config.resolve_index_name(self._config.es_index_prefix)
        if index not in self._ready_indices:
            if not await es.indices.exists(index=index):
                from elasticsearch import BadRequestError

                try:
                    await es.indices.create(index=index)
                except BadRequestError as exc:
                    if exc.body.get("error", {}).get("type") != "resource_already_exists_exception":
                        raise
            self._ready_indices.add(index)

        last_error: Exception | None = None
        for attempt in range(1, self._config.max_retries + 1):
            try:
                operations = []
                for row in rows:
                    doc_id, doc = build_doc_from_row(row, table_config)
                    if not doc_id:
                        raise ValueError("ES row is missing document identity")
                    version = _cdc_version(row)
                    metadata = {"_index": index, "_id": doc_id}
                    if version is None:
                        action = {"create": metadata}
                    else:
                        metadata.update(version=version, version_type="external_gte")
                        action = {"index": metadata}
                    operations.extend([action, doc])
                if not operations:
                    return 0
                response = await es.bulk(operations=operations, refresh=False)
                items = response.get("items", [])
                if len(items) != len(operations) // 2:
                    raise RuntimeError("Elasticsearch bulk acknowledgement was incomplete")
                acknowledgements = [item.get("index", item.get("create", {})) for item in items]
                if any(item.get("status", 500) >= 300 and not (
                    item.get("status") == 409 and
                    item.get("error", {}).get("type") == "version_conflict_engine_exception"
                ) for item in acknowledgements):
                    raise RuntimeError("Elasticsearch bulk contained failed documents")
                flushed = len(operations) // 2
                logger.debug(
                    "[ES-Syncer] %s bulk upsert: %d docs (attempt %d)",
                    table_config.table_name, flushed, attempt,
                )
                return flushed

            except Exception as e:
                last_error = e
                if attempt < self._config.max_retries:
                    wait = self._config.retry_backoff ** attempt
                    logger.warning(
                        "[ES-Syncer] %s bulk failed (attempt %d/%d), retry in %.1fs: %s",
                        table_config.table_name, attempt,
                        self._config.max_retries, wait, e,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "[ES-Syncer] %s bulk failed after %d attempts: %s",
                        table_config.table_name, self._config.max_retries, e,
                    )

        raise last_error  # type: ignore[misc]

    # ── 监控接口 ──────────────────────────────────────────────────────

    def get_metrics(self) -> SyncMetrics:
        """获取同步指标（用于监控/健康检查）。"""
        return self._metrics

    async def get_status(self) -> list[dict[str, Any]]:
        """获取水位线状态（从数据库查询持久化状态）。"""
        return await self._watermark_store.get_status(self._config.syncer_name)
