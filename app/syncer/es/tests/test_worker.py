"""EsSyncWorker — 单元测试。

测试同步主流程：增量查询、批量写入、水位线更新、重试机制。
使用 mock 依赖（PostgresClient、ElasticsearchStorage、WatermarkStore）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.syncer.es.config import SyncConfig, TableSyncConfig
from app.syncer.es.worker import EsSyncWorker


@pytest.fixture
def mock_pg() -> MagicMock:
    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.fetch_all = AsyncMock(return_value=[])
    pg.fetch_one = AsyncMock(return_value=None)
    pg.execute = AsyncMock()
    return pg


@pytest.fixture
def mock_es() -> MagicMock:
    es = MagicMock()
    es.connect = AsyncMock()
    es.close = AsyncMock()
    es.ensure_index = AsyncMock()
    es.add_to_bulk = AsyncMock()
    es.flush_bulk = AsyncMock(return_value=0)
    return es


@pytest.fixture
def mock_watermark_store() -> MagicMock:
    store = MagicMock()
    store.ensure_table = AsyncMock()
    store.get = AsyncMock(return_value=None)
    store.update = AsyncMock()
    store.get_status = AsyncMock(return_value=[])
    return store


@pytest.fixture
def config() -> SyncConfig:
    return SyncConfig(
        syncer_name="test_syncer",
        poll_interval=0,  # 测试中立即返回
        batch_size=10,
        max_retries=2,
        retry_backoff=0.01,  # 测试中快速重试
        tables=[TableSyncConfig(table_name="patent")],
    )


@pytest.fixture
def worker(
    config: SyncConfig,
    mock_pg: MagicMock,
    mock_es: MagicMock,
    mock_watermark_store: MagicMock,
) -> EsSyncWorker:
    w = EsSyncWorker(config=config, pg=mock_pg, es=mock_es)
    w._watermark_store = mock_watermark_store
    return w


class TestEsSyncWorkerInit:
    """测试初始化。"""

    def test_default_config(self) -> None:
        w = EsSyncWorker()
        assert w._config is not None
        assert w._metrics.syncer_name == "es_syncer"

    def test_custom_config(self, config: SyncConfig) -> None:
        w = EsSyncWorker(config=config)
        assert w._config.syncer_name == "test_syncer"


class TestRunOnce:
    """测试单次同步模式。"""

    @pytest.mark.asyncio
    async def test_run_once_no_data(
        self,
        worker: EsSyncWorker,
        mock_pg: MagicMock,
        mock_es: MagicMock,
    ) -> None:
        """无增量数据时应正常完成。"""
        mock_pg.fetch_all.return_value = []

        await worker.run_once()

        mock_pg.connect.assert_called_once()
        mock_es.connect.assert_called_once()
        # 无数据，不应调用 ES 写入
        mock_es.add_to_bulk.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_once_with_data(
        self,
        worker: EsSyncWorker,
        mock_pg: MagicMock,
        mock_es: MagicMock,
        mock_watermark_store: MagicMock,
    ) -> None:
        """有增量数据时应同步到 ES 并更新水位线。"""
        now = datetime.now(timezone.utc)
        rows = [
            {
                "record_id": "rec-001",
                "data_source": "google",
                "data_type": "patent",
                "raw_data": json.dumps({"title": "Patent 1"}),
                "updated_at": now,
            },
            {
                "record_id": "rec-002",
                "data_source": "google",
                "data_type": "patent",
                "raw_data": json.dumps({"title": "Patent 2"}),
                "updated_at": now,
            },
        ]
        mock_pg.fetch_all.return_value = rows
        mock_es.flush_bulk.return_value = 2

        await worker.run_once()

        # 应调用 ES 写入
        assert mock_es.add_to_bulk.call_count == 2
        mock_es.flush_bulk.assert_called_once()

        # 应更新水位线
        mock_watermark_store.update.assert_called_once()
        update_kwargs = mock_watermark_store.update.call_args.kwargs
        assert update_kwargs["syncer_name"] == "test_syncer"
        assert update_kwargs["source_table"] == "ts_rds.rds_patent"
        assert update_kwargs["watermark"] == now
        assert update_kwargs["sync_count"] == 2

    @pytest.mark.asyncio
    async def test_run_once_es_failure_no_watermark_update(
        self,
        worker: EsSyncWorker,
        mock_pg: MagicMock,
        mock_es: MagicMock,
        mock_watermark_store: MagicMock,
    ) -> None:
        """ES 写入失败时不应更新水位线。"""
        now = datetime.now(timezone.utc)
        mock_pg.fetch_all.return_value = [
            {
                "record_id": "rec-001",
                "data_source": "google",
                "data_type": "patent",
                "raw_data": "{}",
                "updated_at": now,
            }
        ]
        mock_es.flush_bulk.side_effect = Exception("ES connection refused")

        # 不应抛出异常（由 _sync_table 捕获）
        await worker.run_once()

        # 应重试 max_retries 次后失败
        assert mock_es.flush_bulk.call_count == 2  # max_retries=2
        # 不应更新水位线
        mock_watermark_store.update.assert_not_called()

        # 指标应记录失败
        metrics = worker.get_metrics()
        assert metrics.tables["patent"].records_failed == 1
        assert metrics.tables["patent"].consecutive_errors == 1

    @pytest.mark.asyncio
    async def test_run_once_retry_succeeds(
        self,
        worker: EsSyncWorker,
        mock_pg: MagicMock,
        mock_es: MagicMock,
        mock_watermark_store: MagicMock,
    ) -> None:
        """第一次失败、第二次成功时应更新水位线。"""
        now = datetime.now(timezone.utc)
        mock_pg.fetch_all.return_value = [
            {
                "record_id": "rec-001",
                "data_source": "google",
                "data_type": "patent",
                "raw_data": "{}",
                "updated_at": now,
            }
        ]
        # 第一次失败，第二次成功
        mock_es.flush_bulk.side_effect = [Exception("temp"), 1]

        await worker.run_once()

        assert mock_es.flush_bulk.call_count == 2
        mock_watermark_store.update.assert_called_once()


class TestIncrementalFetch:
    """测试增量查询逻辑。"""

    @pytest.mark.asyncio
    async def test_first_sync_no_watermark(
        self,
        worker: EsSyncWorker,
        mock_pg: MagicMock,
        mock_watermark_store: MagicMock,
    ) -> None:
        """首次同步（无水位线）应查询全部数据。"""
        mock_watermark_store.get.return_value = None
        mock_pg.fetch_all.return_value = []

        cfg = worker._config.tables[0]
        await worker._fetch_incremental(cfg)

        # SQL 不应包含 WHERE 条件
        sql_call = mock_pg.fetch_all.call_args
        sql = sql_call[0][0]
        assert "WHERE" not in sql
        assert "LIMIT" in sql

    @pytest.mark.asyncio
    async def test_incremental_with_watermark(
        self,
        worker: EsSyncWorker,
        mock_pg: MagicMock,
        mock_watermark_store: MagicMock,
    ) -> None:
        """有水位线时应查询 watermark > last 的数据。"""
        last = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mock_watermark_store.get.return_value = last
        mock_pg.fetch_all.return_value = []

        cfg = worker._config.tables[0]
        await worker._fetch_incremental(cfg)

        sql_call = mock_pg.fetch_all.call_args
        sql = sql_call[0][0]
        params = sql_call[0][1]
        assert "WHERE" in sql
        assert "updated_at > :watermark" in sql
        assert params["watermark"] == last

    @pytest.mark.asyncio
    async def test_batch_watermark_is_max(
        self,
        worker: EsSyncWorker,
        mock_pg: MagicMock,
    ) -> None:
        """batch_watermark 应为批次中最大的 updated_at。"""
        t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
        t3 = datetime(2026, 1, 3, tzinfo=timezone.utc)
        mock_pg.fetch_all.return_value = [
            {"record_id": "r1", "raw_data": "{}", "updated_at": t1},
            {"record_id": "r2", "raw_data": "{}", "updated_at": t3},
            {"record_id": "r3", "raw_data": "{}", "updated_at": t2},
        ]

        cfg = worker._config.tables[0]
        rows, batch_watermark = await worker._fetch_incremental(cfg)

        assert len(rows) == 3
        assert batch_watermark == t3


class TestStop:
    """测试停止逻辑。"""

    @pytest.mark.asyncio
    async def test_stop_closes_es(
        self,
        worker: EsSyncWorker,
        mock_es: MagicMock,
    ) -> None:
        await worker.stop()
        mock_es.close.assert_called_once()
        assert worker._running is False
