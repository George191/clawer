"""Google Patent 按日采集任务。

采集逻辑
--------
1. 计算目标日期（默认为昨天 UTC）
2. 构造查询: query=after:publication:YYYYMMDD AND before:publication:YYYYMMDD
3. 调用 SpiderEngine 执行采集
4. Google Patents API 限制最多返回约 10 页数据（每页 100 条），
   超出部分需通过细化查询条件（如按 assignee 首字母分组）补采
5. 记录采集结果到 TaskStore

手动执行:
    # 采集昨天的专利
    python -m app.scheduler.tasks.google_patent_daily

    # 采集指定日期
    python -m app.scheduler.tasks.google_patent_daily --date 2026-06-20

    # 采集日期范围
    python -m app.scheduler.tasks.google_patent_daily --start 2026-06-15 --end 2026-06-20
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.config.settings import settings
from app.scheduler.task_store import get_task_store

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
#  常量
# ══════════════════════════════════════════════════════════════════════

TEMPLATE_NAME = "google_patent"

# Google Patents API 最大返回页数限制
MAX_PAGES_PER_QUERY = 10

# 每页结果数（模板默认 100）
RESULTS_PER_PAGE = 100

# 单次查询最大结果数
MAX_RESULTS_PER_QUERY = MAX_PAGES_PER_QUERY * RESULTS_PER_PAGE  # 1000


# ══════════════════════════════════════════════════════════════════════
#  日期工具
# ══════════════════════════════════════════════════════════════════════

def format_patent_date(d: date) -> str:
    """将日期转换为 Google Patents API 日期格式 YYYYMMDD。"""
    return d.strftime("%Y%m%d")


def build_date_range_query(
    target_date: date,
    date_field: str = "publication",
) -> str:
    """构造按日过滤的 Google Patents 查询字符串。

    使用 before 和 after 同时限定，确保只返回目标日期的专利。

    Args:
        target_date: 目标日期
        date_field: 日期类型 (publication / priority / filing)

    Returns:
        查询字符串，如 "after:publication:20260622 AND before:publication:20260622"
    """
    date_str = format_patent_date(target_date)
    return f"after:{date_field}:{date_str} AND before:{date_field}:{date_str}"


def get_yesterday_utc() -> date:
    """获取 UTC 昨天日期。"""
    return datetime.now(timezone.utc).date() - timedelta(days=1)


# ══════════════════════════════════════════════════════════════════════
#  采集核心逻辑
# ══════════════════════════════════════════════════════════════════════

async def _crawl_single_query(
    query: str,
    max_pages: int = MAX_PAGES_PER_QUERY,
) -> dict[str, Any]:
    """执行单次 Google Patents 查询采集。

    Args:
        query: 查询字符串
        max_pages: 最大页数（Google API 限制 10 页）

    Returns:
        采集结果摘要
    """
    from app.engine.spider_engine import SpiderEngine
    from app.engine.template_loader import TemplateLoader

    loader = TemplateLoader()
    engine = SpiderEngine()

    try:
        # 加载模板并注入查询参数
        template = loader.load(TEMPLATE_NAME, param_values={"query": query})

        # 覆盖 max_pages 限制，防止 API 超出限制后空转
        if template.list_pagination:
            template.list_pagination.max_pages = max_pages

        logger.info("开始采集: query=%s, max_pages=%d", query, max_pages)

        result = await engine.crawl(template)

        summary = {
            "query": query,
            "total_records": result.total,
            "success": result.success,
            "errors": result.errors,
            "downloaded_files": len(result.downloaded_files),
        }

        if result.success:
            logger.info("采集成功: query=%s, records=%d", query, result.total)
        else:
            logger.warning(
                "采集完成(有错误): query=%s, records=%d, errors=%s",
                query, result.total, result.errors[:3],
            )

        return summary

    except Exception as e:
        logger.exception("采集异常: query=%s, error=%s", query, e)
        return {
            "query": query,
            "total_records": 0,
            "success": False,
            "errors": [str(e)],
            "downloaded_files": 0,
        }
    finally:
        await engine.close()


async def crawl_by_date(
    target_date: date,
    task_id: str | None = None,
) -> dict[str, Any]:
    """采集指定日期的专利数据。

    Args:
        target_date: 目标日期
        task_id: Celery 任务 ID（用于状态追踪）

    Returns:
        采集结果汇总
    """
    task_store = get_task_store()
    query = build_date_range_query(target_date)
    task_name = f"google_patent_daily:{target_date.isoformat()}"

    # 记录任务创建
    if task_id:
        await task_store.record_created(
            task_id=task_id,
            task_name=task_name,
            params={"date": target_date.isoformat(), "query": query},
        )
        await task_store.record_started(task_id)

    logger.info("=" * 70)
    logger.info("Google Patent 按日采集: date=%s", target_date.isoformat())
    logger.info("查询条件: %s", query)
    logger.info("=" * 70)

    try:
        result = await _crawl_single_query(query)

        # 检查是否达到 API 上限（可能存在遗漏）
        hit_limit = result["total_records"] >= MAX_RESULTS_PER_QUERY
        if hit_limit:
            logger.warning(
                "查询结果达到 API 上限 (%d 条)，可能存在数据遗漏。"
                "建议通过细化查询条件（如按 assignee 分组）补采。",
                MAX_RESULTS_PER_QUERY,
            )

        summary = {
            "date": target_date.isoformat(),
            "query": query,
            "total_records": result["total_records"],
            "success": result["success"],
            "errors": result["errors"],
            "hit_api_limit": hit_limit,
        }

        if task_id:
            await task_store.record_success(task_id, result=summary)

        return summary

    except Exception as e:
        error_msg = str(e)
        logger.exception("按日采集失败: date=%s", target_date.isoformat())

        if task_id:
            await task_store.record_failure(task_id, error_msg)

        return {
            "date": target_date.isoformat(),
            "query": query,
            "total_records": 0,
            "success": False,
            "errors": [error_msg],
            "hit_api_limit": False,
        }


async def crawl_date_range(
    start_date: date,
    end_date: date,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """采集日期范围内的专利数据（逐日采集）。

    Args:
        start_date: 起始日期（含）
        end_date: 结束日期（含）
        task_id: Celery 任务 ID

    Returns:
        每日采集结果列表
    """
    if start_date > end_date:
        raise ValueError(f"起始日期不能晚于结束日期: {start_date} > {end_date}")

    task_store = get_task_store()
    task_name = f"google_patent_range:{start_date}_{end_date}"

    if task_id:
        await task_store.record_created(
            task_id=task_id,
            task_name=task_name,
            params={"start": start_date.isoformat(), "end": end_date.isoformat()},
        )
        await task_store.record_started(task_id)

    total_days = (end_date - start_date).days + 1
    logger.info("日期范围采集: %s ~ %s (共 %d 天)", start_date, end_date, total_days)

    results: list[dict[str, Any]] = []
    current = start_date

    while current <= end_date:
        logger.info("处理 %d/%d: %s", len(results) + 1, total_days, current.isoformat())

        try:
            daily_result = await crawl_by_date(current)
            results.append(daily_result)
        except Exception as e:
            logger.exception("日期 %s 采集异常: %s", current, e)
            results.append({
                "date": current.isoformat(),
                "total_records": 0,
                "success": False,
                "errors": [str(e)],
            })

        # 日期间间隔，避免请求过于密集
        if current < end_date:
            await asyncio.sleep(2)

        current += timedelta(days=1)

    # 汇总
    total_records = sum(r.get("total_records", 0) for r in results)
    success_days = sum(1 for r in results if r.get("success"))
    failed_days = len(results) - success_days

    logger.info("=" * 70)
    logger.info("范围采集完成: %d 天, 成功 %d 天, 失败 %d 天, 共 %d 条记录",
                total_days, success_days, failed_days, total_records)
    logger.info("=" * 70)

    if task_id:
        await task_store.record_success(task_id, result={
            "total_days": total_days,
            "success_days": success_days,
            "failed_days": failed_days,
            "total_records": total_records,
        })

    return results


# ══════════════════════════════════════════════════════════════════════
#  Celery Task 定义
# ══════════════════════════════════════════════════════════════════════

try:
    from app.scheduler.celery_app import app
    from celery import shared_task

    @app.task(
        name="app.scheduler.tasks.google_patent_daily.crawl_daily",
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

        try:
            return asyncio.run(crawl_by_date(target_date, task_id=task_id))
        except Exception as exc:
            raise self.retry(exc=exc, countdown=300)

    @app.task(
        name="app.scheduler.tasks.google_patent_daily.crawl_date_range",
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

        try:
            return asyncio.run(crawl_date_range(start_date, end_date, task_id=task_id))
        except Exception as exc:
            raise self.retry(exc=exc, countdown=600)

except ImportError:
    # Celery 未安装时，跳过 task 注册（允许模块被 import 用于直接调用）
    logger.warning("Celery 未安装，task 注册已跳过。请执行: pip install celery")


# ══════════════════════════════════════════════════════════════════════
#  CLI 入口（支持手动执行，不依赖 Celery）
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    """命令行入口 — 手动执行采集。"""
    parser = argparse.ArgumentParser(
        description="Google Patent 按日采集",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 采集昨天的专利
  python -m app.scheduler.tasks.google_patent_daily

  # 采集指定日期
  python -m app.scheduler.tasks.google_patent_daily --date 2026-06-20

  # 采集日期范围
  python -m app.scheduler.tasks.google_patent_daily --start 2026-06-15 --end 2026-06-20
        """,
    )
    parser.add_argument("--date", help="目标日期 YYYY-MM-DD（默认: 昨天 UTC）")
    parser.add_argument("--start", help="起始日期 YYYY-MM-DD（范围模式）")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD（范围模式）")
    parser.add_argument("--days-back", type=int, help="向前回溯天数")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [PATENT-DAILY] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.start or args.end or args.days_back is not None:
        # 范围模式
        if args.days_back is not None:
            end_date = datetime.now(timezone.utc).date()
            start_date = end_date - timedelta(days=args.days_back)
        else:
            end_date = date.fromisoformat(args.end) if args.end else get_yesterday_utc()
            start_date = date.fromisoformat(args.start) if args.start else end_date

        results = asyncio.run(crawl_date_range(start_date, end_date))

        success_count = sum(1 for r in results if r.get("success"))
        total_records = sum(r.get("total_records", 0) for r in results)
        print(f"\n范围采集完成: {len(results)} 天, 成功 {success_count} 天, 共 {total_records} 条")
    else:
        # 单日模式
        target_date = date.fromisoformat(args.date) if args.date else get_yesterday_utc()
        result = asyncio.run(crawl_by_date(target_date))

        status = "成功" if result["success"] else "失败"
        print(f"\n采集{status}: date={result['date']}, records={result['total_records']}")
        if result.get("hit_api_limit"):
            print(f"警告: 结果达到 API 上限 ({MAX_RESULTS_PER_QUERY} 条)，可能存在遗漏")


if __name__ == "__main__":
    main()
