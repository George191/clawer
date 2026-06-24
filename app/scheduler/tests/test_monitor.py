"""调度监控 — 单元测试。

测试覆盖:
    - SchedulerMonitor 类的健康检查逻辑
    - AlertThresholds 配置
    - HealthReport 数据结构
    - 兼容函数
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scheduler.monitor import (
    AlertThresholds,
    HealthReport,
    SchedulerMonitor,
    check_task_health,
    health_check,
)


def _make_record(
    status: str = "success",
    hours_ago: float = 1.0,
    stuck: bool = False,
) -> dict:
    """构造测试用任务记录。"""
    now = datetime.now(timezone.utc)
    created_at = now - timedelta(hours=hours_ago)
    started_at = created_at

    if stuck:
        # 卡在 started 状态：started_at 很久以前，无 finished_at
        started_at = now - timedelta(hours=10)

    return {
        "_id": "task-1",
        "task_name": "test_task",
        "status": status,
        "created_at": created_at,
        "started_at": started_at,
        "finished_at": None if stuck else created_at + timedelta(minutes=5),
    }


# ══════════════════════════════════════════════════════════════════════
#  数据类测试
# ══════════════════════════════════════════════════════════════════════

class TestAlertThresholds:
    """测试 AlertThresholds 配置。"""

    def test_defaults(self) -> None:
        t = AlertThresholds()
        assert t.failure_rate == 0.5
        assert t.no_run_hours == 26
        assert t.stuck_hours == 6

    def test_custom_values(self) -> None:
        t = AlertThresholds(failure_rate=0.3, no_run_hours=12, stuck_hours=2)
        assert t.failure_rate == 0.3
        assert t.no_run_hours == 12
        assert t.stuck_hours == 2


class TestHealthReport:
    """测试 HealthReport 数据结构。"""

    def test_default_lists_empty(self) -> None:
        report = HealthReport(overall_healthy=True, checked_at="now")
        assert report.tasks == []
        assert report.alerts == []


# ══════════════════════════════════════════════════════════════════════
#  SchedulerMonitor 类测试
# ══════════════════════════════════════════════════════════════════════

class TestSchedulerMonitor:
    """测试 SchedulerMonitor 类。"""

    def test_init_with_defaults(self) -> None:
        """默认构造应使用全局单例。"""
        monitor = SchedulerMonitor()
        assert monitor._task_store is None
        assert monitor._thresholds is not None

    def test_init_with_injected_deps(self) -> None:
        """应支持依赖注入。"""
        mock_store = MagicMock()
        mock_registry = MagicMock()
        thresholds = AlertThresholds(failure_rate=0.3)
        monitor = SchedulerMonitor(
            task_store=mock_store,
            registry=mock_registry,
            thresholds=thresholds,
        )
        assert monitor._task_store is mock_store
        assert monitor._registry is mock_registry
        assert monitor._thresholds is thresholds

    def test_get_store_uses_global_singleton(self) -> None:
        """未注入 task_store 时应使用全局单例。"""
        monitor = SchedulerMonitor()
        with patch("app.scheduler.monitor.get_task_store") as mock_get:
            mock_store = MagicMock()
            mock_get.return_value = mock_store
            store = monitor._get_store()
            assert store is mock_store
            monitor._get_store()
            mock_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_task_never_run(self) -> None:
        """任务从未执行过 → NEVER_RUN。"""
        mock_store = MagicMock()
        mock_store.get_history = AsyncMock(return_value=[])
        monitor = SchedulerMonitor(task_store=mock_store)

        result = await monitor.check_task("test_task")

        assert result["healthy"] is False
        assert result["alert"] == "NEVER_RUN"

    @pytest.mark.asyncio
    async def test_check_task_healthy(self) -> None:
        """最近执行成功 → 健康。"""
        mock_store = MagicMock()
        mock_store.get_history = AsyncMock(return_value=[_make_record("success", 1.0)])
        monitor = SchedulerMonitor(task_store=mock_store)

        result = await monitor.check_task("test_task")

        assert result["healthy"] is True
        assert result["alert"] is None

    @pytest.mark.asyncio
    async def test_check_task_overdue(self) -> None:
        """超过阈值未执行 → OVERDUE。"""
        mock_store = MagicMock()
        mock_store.get_history = AsyncMock(return_value=[_make_record("success", 30.0)])
        monitor = SchedulerMonitor(task_store=mock_store)

        result = await monitor.check_task("test_task")

        assert result["healthy"] is False
        assert result["alert"] == "OVERDUE"

    @pytest.mark.asyncio
    async def test_check_task_stuck(self) -> None:
        """卡在 started 状态超过阈值 → STUCK。"""
        mock_store = MagicMock()
        mock_store.get_history = AsyncMock(
            return_value=[_make_record("started", 10.0, stuck=True)]
        )
        monitor = SchedulerMonitor(task_store=mock_store)

        result = await monitor.check_task("test_task")

        assert result["healthy"] is False
        assert result["alert"] == "STUCK"

    @pytest.mark.asyncio
    async def test_check_task_high_failure_rate(self) -> None:
        """失败率超过阈值 → HIGH_FAILURE_RATE。"""
        mock_store = MagicMock()
        records = [_make_record("failed", float(i + 1)) for i in range(6)]
        records += [_make_record("success", 7.0), _make_record("success", 8.0)]
        mock_store.get_history = AsyncMock(return_value=records)
        monitor = SchedulerMonitor(task_store=mock_store)

        result = await monitor.check_task("test_task")

        assert result["healthy"] is False
        assert result["alert"] == "HIGH_FAILURE_RATE"

    @pytest.mark.asyncio
    async def test_custom_thresholds_override(self) -> None:
        """自定义阈值应生效。"""
        mock_store = MagicMock()
        # 30% 失败率（1/3），默认阈值 50% 不告警，自定义 20% 应告警
        records = [
            _make_record("failed", 1.0),
            _make_record("success", 2.0),
            _make_record("success", 3.0),
        ]
        mock_store.get_history = AsyncMock(return_value=records)
        thresholds = AlertThresholds(failure_rate=0.2)
        monitor = SchedulerMonitor(task_store=mock_store, thresholds=thresholds)

        result = await monitor.check_task("test_task")

        assert result["healthy"] is False
        assert result["alert"] == "HIGH_FAILURE_RATE"

    @pytest.mark.asyncio
    async def test_check_all_returns_health_report(self) -> None:
        """check_all 应返回 HealthReport。"""
        mock_store = MagicMock()
        mock_store.get_history = AsyncMock(return_value=[_make_record("success", 1.0)])
        monitor = SchedulerMonitor(task_store=mock_store)

        report = await monitor.check_all()

        assert isinstance(report, HealthReport)
        assert report.overall_healthy is True
        assert len(report.tasks) >= 2  # beat_schedule 中至少 2 个任务


# ══════════════════════════════════════════════════════════════════════
#  兼容函数测试
# ══════════════════════════════════════════════════════════════════════

class TestCompatFunctions:
    """测试兼容函数。"""

    @pytest.mark.asyncio
    async def test_check_task_health_compat(self) -> None:
        """兼容函数 check_task_health 应正常工作。"""
        mock_store = MagicMock()
        mock_store.get_history = AsyncMock(return_value=[])
        with patch("app.scheduler.monitor.get_task_store", return_value=mock_store):
            result = await check_task_health("test_task")
        assert result["alert"] == "NEVER_RUN"

    @pytest.mark.asyncio
    async def test_health_check_compat(self) -> None:
        """兼容函数 health_check 应返回字典格式。"""
        mock_store = MagicMock()
        mock_store.get_history = AsyncMock(return_value=[_make_record("success", 1.0)])
        with patch("app.scheduler.monitor.get_task_store", return_value=mock_store):
            result = await health_check()
        assert "overall_healthy" in result
        assert "tasks" in result
        assert "alerts" in result
