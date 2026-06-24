from datetime import date, datetime, timezone

from app.etl.table_layout import default_table_layout, partition_bounds


def test_current_tables_default_to_unpartitioned() -> None:
    layout = default_table_layout("rds", "news", "current")

    assert layout.schema_name == "ts_rds"
    assert layout.table_name == "rds_news"
    assert layout.partition_type == "hash"
    assert layout.partition_column == "record_id"
    assert layout.partition_granularity is None
    assert layout.partition_count == 32


def test_rds_history_uses_monthly_updated_at_partition() -> None:
    layout = default_table_layout("rds", "news", "history")

    assert layout.schema_name == "ts_rds_hist"
    assert layout.table_name == "rds_news"
    assert layout.partition_type == "range"
    assert layout.partition_column == "updated_at"
    assert layout.partition_granularity == "month"


def test_ods_history_follows_table_specific_defaults() -> None:
    patent = default_table_layout("ods", "patent", "history")
    news = default_table_layout("ods", "news", "history")
    navwarn = default_table_layout("ods", "navwarn", "history")

    assert patent.partition_column == "publication_date"
    assert patent.partition_granularity == "year"
    assert news.partition_column == "source_published_at"
    assert news.partition_granularity == "month"
    assert navwarn.partition_type == "none"


def test_partition_bounds_for_month() -> None:
    start, end, suffix = partition_bounds(
        datetime(2026, 6, 24, 8, 30, tzinfo=timezone.utc),
        "month",
    )

    assert start == datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    assert suffix == "202606"


def test_partition_bounds_for_year_from_date() -> None:
    start, end, suffix = partition_bounds(date(2026, 6, 24), "year")

    assert start == datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert suffix == "2026"
