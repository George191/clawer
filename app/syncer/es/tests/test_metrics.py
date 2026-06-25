"""SyncMetrics — 单元测试。

测试指标记录、健康状态判断、汇总输出。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.syncer.es.config import TableSyncConfig
from app.syncer.es.metrics import SyncMetrics, TableMetrics


class TestTableMetrics:
    """测试 TableMetrics。"""

    def test_default_healthy(self) -> None:
        m = TableMetrics(table_name="patent", source_table="ts_rds.rds_patent", target_index="spider_patent")
        assert m.is_healthy is True
        assert m.success_rate == 1.0

    def test_success_rate(self) -> None:
        m = TableMetrics(table_name="patent", source_table="ts_rds.rds_patent", target_index="spider_patent")
        m.records_synced = 80
        m.records_failed = 20
        assert m.success_rate == 0.8

    def test_unhealthy_after_5_errors(self) -> None:
        m = TableMetrics(table_name="patent", source_table="ts_rds.rds_patent", target_index="spider_patent")
        m.consecutive_errors = 5
        assert m.is_healthy is False

    def test_to_dict(self) -> None:
        m = TableMetrics(table_name="patent", source_table="ts_rds.rds_patent", target_index="spider_patent")
        m.records_synced = 100
        d = m.to_dict()
        assert d["table_name"] == "patent"
        assert d["records_synced"] == 100
        assert d["is_healthy"] is True
        assert "started_at" in d


class TestSyncMetrics:
    """测试 SyncMetrics。"""

    def test_get_or_create_table(self) -> None:
        metrics = SyncMetrics(syncer_name="es_syncer")
        cfg = TableSyncConfig(table_name="patent")

        m1 = metrics.get_or_create_table(cfg)
        m2 = metrics.get_or_create_table(cfg)
        assert m1 is m2
        assert m1.table_name == "patent"

    def test_record_batch_success(self) -> None:
        metrics = SyncMetrics(syncer_name="es_syncer")
        cfg = TableSyncConfig(table_name="patent")
        metrics.get_or_create_table(cfg)

        watermark = datetime(2026, 1, 1, tzinfo=timezone.utc)
        metrics.record_batch(
            table_name="patent",
            count=100,
            latency_ms=500.0,
            watermark=watermark,
        )

        m = metrics.tables["patent"]
        assert m.records_synced == 100
        assert m.records_failed == 0
        assert m.consecutive_errors == 0
        assert m.last_watermark == watermark
        assert m.last_batch_count == 100
        assert metrics.total_records_synced == 100

    def test_record_batch_error(self) -> None:
        metrics = SyncMetrics(syncer_name="es_syncer")
        cfg = TableSyncConfig(table_name="patent")
        metrics.get_or_create_table(cfg)

        metrics.record_batch(
            table_name="patent",
            count=50,
            latency_ms=1000.0,
            error="ES timeout",
        )

        m = metrics.tables["patent"]
        assert m.records_synced == 0
        assert m.records_failed == 50
        assert m.consecutive_errors == 1
        assert m.last_error == "ES timeout"
        assert m.is_healthy is True  # 1 < 5

    def test_consecutive_errors_makes_unhealthy(self) -> None:
        metrics = SyncMetrics(syncer_name="es_syncer")
        cfg = TableSyncConfig(table_name="patent")
        metrics.get_or_create_table(cfg)

        for _ in range(5):
            metrics.record_batch(
                table_name="patent", count=10, latency_ms=100, error="fail"
            )

        m = metrics.tables["patent"]
        assert m.consecutive_errors == 5
        assert m.is_healthy is False

    def test_success_resets_consecutive_errors(self) -> None:
        metrics = SyncMetrics(syncer_name="es_syncer")
        cfg = TableSyncConfig(table_name="patent")
        metrics.get_or_create_table(cfg)

        # 先产生 3 次错误
        for _ in range(3):
            metrics.record_batch("patent", 10, 100, error="fail")

        # 一次成功
        metrics.record_batch("patent", 10, 100)

        m = metrics.tables["patent"]
        assert m.consecutive_errors == 0
        assert m.is_healthy is True

    def test_to_dict(self) -> None:
        metrics = SyncMetrics(syncer_name="es_syncer")
        cfg = TableSyncConfig(table_name="patent")
        metrics.get_or_create_table(cfg)
        metrics.record_batch("patent", 100, 500)

        d = metrics.to_dict()
        assert d["syncer_name"] == "es_syncer"
        assert d["total_records_synced"] == 100
        assert "patent" in d["tables"]
        assert d["tables"]["patent"]["records_synced"] == 100
