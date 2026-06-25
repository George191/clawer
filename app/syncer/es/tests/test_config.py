"""SyncConfig / TableSyncConfig — 单元测试。

测试配置构建、字段映射、文档转换逻辑。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.syncer.es.config import SyncConfig, TableSyncConfig, build_doc_from_row


class TestTableSyncConfig:
    """测试 TableSyncConfig。"""

    def test_default_values(self) -> None:
        cfg = TableSyncConfig(table_name="patent")
        assert cfg.table_name == "patent"
        assert cfg.schema_name == "ts_rds"
        assert cfg.watermark_column == "updated_at"
        assert cfg.doc_id_field == "record_id"
        assert cfg.include_metadata is True
        assert cfg.include_raw_data is True

    def test_source_table(self) -> None:
        cfg = TableSyncConfig(table_name="patent")
        assert cfg.source_table == "ts_rds.rds_patent"

    def test_source_table_custom_schema(self) -> None:
        cfg = TableSyncConfig(table_name="news", schema_name="ts_ods")
        assert cfg.source_table == "ts_ods.rds_news"

    def test_index_name_default(self) -> None:
        cfg = TableSyncConfig(table_name="patent")
        # 默认使用 settings.es_index_prefix
        assert cfg.index_name == "spider_patent"

    def test_index_name_custom(self) -> None:
        cfg = TableSyncConfig(table_name="patent", target_index="custom_index")
        assert cfg.index_name == "custom_index"


class TestSyncConfig:
    """测试 SyncConfig。"""

    def test_from_settings(self) -> None:
        config = SyncConfig.from_settings()
        assert config.syncer_name == "es_syncer"
        assert len(config.tables) > 0
        # 默认应包含 patent 表
        table_names = [t.table_name for t in config.tables]
        assert "patent" in table_names

    def test_from_yaml_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            SyncConfig.from_yaml(tmp_path / "nonexistent.yaml")

    def test_from_yaml_valid(self, tmp_path: Path) -> None:
        yaml_content = """
syncer_name: custom_syncer
poll_interval: 5
batch_size: 100
tables:
  - table_name: patent
    target_index: custom_patent
    field_mapping:
      title: patent_title
    exclude_fields:
      - kafka_offset
  - table_name: news
"""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        config = SyncConfig.from_yaml(yaml_file)
        assert config.syncer_name == "custom_syncer"
        assert config.poll_interval == 5
        assert config.batch_size == 100
        assert len(config.tables) == 2

        patent = config.tables[0]
        assert patent.table_name == "patent"
        assert patent.target_index == "custom_patent"
        assert patent.field_mapping == {"title": "patent_title"}
        assert patent.exclude_fields == ["kafka_offset"]

        news = config.tables[1]
        assert news.table_name == "news"


class TestBuildDocFromRow:
    """测试 build_doc_from_row 文档转换。"""

    def test_basic_conversion(self) -> None:
        now = datetime.now(timezone.utc)
        row = {
            "record_id": "rec-001",
            "data_source": "google_patent",
            "data_type": "patent",
            "raw_data": json.dumps({"title": "Test Patent", "assignee": "Google"}),
            "updated_at": now,
        }
        cfg = TableSyncConfig(table_name="patent")

        doc_id, doc = build_doc_from_row(row, cfg)
        assert doc_id == "rec-001"
        # raw_data 应展开
        assert doc["title"] == "Test Patent"
        assert doc["assignee"] == "Google"
        # 元数据应包含
        assert doc["record_id"] == "rec-001"
        assert doc["data_source"] == "google_patent"
        assert doc["updated_at"] == now.isoformat()

    def test_raw_data_already_dict(self) -> None:
        """raw_data 已为 dict 时应直接使用。"""
        row = {
            "record_id": "rec-002",
            "raw_data": {"title": "Direct Dict"},
        }
        cfg = TableSyncConfig(table_name="patent")
        _, doc = build_doc_from_row(row, cfg)
        assert doc["title"] == "Direct Dict"

    def test_raw_data_invalid_json(self) -> None:
        """raw_data 为无效 JSON 字符串时应跳过。"""
        row = {
            "record_id": "rec-003",
            "raw_data": "not-json",
        }
        cfg = TableSyncConfig(table_name="patent")
        _, doc = build_doc_from_row(row, cfg)
        # 不应包含 raw_data 字段
        assert "raw_data" not in doc

    def test_field_mapping(self) -> None:
        """字段重命名映射应生效。"""
        row = {
            "record_id": "rec-004",
            "raw_data": {"title": "Mapped Title"},
        }
        cfg = TableSyncConfig(
            table_name="patent",
            field_mapping={"title": "patent_title"},
        )
        _, doc = build_doc_from_row(row, cfg)
        assert "patent_title" in doc
        assert doc["patent_title"] == "Mapped Title"
        assert "title" not in doc

    def test_exclude_fields(self) -> None:
        """排除字段应生效。"""
        row = {
            "record_id": "rec-005",
            "raw_data": {"title": "Keep", "secret": "Drop"},
            "data_source": "src",
        }
        cfg = TableSyncConfig(
            table_name="patent",
            exclude_fields=["secret", "data_source"],
        )
        _, doc = build_doc_from_row(row, cfg)
        assert "title" in doc
        assert "secret" not in doc
        assert "data_source" not in doc

    def test_include_raw_data_false(self) -> None:
        """include_raw_data=False 时不应展开 raw_data。"""
        row = {
            "record_id": "rec-006",
            "raw_data": {"title": "Should Not Appear"},
            "data_source": "src",
        }
        cfg = TableSyncConfig(
            table_name="patent",
            include_raw_data=False,
        )
        _, doc = build_doc_from_row(row, cfg)
        assert "title" not in doc
        assert "data_source" in doc  # 元数据仍包含

    def test_include_metadata_false(self) -> None:
        """include_metadata=False 时不应包含元数据。"""
        row = {
            "record_id": "rec-007",
            "data_source": "src",
            "raw_data": {"title": "Only This"},
        }
        cfg = TableSyncConfig(
            table_name="patent",
            include_metadata=False,
        )
        _, doc = build_doc_from_row(row, cfg)
        assert "title" in doc
        assert "data_source" not in doc
        assert "record_id" not in doc

    def test_custom_doc_id_field(self) -> None:
        """自定义 doc_id_field。"""
        row = {
            "record_id": "rec-008",
            "data_source": "google",
            "raw_data": {"patent_number": "US123"},
        }
        cfg = TableSyncConfig(
            table_name="patent",
            doc_id_field="data_source",
        )
        doc_id, _ = build_doc_from_row(row, cfg)
        assert doc_id == "google"

    def test_missing_doc_id(self) -> None:
        """doc_id 字段缺失时应返回空字符串。"""
        row = {"raw_data": {"title": "No ID"}}
        cfg = TableSyncConfig(table_name="patent")
        doc_id, _ = build_doc_from_row(row, cfg)
        assert doc_id == ""
