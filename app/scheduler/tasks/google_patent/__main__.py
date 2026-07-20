"""Google Patent 采集 — CLI 入口。

手动执行（不依赖 Celery）:

    # 采集昨天的专利
    python -m app.scheduler.tasks.google_patent

    # 采集指定日期
    python -m app.scheduler.tasks.google_patent --date 2026-06-20

    # 采集日期范围
    python -m app.scheduler.tasks.google_patent --start 2026-06-15 --end 2026-06-20
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone

from app.logger import setup_service_logging
from app.scheduler.tasks.google_patent.crawler import (
    MAX_RESULTS_PER_QUERY,
    GooglePatentCrawler,
    get_yesterday_utc,
)


class GooglePatentCLI:
    """Google Patent 采集命令行工具。

    封装参数解析和采集流程编排，支持单日和日期范围两种模式。
    """

    def __init__(self) -> None:
        self._parser = self._build_parser()

    # ── 参数解析 ──────────────────────────────────────────────────────

    def _build_parser(self) -> argparse.ArgumentParser:
        """构建命令行参数解析器。"""
        parser = argparse.ArgumentParser(
            description="Google Patent 按日采集",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
示例:
  # 采集昨天的专利
  python -m app.scheduler.tasks.google_patent

  # 采集指定日期
  python -m app.scheduler.tasks.google_patent --date 2026-06-20

  # 采集日期范围
  python -m app.scheduler.tasks.google_patent --start 2026-06-15 --end 2026-06-20
            """,
        )
        parser.add_argument("--date", help="目标日期 YYYY-MM-DD（默认: 昨天 UTC）")
        parser.add_argument("--start", help="起始日期 YYYY-MM-DD（范围模式）")
        parser.add_argument("--end", help="结束日期 YYYY-MM-DD（范围模式）")
        parser.add_argument("--days-back", type=int, help="向前回溯天数")
        return parser

    # ── 模式判断 ──────────────────────────────────────────────────────

    def _is_range_mode(self, args: argparse.Namespace) -> bool:
        """判断是否为日期范围模式。"""
        return bool(args.start or args.end or args.days_back is not None)

    def _resolve_date_range(self, args: argparse.Namespace) -> tuple[date, date]:
        """解析日期范围参数。"""
        if args.days_back is not None:
            end_date = datetime.now(timezone.utc).date()
            start_date = end_date - timedelta(days=args.days_back)
        else:
            end_date = date.fromisoformat(args.end) if args.end else get_yesterday_utc()
            start_date = date.fromisoformat(args.start) if args.start else end_date
        return start_date, end_date

    # ── 执行流程 ──────────────────────────────────────────────────────

    async def _run_range(self, start_date: date, end_date: date) -> None:
        """执行日期范围采集。"""
        crawler = GooglePatentCrawler()
        try:
            results = await crawler.crawl_date_range(start_date, end_date)
        finally:
            await crawler.close()

        success_count = sum(1 for r in results if r.get("success"))
        total_records = sum(r.get("total_records", 0) for r in results)
        print(f"\n范围采集完成: {len(results)} 天, 成功 {success_count} 天, 共 {total_records} 条")

    async def _run_single(self, target_date: date) -> None:
        """执行单日采集。"""
        crawler = GooglePatentCrawler()
        try:
            result = await crawler.crawl_date(target_date)
        finally:
            await crawler.close()

        status = "成功" if result["success"] else "失败"
        print(f"\n采集{status}: date={result['date']}, records={result['total_records']}")
        if result.get("hit_api_limit"):
            print(f"警告: 结果达到 API 上限 ({MAX_RESULTS_PER_QUERY} 条)，可能存在遗漏")

    def run(self) -> None:
        """命令行入口 — 手动执行采集。"""
        args = self._parser.parse_args()

        setup_service_logging("patent-daily", "INFO")

        if self._is_range_mode(args):
            start_date, end_date = self._resolve_date_range(args)
            asyncio.run(self._run_range(start_date, end_date))
        else:
            target_date = date.fromisoformat(args.date) if args.date else get_yesterday_utc()
            asyncio.run(self._run_single(target_date))


def main() -> None:
    """模块入口函数。"""
    GooglePatentCLI().run()


if __name__ == "__main__":
    main()
