"""ES 同步器配置 — 同步规则、字段映射、连接参数。

配置来源优先级:
    1. 环境变量（SPIDER_ 前缀，见 settings.py）
    2. YAML 配置文件（可选，路径通过 SPIDER_ES_SYNCER_CONFIG 指定）
    3. 代码内置默认值

字段映射:
    RDS 表结构: record_id / data_source / data_type / raw_data(JSONB) / updated_at
    ES 文档:    record_id 作为 _id, raw_data 展开为顶层字段 + 元数据字段
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class TableSyncConfig:
    """单表同步配置。

    定义一张 RDS 表到 ES 索引的同步规则。
    """

    table_name: str                           # RDS 表名（不含 schema 前缀）, 如 "patent"
    schema_name: str = "ts_rds"               # RDS schema 名
    target_index: str = ""                    # ES 索引名（空则用 index_prefix + table_name）
    watermark_column: str = "updated_at"      # 增量水位线列
    doc_id_field: str = "record_id"           # ES 文档 _id 来源字段
    include_metadata: bool = True             # 是否包含元数据字段（data_source/data_type 等）
    include_raw_data: bool = True             # 是否将 raw_data 展开为顶层字段
    field_mapping: dict[str, str] = field(    # 字段重命名映射（source_field → target_field）
        default_factory=dict
    )
    exclude_fields: list[str] = field(        # 排除不同步的字段
        default_factory=list
    )

    @property
    def source_table(self) -> str:
        """源表全名（schema.table）。"""
        return f"{self.schema_name}.rds_{self.table_name}"

    @property
    def index_name(self) -> str:
        """目标 ES 索引名。"""
        if self.target_index:
            return self.target_index
        return f"{settings.es_index_prefix}{self.table_name}"


@dataclass
class SyncConfig:
    """ES 同步器全局配置。

    汇聚环境变量和配置文件的参数。
    """

    poll_interval: int = field(default_factory=lambda: settings.es_syncer_poll_interval)
    batch_size: int = field(default_factory=lambda: settings.es_syncer_batch_size)
    max_retries: int = field(default_factory=lambda: settings.es_syncer_max_retries)
    retry_backoff: float = field(default_factory=lambda: settings.es_syncer_retry_backoff)

    # ES 连接参数
    es_url: str = field(default_factory=lambda: settings.es_url)
    es_username: str = field(default_factory=lambda: settings.es_username)
    es_password: str = field(default_factory=lambda: settings.es_password)
    es_index_prefix: str = field(default_factory=lambda: settings.es_index_prefix)

    # 同步器名称（用于水位线表标识）
    syncer_name: str = "es_syncer"

    # 表级配置
    tables: list[TableSyncConfig] = field(default_factory=list)

    @classmethod
    def from_settings(cls) -> SyncConfig:
        """从 settings 构建默认配置。

        根据 es_syncer_tables 配置自动生成表级配置。
        """
        table_names = [
            t.strip() for t in settings.es_syncer_tables.split(",") if t.strip()
        ]
        tables = [
            TableSyncConfig(table_name=name) for name in table_names
        ]
        return cls(tables=tables)

    @classmethod
    def from_yaml(cls, path: str | Path) -> SyncConfig:
        """从 YAML 配置文件加载（覆盖默认配置）。

        YAML 格式示例:
            syncer_name: es_syncer
            poll_interval: 5
            batch_size: 100
            es_url: http://es:9200
            tables:
              - table_name: patent
                target_index: spider_patent_v2
                field_mapping:
                  title: patent_title
                exclude_fields:
                  - kafka_offset
              - table_name: news
        """
        try:
            import yaml
        except ImportError as e:
            raise ImportError("PyYAML 未安装，请执行: pip install pyyaml") from e

        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"ES 同步器配置文件不存在: {path}")

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        config = cls.from_settings()

        # 覆盖全局参数
        for key in ("syncer_name", "poll_interval", "batch_size",
                    "max_retries", "retry_backoff",
                    "es_url", "es_username", "es_password", "es_index_prefix"):
            if key in data:
                setattr(config, key, data[key])

        # 覆盖表级配置
        if "tables" in data:
            config.tables = []
            for table_data in data["tables"]:
                config.tables.append(TableSyncConfig(
                    table_name=table_data["table_name"],
                    schema_name=table_data.get("schema_name", "ts_rds"),
                    target_index=table_data.get("target_index", ""),
                    watermark_column=table_data.get("watermark_column", "updated_at"),
                    doc_id_field=table_data.get("doc_id_field", "record_id"),
                    include_metadata=table_data.get("include_metadata", True),
                    include_raw_data=table_data.get("include_raw_data", True),
                    field_mapping=table_data.get("field_mapping", {}),
                    exclude_fields=table_data.get("exclude_fields", []),
                ))

        logger.info(
            "ES syncer config loaded from %s: %d tables",
            path, len(config.tables),
        )
        return config


def build_doc_from_row(
    row: dict[str, Any],
    table_config: TableSyncConfig,
) -> tuple[str, dict[str, Any]]:
    """将 RDS 数据库行转换为 ES 文档。

    Args:
        row: 数据库行（record_id/data_source/data_type/raw_data/updated_at 等）
        table_config: 表同步配置

    Returns:
        (doc_id, doc) 元组
    """
    import json

    doc_id = str(row.get(table_config.doc_id_field, ""))
    doc: dict[str, Any] = {}

    # 展开原始数据（raw_data JSONB）
    if table_config.include_raw_data and "raw_data" in row:
        raw = row["raw_data"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                # 应用字段重命名
                mapped_key = table_config.field_mapping.get(key, key)
                if mapped_key not in table_config.exclude_fields:
                    doc[mapped_key] = value

    # 添加元数据字段
    if table_config.include_metadata:
        for meta_field in ("record_id", "data_source", "data_type",
                           "created_at", "updated_at"):
            if meta_field in row and meta_field not in table_config.exclude_fields:
                value = row[meta_field]
                # 时间戳转为 ISO 字符串
                if hasattr(value, "isoformat"):
                    value = value.isoformat()
                doc[meta_field] = value

    return doc_id, doc
