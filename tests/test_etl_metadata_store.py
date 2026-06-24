from app.storage.etl_metadata_store import _build_current_ddl, _build_history_ddl


_CURRENT_RDS_DDL = """CREATE TABLE IF NOT EXISTS ts_rds.rds_news (
    record_id TEXT PRIMARY KEY,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rds_news_data_source ON ts_rds.rds_news (data_source);
CREATE INDEX IF NOT EXISTS idx_rds_news_created_at ON ts_rds.rds_news (created_at DESC);
"""


def test_build_current_ddl_rewrites_plain_table_to_hash_partitioned_parent() -> None:
    ddl_sql = _build_current_ddl(
        layer="rds",
        logical_table="news",
        current_ddl_sql=_CURRENT_RDS_DDL,
        partition_type="hash",
        partition_column="record_id",
    )

    assert "CREATE TABLE IF NOT EXISTS ts_rds.rds_news (" in ddl_sql
    assert ") PARTITION BY HASH (record_id);" in ddl_sql
    assert "CREATE INDEX IF NOT EXISTS idx_rds_news_data_source ON ts_rds.rds_news" in ddl_sql


def test_build_current_ddl_is_idempotent() -> None:
    ddl_sql = _build_current_ddl(
        layer="rds",
        logical_table="news",
        current_ddl_sql=_CURRENT_RDS_DDL,
        partition_type="hash",
        partition_column="record_id",
    )

    rebuilt = _build_current_ddl(
        layer="rds",
        logical_table="news",
        current_ddl_sql=ddl_sql,
        partition_type="hash",
        partition_column="record_id",
    )

    assert rebuilt == ddl_sql


def test_build_history_ddl_rewrites_hash_parent_to_range_history_table() -> None:
    current_hash_ddl = _build_current_ddl(
        layer="rds",
        logical_table="news",
        current_ddl_sql=_CURRENT_RDS_DDL,
        partition_type="hash",
        partition_column="record_id",
    )

    ddl_sql = _build_history_ddl(
        layer="rds",
        logical_table="news",
        current_ddl_sql=current_hash_ddl,
        partition_type="range",
        partition_column="updated_at",
    )

    assert "CREATE TABLE IF NOT EXISTS ts_rds_hist.rds_news (" in ddl_sql
    assert "history_id BIGSERIAL" in ddl_sql
    assert "record_id TEXT NOT NULL," in ddl_sql
    assert "record_id TEXT PRIMARY KEY" not in ddl_sql
    assert ") PARTITION BY RANGE (updated_at);" in ddl_sql
    assert "ON ts_rds_hist.rds_news (data_source);" in ddl_sql
    assert "idx_rds_news_record_id" in ddl_sql
    assert "idx_rds_news_updated_at" in ddl_sql


def test_build_history_ddl_is_idempotent_from_rebuilt_current_ddl() -> None:
    current_hash_ddl = _build_current_ddl(
        layer="rds",
        logical_table="news",
        current_ddl_sql=_CURRENT_RDS_DDL,
        partition_type="hash",
        partition_column="record_id",
    )

    ddl_sql = _build_history_ddl(
        layer="rds",
        logical_table="news",
        current_ddl_sql=current_hash_ddl,
        partition_type="range",
        partition_column="updated_at",
    )
    rebuilt = _build_history_ddl(
        layer="rds",
        logical_table="news",
        current_ddl_sql=current_hash_ddl,
        partition_type="range",
        partition_column="updated_at",
    )

    assert rebuilt == ddl_sql
