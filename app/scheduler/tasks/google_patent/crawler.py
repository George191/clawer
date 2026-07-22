"""Google Patent 按日采集 — 纯采集逻辑。

本模块只负责采集，不依赖 Celery 或 CLI。
Celery task 定义在 tasks.py，CLI 入口在 __main__.py。

采集流程
--------
1. 计算目标日期（默认为昨天 UTC）
2. 构造查询: query=after:publication:YYYYMMDD AND before:publication:YYYYMMDD
3. 调用 SpiderEngine 执行采集
4. Google Patents API 限制最多返回约 10 页数据（每页 100 条），
   超出部分需通过细化查询条件（如按 assignee 首字母分组）补采
5. 记录采集结果到 TaskStore
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from app.logger import get_logger
from app.scheduler.task_store import TaskStore, get_task_store

if TYPE_CHECKING:
    from app.engine.spider_engine import SpiderEngine

logger = get_logger(__name__)

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

# 任务名常量（与 beat_schedule / monitor 对齐）
TASK_NAME_DAILY = "google_patent_daily"
TASK_NAME_RANGE = "google_patent_range"

# 日期范围采集时，每日之间的间隔（秒），避免请求过于密集
DAILY_INTERVAL_SECONDS = 2


# ══════════════════════════════════════════════════════════════════════
#  日期工具（纯函数，无状态，保持模块级）
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
#  GooglePatentCrawler — 采集器
# ══════════════════════════════════════════════════════════════════════

class GooglePatentCrawler:
    """Google Patent 采集器。

    封装 SpiderEngine 和 TaskStore 的生命周期管理，
    支持单日采集和日期范围采集。

    用法:
        crawler = GooglePatentCrawler()
        try:
            await crawler.crawl_date(target_date, task_id="xxx")
        finally:
            await crawler.close()
    """

    def __init__(
        self,
        task_store: TaskStore | None = None,
        engine: SpiderEngine | None = None,
    ) -> None:
        self._task_store = task_store
        self._engine = engine
        self._owns_engine = engine is None

    # ── 资源管理 ──────────────────────────────────────────────────────

    def _get_store(self) -> TaskStore:
        if self._task_store is None:
            self._task_store = get_task_store()
        return self._task_store

    async def _get_engine(self) -> SpiderEngine:
        if self._engine is None:
            from app.engine.spider_engine import SpiderEngine
            self._engine = SpiderEngine()
            self._owns_engine = True
        return self._engine

    async def close(self) -> None:
        """释放资源（仅关闭自建的 engine）。"""
        if self._owns_engine and self._engine is not None:
            await self._engine.close()
            self._engine = None

    # ── 采集接口 ──────────────────────────────────────────────────────

    async def crawl_single_query(
        self,
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
        from app.crawler.checkpoint import PageCheckpointStore

        engine = await self._get_engine()
        checkpoint_store = PageCheckpointStore(TEMPLATE_NAME, query)

        try:
            await checkpoint_store.connect()
            resume_page = await checkpoint_store.load()

            from app.engine.template_loader import TemplateLoader
            loader = TemplateLoader()
            template = loader.load(TEMPLATE_NAME, param_values={"query": query})

            # 覆盖 max_pages 限制，防止 API 超出限制后空转
            if template.list_pagination:
                template.list_pagination.max_pages = max_pages

            logger.info(
                "开始采集: query=%s, max_pages=%d%s",
                query,
                max_pages,
                f", resume_page={resume_page}" if resume_page else "",
            )

            result = await engine.crawl_from_page(
                template,
                resume_page,
                checkpoint_store.save,
            )

            summary = {
                "query": query,
                "total_records": result.total,
                "success": result.success,
                "errors": result.errors,
                "downloaded_files": len(result.downloaded_files),
            }

            if result.success:
                await checkpoint_store.clear()
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
            await checkpoint_store.close()

    async def crawl_date(
        self,
        target_date: date,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """采集指定日期的专利数据。

        Args:
            target_date: 目标日期
            task_id: 任务 ID（用于状态追踪，None 则不记录）

        Returns:
            采集结果汇总
        """
        store = self._get_store()
        query = build_date_range_query(target_date)

        # 记录任务创建
        if task_id:
            await store.record_created(
                task_id=task_id,
                task_name=TASK_NAME_DAILY,
                params={"date": target_date.isoformat(), "query": query},
            )
            await store.record_started(task_id)

        logger.info("=" * 70)
        logger.info("Google Patent 按日采集: date=%s", target_date.isoformat())
        logger.info("查询条件: %s", query)
        logger.info("=" * 70)

        try:
            result = await self.crawl_single_query(query)

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
                if summary["success"]:
                    await store.record_success(task_id, result=summary)
                else:
                    error_msg = "; ".join(summary["errors"][:3]) or "采集结果包含错误"
                    await store.record_failure(task_id, error_msg)

            return summary

        except Exception as e:
            error_msg = str(e)
            logger.exception("按日采集失败: date=%s", target_date.isoformat())

            if task_id:
                await store.record_failure(task_id, error_msg)

            return {
                "date": target_date.isoformat(),
                "query": query,
                "total_records": 0,
                "success": False,
                "errors": [error_msg],
                "hit_api_limit": False,
            }

    async def crawl_date_range(
        self,
        start_date: date,
        end_date: date,
        task_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """采集日期范围内的专利数据（逐日采集）。

        Args:
            start_date: 起始日期（含）
            end_date: 结束日期（含）
            task_id: 任务 ID

        Returns:
            每日采集结果列表
        """
        if start_date > end_date:
            raise ValueError(f"起始日期不能晚于结束日期: {start_date} > {end_date}")

        store = self._get_store()

        if task_id:
            await store.record_created(
                task_id=task_id,
                task_name=TASK_NAME_RANGE,
                params={"start": start_date.isoformat(), "end": end_date.isoformat()},
            )
            await store.record_started(task_id)

        total_days = (end_date - start_date).days + 1
        logger.info("日期范围采集: %s ~ %s (共 %d 天)", start_date, end_date, total_days)

        # 预热 engine（跨日复用）
        await self._get_engine()

        results: list[dict[str, Any]] = []
        current = start_date

        try:
            while current <= end_date:
                logger.info(
                    "处理 %d/%d: %s", len(results) + 1, total_days, current.isoformat(),
                )

                try:
                    daily_result = await self.crawl_date(current)
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
                    await asyncio.sleep(DAILY_INTERVAL_SECONDS)

                current += timedelta(days=1)

        finally:
            await self.close()

        # 汇总
        total_records = sum(r.get("total_records", 0) for r in results)
        success_days = sum(1 for r in results if r.get("success"))
        failed_days = len(results) - success_days

        logger.info("=" * 70)
        logger.info("范围采集完成: %d 天, 成功 %d 天, 失败 %d 天, 共 %d 条记录",
                    total_days, success_days, failed_days, total_records)
        logger.info("=" * 70)

        if task_id:
            summary = {
                "total_days": total_days,
                "success_days": success_days,
                "failed_days": failed_days,
                "total_records": total_records,
            }
            if failed_days == 0:
                await store.record_success(task_id, result=summary)
            else:
                await store.record_failure(task_id, f"{failed_days}/{total_days} 天采集失败")

        return results
