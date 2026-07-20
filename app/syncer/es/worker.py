"""ES 同步 Worker — 监听 RDS 数据库变更，实时同步到 Elasticsearch。

工作流程（与 SyncWorker 架构一致）
----------------------------------
1. 轮询 RDS 表中 updated_at > last_watermark 的记录
2. 批量推送记录到 Elasticsearch（bulk upsert）
3. 推送成功后更新水位线

设计原则（与 SyncWorker 一致）
------------------------------
- 与 ETL 完全解耦，独立运行
- 幂等性：通过 doc_id (record_id) upsert 保证不重复
- 批量处理：通过 batch_size 控制每次同步数量
- 增量同步：通过 updated_at 水位线跟踪进度
- 失败重试：ES 写入失败时按 max_retries 重试，不前进水位线

数据一致性
----------
- 水位线只前进不回退（GREATEST 语义）
- 批次失败时不更新水位线，下次轮询重试同一批次
- upsert (doc_as_upsert=True) 自动处理冲突
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from app.config.settings import settings
from app.logger import get_logger
from app.storage.elasticsearch_storage import ElasticsearchStorage
from app.storage.postgres_client import PostgresClient, get_pg_client
from app.syncer.es.config import SyncConfig, TableSyncConfig, build_doc_from_row
from app.syncer.es.metrics import SyncMetrics
from app.syncer.es.watermark import WatermarkStore

logger = get_logger(__name__)


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
        es: ElasticsearchStorage | None = None,
    ) -> None:
        self._config = config or SyncConfig.from_settings()
        self._pg = pg
        self._es = es
        self._watermark_store = WatermarkStore(pg=pg)
        self._metrics = SyncMetrics(syncer_name=self._config.syncer_name)
        self._running = False

    # ── 依赖管理（与 SyncWorker 一致的延迟初始化） ────────────────────

    def _get_pg(self) -> PostgresClient:
        if self._pg is None:
            self._pg = get_pg_client()
        return self._pg

    def _get_es(self) -> ElasticsearchStorage:
        if self._es is None:
            self._es = ElasticsearchStorage(
                hosts=self._config.es_url,
                username=self._config.es_username,
                password=self._config.es_password,
                index_prefix=self._config.es_index_prefix,
                batch_size=self._config.batch_size,
            )
        return self._es

    # ── 主循环（与 SyncWorker.run() 结构一致） ────────────────────────

    async def run(self) -> None:
        """启动同步主循环。"""
        self._running = True
        await self._init_dependencies()

        while self._running:
            try:
                total_synced = 0
                for table_config in self._config.tables:
                    if not self._running:
                        break
                    count = await self._sync_table(table_config)
                    total_synced += count

                if total_synced == 0:
                    await asyncio.sleep(self._config.poll_interval)
            except Exception:
                logger.exception("[ES-Syncer] Main loop error")
                await asyncio.sleep(self._config.poll_interval)

    async def run_once(self) -> None:
        """执行一轮同步后退出（用于测试/补数）。"""
        await self._init_dependencies()
        for table_config in self._config.tables:
            await self._sync_table(table_config)

    async def _init_dependencies(self) -> None:
        """初始化依赖（PG/ES/水位线/指标）。"""
        pg = self._get_pg()
        await pg.connect()
        await self._watermark_store.ensure_table()
        es = self._get_es()
        await es.connect()

        # 初始化指标
        for table_config in self._config.tables:
            metrics = self._metrics.get_or_create_table(table_config)
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
                tc.source_table, tc.index_name,
                metrics.last_watermark.isoformat() if metrics.last_watermark else "initial",
            )

    async def stop(self) -> None:
        """停止同步并清理资源。"""
        self._running = False
        if self._es is not None:
            await self._es.close()
        self._metrics.log_summary()
        logger.info("[ES-Syncer] Stopped")

    # ── 单表同步 ──────────────────────────────────────────────────────

    async def _sync_table(self, table_config: TableSyncConfig) -> int:
        """同步单张表的一批次数据。

        Returns:
            本批次同步的记录数
        """
        metrics = self._metrics.get_or_create_table(table_config)
        start_time = time.monotonic()
        pending_count = 0  # 已取出但尚未确认写入 ES 的记录数

        try:
            # 1. 查询增量数据
            rows, batch_watermark = await self._fetch_incremental(table_config)
            if not rows:
                return 0
            pending_count = len(rows)

            # 2. 批量写入 ES（带重试）
            synced = await self._bulk_upsert_with_retry(table_config, rows)

            # 3. 更新水位线
            await self._watermark_store.update(
                syncer_name=self._config.syncer_name,
                source_table=table_config.source_table,
                target_index=table_config.index_name,
                watermark=batch_watermark,
                sync_count=synced,
            )

            # 4. 记录指标
            latency_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_batch(
                table_name=table_config.table_name,
                count=synced,
                latency_ms=latency_ms,
                watermark=batch_watermark,
            )
            return synced

        except Exception as e:
            latency_ms = (time.monotonic() - start_time) * 1000
            self._metrics.record_batch(
                table_name=table_config.table_name,
                count=pending_count,
                latency_ms=latency_ms,
                error=str(e),
            )
            logger.exception(
                "[ES-Syncer] %s sync failed", table_config.table_name
            )
            return 0

    async def _fetch_incremental(
        self, table_config: TableSyncConfig
    ) -> tuple[list[dict[str, Any]], datetime | None]:
        """从 RDS 查询增量数据。

        查询 updated_at > last_watermark 的记录，按 updated_at 升序，限制 batch_size。

        Returns:
            (rows, batch_watermark) — rows 为数据行列表，batch_watermark 为本批次最大 updated_at
        """
        pg = self._get_pg()
        last_watermark = await self._watermark_store.get(
            self._config.syncer_name, table_config.source_table
        )

        sql = f"""
            SELECT record_id, data_source, data_type, raw_data,
                   kafka_offset, kafka_partition, kafka_topic,
                   created_at, updated_at
            FROM {table_config.source_table}
        """
        params: dict[str, Any] = {"limit": self._config.batch_size}

        if last_watermark is not None:
            sql += f" WHERE {table_config.watermark_column} > :watermark"
            params["watermark"] = last_watermark

        sql += f" ORDER BY {table_config.watermark_column} ASC LIMIT :limit"

        rows = await pg.fetch_all(sql, params)
        if not rows:
            return [], None

        # 本批次的水位线 = 最大 updated_at
        batch_watermark = max(
            row[table_config.watermark_column] for row in rows
            if row.get(table_config.watermark_column) is not None
        )
        return rows, batch_watermark

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
        index = table_config.index_name
        await es.ensure_index(table_config.table_name)

        last_error: Exception | None = None
        for attempt in range(1, self._config.max_retries + 1):
            try:
                # 构建文档并加入 bulk 队列
                for row in rows:
                    doc_id, doc = build_doc_from_row(row, table_config)
                    if not doc_id:
                        logger.warning(
                            "[ES-Syncer] %s skip row without doc_id: %s",
                            table_config.table_name, row.get("record_id"),
                        )
                        continue
                    await es.add_to_bulk(index, doc_id, doc)

                flushed = await es.flush_bulk()
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
