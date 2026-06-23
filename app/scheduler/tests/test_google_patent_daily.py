"""Google Patent 按日采集任务 — 单元测试。

测试覆盖:
    - 日期格式化
    - 查询字符串构造
    - 日期范围工具函数
    - API 上限检测逻辑
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.scheduler.tasks.google_patent_daily import (
    MAX_PAGES_PER_QUERY,
    MAX_RESULTS_PER_QUERY,
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
