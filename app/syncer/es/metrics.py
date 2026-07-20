"""ES 同步器监控指标 — 同步状态、数据量统计、异常报警。

与 SyncWorker 的日志风格一致，提供结构化指标输出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TableMetrics:
    """单表同步指标。"""

    table_name: str
    source_table: str
    target_index: str

    # 本次运行统计
    batches_processed: int = 0
    records_synced: int = 0
    records_failed: int = 0
    last_batch_count: int = 0
    last_batch_latency_ms: float = 0.0

    # 水位线
    last_watermark: datetime | None = None
    initial_watermark: datetime | None = None

    # 错误
    last_error: str | None = None
    consecutive_errors: int = 0

    # 时间
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_sync_at: datetime | None = None

    @property
    def success_rate(self) -> float:
        """成功率。"""
        total = self.records_synced + self.records_failed
        if total == 0:
            return 1.0
        return self.records_synced / total

    @property
    def is_healthy(self) -> bool:
        """是否健康（连续错误 < 5）。"""
        return self.consecutive_errors < 5

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（用于日志/API）。"""
        return {
            "table_name": self.table_name,
            "source_table": self.source_table,
            "target_index": self.target_index,
            "batches_processed": self.batches_processed,
            "records_synced": self.records_synced,
            "records_failed": self.records_failed,
            "last_batch_count": self.last_batch_count,
            "last_batch_latency_ms": round(self.last_batch_latency_ms, 2),
            "last_watermark": self.last_watermark.isoformat() if self.last_watermark else None,
            "initial_watermark": self.initial_watermark.isoformat() if self.initial_watermark else None,
            "last_error": self.last_error,
            "consecutive_errors": self.consecutive_errors,
            "is_healthy": self.is_healthy,
            "success_rate": round(self.success_rate, 4),
            "started_at": self.started_at.isoformat(),
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
        }


@dataclass
class SyncMetrics:
    """全局同步指标。"""

    syncer_name: str
    tables: dict[str, TableMetrics] = field(default_factory=dict)

    # 全局统计
    total_batches: int = 0
    total_records_synced: int = 0
    total_records_failed: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def get_or_create_table(self, table_config: Any) -> TableMetrics:
        """获取或创建表级指标。"""
        key = table_config.table_name
        if key not in self.tables:
            self.tables[key] = TableMetrics(
                table_name=table_config.table_name,
                source_table=table_config.source_table,
                target_index=table_config.index_name,
            )
        return self.tables[key]

    def record_batch(
        self,
        table_name: str,
        count: int,
        latency_ms: float,
        watermark: datetime | None = None,
        error: str | None = None,
    ) -> None:
        """记录一次批次同步结果。"""
        metrics = self.tables.get(table_name)
        if metrics is None:
            return

        metrics.batches_processed += 1
        metrics.last_batch_count = count
        metrics.last_batch_latency_ms = latency_ms
        metrics.last_sync_at = datetime.now(timezone.utc)

        self.total_batches += 1

        if error:
            metrics.records_failed += count
            metrics.last_error = error
            metrics.consecutive_errors += 1
            self.total_records_failed += count
            logger.error(
                "[ES-Syncer] %s batch failed: %d records, error=%s, latency=%.0fms",
                table_name, count, error, latency_ms,
            )
        else:
            metrics.records_synced += count
            metrics.consecutive_errors = 0
            metrics.last_error = None
            self.total_records_synced += count
            if watermark:
                metrics.last_watermark = watermark
            logger.info(
                "[ES-Syncer] %s batch synced: %d records, watermark=%s, latency=%.0fms",
                table_name, count,
                watermark.isoformat() if watermark else "N/A",
                latency_ms,
            )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "syncer_name": self.syncer_name,
            "total_batches": self.total_batches,
            "total_records_synced": self.total_records_synced,
            "total_records_failed": self.total_records_failed,
            "started_at": self.started_at.isoformat(),
            "tables": {k: v.to_dict() for k, v in self.tables.items()},
        }

    def log_summary(self) -> None:
        """输出汇总日志。"""
        uptime = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        logger.info(
            "[ES-Syncer] === Summary === "
            "uptime=%.0fs, batches=%d, synced=%d, failed=%d, tables=%d",
            uptime, self.total_batches,
            self.total_records_synced, self.total_records_failed,
            len(self.tables),
        )
        for name, m in self.tables.items():
            status = "HEALTHY" if m.is_healthy else "UNHEALTHY"
            logger.info(
                "[ES-Syncer] %s [%s]: batches=%d, synced=%d, failed=%d, "
                "success_rate=%.2f%%, last_watermark=%s",
                name, status, m.batches_processed,
                m.records_synced, m.records_failed,
                m.success_rate * 100,
                m.last_watermark.isoformat() if m.last_watermark else "N/A",
            )
