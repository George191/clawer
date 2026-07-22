"""Google Patent 采集 — Celery Task 定义。

Celery task 内部通过 asyncio.run() 桥接到 async 采集逻辑。
每个 task 独立事件循环，task 间无连接池复用（Celery worker 进程级隔离）。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.logger import get_logger
from app.scheduler.celery_app import app
from app.scheduler.tasks.google_patent.crawler import (
    GooglePatentCrawler,
    get_yesterday_utc,
)

logger = get_logger(__name__)


@app.task(
    name="app.scheduler.tasks.google_patent.crawl_daily",
    bind=True,
    max_retries=3,
    default_retry_delay=300,  # 5 分钟后重试
)
def crawl_daily(self, target_date_str: str | None = None) -> dict[str, Any]:
    """Celery Task: 每日 Google Patent 采集。

    Args:
        target_date_str: 目标日期 YYYY-MM-DD，None 则采集昨天

    Returns:
        采集结果摘要
    """
    if target_date_str:
        target_date = date.fromisoformat(target_date_str)
    else:
        target_date = get_yesterday_utc()

    task_id = self.request.id

    async def _run() -> dict[str, Any]:
        crawler = GooglePatentCrawler()
        try:
            result = await crawler.crawl_date(target_date, task_id=task_id)
            if not result["success"]:
                raise RuntimeError("; ".join(result["errors"][:3]) or "Google Patent 采集失败")
            return result
        finally:
            await crawler.close()

    try:
        return asyncio.run(_run())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=300)


@app.task(
    name="app.scheduler.tasks.google_patent.crawl_date_range",
    bind=True,
    max_retries=2,
    default_retry_delay=600,
)
def crawl_date_range_task(
    self,
    start_date_str: str | None = None,
    end_date_str: str | None = None,
    days_back: int | None = None,
) -> list[dict[str, Any]]:
    """Celery Task: 日期范围采集。

    Args:
        start_date_str: 起始日期 YYYY-MM-DD
        end_date_str: 结束日期 YYYY-MM-DD
        days_back: 向前回溯天数（与 start_date_str 互斥）

    Returns:
        每日采集结果列表
    """
    if days_back is not None:
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=days_back)
    else:
        end_date = date.fromisoformat(end_date_str) if end_date_str else get_yesterday_utc()
        start_date = date.fromisoformat(start_date_str) if start_date_str else end_date

    task_id = self.request.id

    async def _run() -> list[dict[str, Any]]:
        crawler = GooglePatentCrawler()
        try:
            results = await crawler.crawl_date_range(start_date, end_date, task_id=task_id)
            failed = [result for result in results if not result.get("success")]
            if failed:
                raise RuntimeError(f"Google Patent 日期范围采集失败: {len(failed)} 天")
            return results
        finally:
            await crawler.close()

    try:
        return asyncio.run(_run())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=600)
