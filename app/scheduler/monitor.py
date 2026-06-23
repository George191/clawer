"""调度监控与告警 — 任务健康度检查。

功能:
    - 检查任务是否按时执行
    - 检查任务失败率
    - 输出监控摘要（可对接告警系统）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.scheduler.task_store import get_task_store

logger = logging.getLogger(__name__)

# 告警阈值
ALERT_FAILURE_RATE = 0.5          # 失败率 > 50% 告警
ALERT_NO_RUN_HOURS = 26           # 超过 26 小时未执行告警
ALERT_STUCK_HOURS = 6             # 任务卡在 started 状态超过 6 小时告警


async def check_task_health(task_name: str) -> dict[str, Any]:
    """检查单个任务的健康状态。

    Args:
        task_name: 任务名称

    Returns:
        健康检查结果
    """
    store = get_task_store()
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
        if hours_since > ALERT_NO_RUN_HOURS:
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
            if stuck_hours > ALERT_STUCK_HOURS:
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

    if failure_rate > ALERT_FAILURE_RATE:
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


async def health_check() -> dict[str, Any]:
    """全量健康检查。

    Returns:
        {
            "overall_healthy": bool,
            "tasks": [check_result, ...],
            "alerts": [alert_message, ...],
        }
    """
    # 监控所有已注册的定时任务
    monitored_tasks = [
        "google_patent_daily",
        "google_patent_range",
    ]

    checks = await asyncio.gather(
        *[check_task_health(name) for name in monitored_tasks],
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

    return {
        "overall_healthy": len(alerts) == 0,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "tasks": task_results,
        "alerts": alerts,
    }


async def print_health_report() -> None:
    """打印健康报告到日志。"""
    report = await health_check()

    logger.info("=" * 60)
    logger.info("调度任务健康报告")
    logger.info("=" * 60)

    status = "健康" if report["overall_healthy"] else "有告警"
    logger.info("整体状态: %s", status)

    for task in report["tasks"]:
        icon = "[OK]" if task["healthy"] else "[ALERT]"
        name = task.get("task_name", "unknown")
        msg = task.get("message", "")
        logger.info("  %s %s: %s", icon, name, msg)

    if report["alerts"]:
        logger.warning("告警列表:")
        for alert in report["alerts"]:
            logger.warning("  - %s", alert)

    logger.info("=" * 60)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [MONITOR] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    asyncio.run(print_health_report())
