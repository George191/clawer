"""Celery Beat 定时调度配置。

定义所有定时任务的执行频率和参数。
使用 crontab 表达式控制执行时间。
"""

from __future__ import annotations

from celery.schedules import crontab


def build_beat_schedule() -> dict:
    """构建 Beat 调度配置。

    Returns:
        Celery beat_schedule 字典，key 为任务别名，
        value 包含 task / schedule / args / kwargs 等。
    """
    return {
        # ── Google Patent 每日采集 ──
        # 每天 UTC 06:00 执行（采集前一天发布的专利）
        "google-patent-daily": {
            "task": "app.scheduler.tasks.google_patent_daily.crawl_daily",
            "schedule": crontab(hour=6, minute=0),
            "args": (),
            "kwargs": {},
            "options": {
                "queue": "patent",
                "expires": 6 * 3600,  # 6小时后过期（避免堆积）
            },
        },

        # ── Google Patent 每周全量补采 ──
        # 每周一 UTC 03:00 执行（采集过去 7 天的专利，补漏）
        "google-patent-weekly-backfill": {
            "task": "app.scheduler.tasks.google_patent_daily.crawl_date_range",
            "schedule": crontab(hour=3, minute=0, day_of_week=1),
            "args": (),
            "kwargs": {
                "days_back": 7,
            },
            "options": {
                "queue": "patent",
                "expires": 12 * 3600,
            },
        },
    }
