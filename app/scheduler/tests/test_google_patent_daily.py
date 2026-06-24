"""Google Patent 按日采集 — 单元测试。

测试覆盖:
    - 日期工具函数（纯函数）
    - 常量定义
    - GooglePatentCrawler 类的依赖注入和生命周期
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scheduler.tasks.google_patent.crawler import (
    MAX_PAGES_PER_QUERY,
    MAX_RESULTS_PER_QUERY,
    GooglePatentCrawler,
    build_date_range_query,
    format_patent_date,
    get_yesterday_utc,
)


# ══════════════════════════════════════════════════════════════════════
#  日期工具测试
# ══════════════════════════════════════════════════════════════════════

class TestFormatPatentDate:
    """测试 format_patent_date。"""

    def test_normal_date(self) -> None:
        assert format_patent_date(date(2026, 6, 22)) == "20260622"

    def test_single_digit_month_day(self) -> None:
        assert format_patent_date(date(2026, 1, 5)) == "20260105"

    def test_new_year(self) -> None:
        assert format_patent_date(date(2026, 1, 1)) == "20260101"

    def test_year_end(self) -> None:
        assert format_patent_date(date(2026, 12, 31)) == "20261231"


class TestBuildDateRangeQuery:
    """测试 build_date_range_query。"""

    def test_default_publication_field(self) -> None:
        query = build_date_range_query(date(2026, 6, 22))
        assert "after:publication:20260622" in query
        assert "before:publication:20260622" in query
        assert "AND" in query

    def test_priority_field(self) -> None:
        query = build_date_range_query(date(2026, 6, 22), date_field="priority")
        assert "after:priority:20260622" in query
        assert "before:priority:20260622" in query

    def test_filing_field(self) -> None:
        query = build_date_range_query(date(2026, 6, 22), date_field="filing")
        assert "after:filing:20260622" in query
        assert "before:filing:20260622" in query

    def test_query_format_structure(self) -> None:
        """查询字符串应包含 before 和 after，且用 AND 连接。"""
        query = build_date_range_query(date(2026, 6, 22))
        parts = query.split(" AND ")
        assert len(parts) == 2
        assert any("after:" in p for p in parts)
        assert any("before:" in p for p in parts)


class TestGetYesterdayUtc:
    """测试 get_yesterday_utc。"""

    def test_returns_yesterday(self) -> None:
        today = datetime.now(timezone.utc).date()
        yesterday = get_yesterday_utc()
        assert yesterday == today - timedelta(days=1)

    def test_returns_date_type(self) -> None:
        result = get_yesterday_utc()
        assert isinstance(result, date)


# ══════════════════════════════════════════════════════════════════════
#  常量测试
# ══════════════════════════════════════════════════════════════════════

class TestConstants:
    """测试模块常量。"""

    def test_max_results(self) -> None:
        """API 上限应为 1000（10 页 x 100 条/页）。"""
        assert MAX_RESULTS_PER_QUERY == 1000

    def test_max_pages(self) -> None:
        assert MAX_PAGES_PER_QUERY == 10


# ══════════════════════════════════════════════════════════════════════
#  GooglePatentCrawler 类测试
# ══════════════════════════════════════════════════════════════════════

class TestGooglePatentCrawler:
    """测试 GooglePatentCrawler 类。"""

    def test_init_with_defaults(self) -> None:
        """默认构造应延迟初始化依赖。"""
        crawler = GooglePatentCrawler()
        assert crawler._task_store is None
        assert crawler._engine is None
        assert crawler._owns_engine is True

    def test_init_with_injected_deps(self) -> None:
        """应支持依赖注入。"""
        mock_store = MagicMock()
        mock_engine = MagicMock()
        crawler = GooglePatentCrawler(task_store=mock_store, engine=mock_engine)
        assert crawler._task_store is mock_store
        assert crawler._engine is mock_engine
        assert crawler._owns_engine is False

    def test_get_store_uses_global_singleton(self) -> None:
        """未注入 task_store 时应使用全局单例。"""
        crawler = GooglePatentCrawler()
        with patch("app.scheduler.tasks.google_patent.crawler.get_task_store") as mock_get:
            mock_store = MagicMock()
            mock_get.return_value = mock_store
            store = crawler._get_store()
            assert store is mock_store
            # 第二次调用应复用缓存
            crawler._get_store()
            mock_get.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_only_closes_owned_engine(self) -> None:
        """close() 只关闭自建的 engine，不关闭注入的。"""
        mock_engine = AsyncMock()
        crawler = GooglePatentCrawler(engine=mock_engine)
        await crawler.close()
        mock_engine.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_closes_owned_engine(self) -> None:
        """close() 应关闭自建的 engine。"""
        crawler = GooglePatentCrawler()
        mock_engine = AsyncMock()
        crawler._engine = mock_engine
        crawler._owns_engine = True
        await crawler.close()
        mock_engine.close.assert_called_once()
        assert crawler._engine is None

    @pytest.mark.asyncio
    async def test_crawl_date_range_validates_dates(self) -> None:
        """起始日期晚于结束日期应抛 ValueError。"""
        crawler = GooglePatentCrawler()
        with pytest.raises(ValueError, match="起始日期不能晚于结束日期"):
            await crawler.crawl_date_range(
                start_date=date(2026, 6, 22),
                end_date=date(2026, 6, 21),
            )
