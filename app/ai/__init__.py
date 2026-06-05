"""AI 智能层 — SQL 提示 / 仪表盘指标 / 智能分析。

此包提供：
- ``SQLHintEngine``           ETL 数据浏览器 SQL 智能补全与安全校验
- ``DashboardMetricsEngine``  Dashboard v2 实时指标推送
"""

from app.ai.dashboard_metrics import (
    CrawlStats,
    DashboardMetricsEngine,
    LayerMetric,
    PipelineMetrics,
)
from app.ai.sql_hints import (
    Completion,
    SQLHintEngine,
    ValidationResult,
)

__all__ = [
    "SQLHintEngine",
    "Completion",
    "ValidationResult",
    "DashboardMetricsEngine",
    "PipelineMetrics",
    "LayerMetric",
    "CrawlStats",
]