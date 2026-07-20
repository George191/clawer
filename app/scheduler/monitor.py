"""调度监控与告警 — 任务健康度检查。

功能:
    - 检查任务是否按时执行
    - 检查任务失败率
    - 输出监控摘要（可对接告警系统）

任务名来源
----------
通过 BeatScheduleRegistry 动态获取，
与 beat_schedule / task_store 中的 task_name 保持一致。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.logger import get_logger, setup_service_logging
from app.scheduler.beat_schedule import BeatScheduleRegistry
from app.scheduler.task_store import TaskStore, get_task_store

logger = get_logger(__name__)


@dataclass
class AlertThresholds:
    """告警阈值配置。"""

    failure_rate: float = 0.5          # 失败率 > 50% 告警
    no_run_hours: float = 26           # 超过 26 小时未执行告警
    stuck_hours: float = 6             # 任务卡在 started 状态超过 6 小时告警


@dataclass
class HealthReport:
    """健康检查报告。"""

    overall_healthy: bool
    checked_at: str
    tasks: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)


class SchedulerMonitor:
    """调度任务健康监控器。

    检查任务执行状态、失败率、超时等指标，生成健康报告。

    用法:
        monitor = SchedulerMonitor()
        report = await monitor.check_all()
        await monitor.print_report(report)
    """

    def __init__(
        self,
        task_store: TaskStore | None = None,
        registry: BeatScheduleRegistry | None = None,
        thresholds: AlertThresholds | None = None,
    ) -> None:
        self._task_store = task_store
        self._registry = registry or BeatScheduleRegistry.instance()
        self._thresholds = thresholds or AlertThresholds()

    # ── 依赖管理 ──────────────────────────────────────────────────────

    def _get_store(self) -> TaskStore:
        if self._task_store is None:
            self._task_store = get_task_store()
        return self._task_store

    # ── 健康检查 ──────────────────────────────────────────────────────

    async def check_task(self, task_name: str) -> dict[str, Any]:
        """检查单个任务的健康状态。

        Args:
            task_name: 任务名称（与 beat_schedule key 对齐）

        Returns:
            健康检查结果
        """
        store = self._get_store()
        now = datetime.now(timezone.utc)

        # 获取最近 10 次执行记录
        recent = await store.get_history(task_name=task_name, limit=10)

        if not recent:
            return {
                "task_name": task_name,
                "healthy": False,
                "alert": "NEVER_RUN",
                "message": f"任务 {task_name} 从未执行过",
            }

        latest = recent[0]
        latest_time = latest.get("created_at") or latest.get("started_at")

        # 检查是否超时未执行
        if latest_time:
            hours_since = (now - latest_time).total_seconds() / 3600
            if hours_since > self._thresholds.no_run_hours:
                return {
                    "task_name": task_name,
                    "healthy": False,
                    "alert": "OVERDUE",
                    "message": f"任务 {task_name} 已 {hours_since:.1f} 小时未执行",
                    "last_run": latest_time.isoformat(),
                }

        # 检查是否卡在 started 状态
        if latest.get("status") == "started":
            started_at = latest.get("started_at")
            if started_at:
                stuck_hours = (now - started_at).total_seconds() / 3600
                if stuck_hours > self._thresholds.stuck_hours:
                    return {
                        "task_name": task_name,
                        "healthy": False,
                        "alert": "STUCK",
                        "message": f"任务 {task_name} 卡在 started 状态 {stuck_hours:.1f} 小时",
                        "task_id": latest.get("_id"),
                    }

        # 检查失败率
        total = len(recent)
        failed = sum(1 for r in recent if r.get("status") in ("failed", "retry"))
        failure_rate = failed / total if total > 0 else 0

        if failure_rate > self._thresholds.failure_rate:
            return {
                "task_name": task_name,
                "healthy": False,
                "alert": "HIGH_FAILURE_RATE",
                "message": f"任务 {task_name} 失败率 {failure_rate:.0%} ({failed}/{total})",
                "failure_rate": failure_rate,
            }

        return {
            "task_name": task_name,
            "healthy": True,
            "alert": None,
            "message": "正常",
            "last_run": latest_time.isoformat() if latest_time else None,
            "last_status": latest.get("status"),
            "recent_total": total,
            "recent_failed": failed,
        }

    async def check_all(self) -> HealthReport:
        """全量健康检查。"""
        monitored_tasks = self._registry.get_task_names()

        checks = await asyncio.gather(
            *[self.check_task(name) for name in monitored_tasks],
            return_exceptions=True,
        )

        task_results = []
        alerts = []

        for check in checks:
            if isinstance(check, Exception):
                task_results.append({
                    "healthy": False,
                    "alert": "CHECK_ERROR",
                    "message": str(check),
                })
                alerts.append(str(check))
                continue

            task_results.append(check)
            if not check["healthy"]:
                alerts.append(check["message"])

        return HealthReport(
            overall_healthy=len(alerts) == 0,
            checked_at=datetime.now(timezone.utc).isoformat(),
            tasks=task_results,
            alerts=alerts,
        )

    # ── 报告输出 ──────────────────────────────────────────────────────

    async def print_report(self, report: HealthReport | None = None) -> None:
        """打印健康报告到日志。"""
        if report is None:
            report = await self.check_all()

        logger.info("=" * 60)
        logger.info("调度任务健康报告")
        logger.info("=" * 60)

        status = "健康" if report.overall_healthy else "有告警"
        logger.info("整体状态: %s", status)

        for task in report.tasks:
            icon = "[OK]" if task["healthy"] else "[ALERT]"
            name = task.get("task_name", "unknown")
            msg = task.get("message", "")
            logger.info("  %s %s: %s", icon, name, msg)

        if report.alerts:
            logger.warning("告警列表:")
            for alert in report.alerts:
                logger.warning("  - %s", alert)

        logger.info("=" * 60)


# ══════════════════════════════════════════════════════════════════════
#  兼容函数（保持向后兼容）
# ══════════════════════════════════════════════════════════════════════

async def check_task_health(task_name: str) -> dict[str, Any]:
    """检查单个任务健康状态（兼容旧接口）。"""
    return await SchedulerMonitor().check_task(task_name)


async def health_check() -> dict[str, Any]:
    """全量健康检查（兼容旧接口）。"""
    monitor = SchedulerMonitor()
    report = await monitor.check_all()
    return {
        "overall_healthy": report.overall_healthy,
        "checked_at": report.checked_at,
        "tasks": report.tasks,
        "alerts": report.alerts,
    }


async def print_health_report() -> None:
    """打印健康报告（兼容旧接口）。"""
    await SchedulerMonitor().print_report()


if __name__ == "__main__":
    setup_service_logging("monitor", "INFO")
    asyncio.run(print_health_report())
