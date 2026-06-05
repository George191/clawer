"""ETL ODS 层 — 操作数据存储层（标准化与清洗）。

职责：
- 从 Kafka 消费 RDS 处理后的数据 (spider-rds-processed)
- 按 data_type/data_source 路由到对应的标准化处理器
- 字段映射、类型转换、空值处理、异常数据标记
- RDS assets 中的 MinIO 路径替换到 patent 对应的资源字段中
- 标准化结果入库 ts_ods 表
- 推送到 TASK/DWD 层 Kafka Topic

标准化器位于 app/etl/normalizers/ 包中，按类型/源拆分。
扩展新数据源：在 normalizers/ 下创建模块并注册即可。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.etl.base import ETLBase
from app.etl.normalizers import get_normalizer

logger = logging.getLogger(__name__)

ODS_PATENT_DDL = """
CREATE TABLE IF NOT EXISTS ts_ods.ods_patent (
    id                  BIGSERIAL,
    data_source         VARCHAR(128)    NOT NULL,
    data_type           VARCHAR(64)     NOT NULL,
    record_id           VARCHAR(256)    NOT NULL,

    title               TEXT,
    publication_number       VARCHAR(128),
    application_number  VARCHAR(128),
    assignee            TEXT,
    inventor            TEXT,
    publication_date    DATE,
    filing_date         DATE,
    priority_date       DATE,
    grant_date          DATE,
    abstract            TEXT,
    claims              JSONB,
    legal_status        VARCHAR(64),
    ipc_classification  VARCHAR(256),
    cpc_classification  VARCHAR(256),
    patent_type         VARCHAR(64),

    original_file       TEXT,
    thumbnail           TEXT,
    figures             JSONB,

    quality_score       DOUBLE PRECISION,
    quality_flags       JSONB,
    extra_data          JSONB,

    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_ods_patent PRIMARY KEY (id, created_at),
    CONSTRAINT uq_ods_record UNIQUE (record_id, data_source, created_at)
) PARTITION BY RANGE (created_at);
"""

ODS_PATENT_INSERT = """
INSERT INTO ts_ods.ods_patent (
    data_source, data_type, record_id,
    title, publication_number, application_number, assignee, inventor,
    publication_date, filing_date, priority_date, grant_date,
    abstract, claims, legal_status,
    ipc_classification, cpc_classification, patent_type,
    original_file, thumbnail, figures,
    quality_score, quality_flags, extra_data,
    created_at, updated_at
) VALUES (
    :data_source, :data_type, :record_id,
    :title, :publication_number, :application_number, :assignee, :inventor,
    CAST(:publication_date AS date), CAST(:filing_date AS date),
    CAST(:priority_date AS date), CAST(:grant_date AS date),
    :abstract, CAST(:claims AS jsonb), :legal_status,
    :ipc_classification, :cpc_classification, :patent_type,
    :original_file, :thumbnail, CAST(:figures AS jsonb),
    CAST(:quality_score AS float), CAST(:quality_flags AS jsonb), CAST(:extra_data AS jsonb),
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, created_at) DO UPDATE SET
    title = EXCLUDED.title,
    publication_number = EXCLUDED.publication_number,
    application_number = EXCLUDED.application_number,
    assignee = EXCLUDED.assignee,
    inventor = EXCLUDED.inventor,
    publication_date = EXCLUDED.publication_date,
    filing_date = EXCLUDED.filing_date,
    priority_date = EXCLUDED.priority_date,
    grant_date = EXCLUDED.grant_date,
    abstract = EXCLUDED.abstract,
    claims = EXCLUDED.claims,
    legal_status = EXCLUDED.legal_status,
    ipc_classification = EXCLUDED.ipc_classification,
    cpc_classification = EXCLUDED.cpc_classification,
    patent_type = EXCLUDED.patent_type,
    original_file = EXCLUDED.original_file,
    thumbnail = EXCLUDED.thumbnail,
    figures = EXCLUDED.figures,
    quality_score = EXCLUDED.quality_score,
    quality_flags = EXCLUDED.quality_flags,
    extra_data = EXCLUDED.extra_data,
    updated_at = EXCLUDED.updated_at
RETURNING *
"""


class TsOds(ETLBase):
    _layer = "ods"
    _consumer_topics = [settings.etl_rds_topic]
    _consumer_group = settings.etl_ods_consumer_group
    _producer_topic = settings.etl_ods_topic
    _producer_client_id = "etl-ts-ods-producer"

    async def _ddl_for_table(self, table: str) -> str:
        ddl_map = {
            "patent": ODS_PATENT_DDL,
        }
        ddl = ddl_map.get(table, "")
        if not ddl:
            logger.warning("%s No DDL defined for table '%s'", self._log_prefix, table)
        return ddl

    async def _handler_patent(self, message: dict[str, Any]) -> bool:
        try:
            data_type = message.get("data_type", "")
            data_source = message.get("data_source", "")
            record_id = message.get("record_id", "")

            raw_data = message.get("raw_data", message)
            normalizer = get_normalizer(data_type, data_source)
            normalized = normalizer(raw_data)

            now = datetime.now(timezone.utc)

            result = await self._pg.fetch_one(
                ODS_PATENT_INSERT,
                {
                    **normalized,
                    "created_at": now,
                    "updated_at": now,
                },
            )

            await self._emit(result, record_id=record_id, data_source=data_source, data_type=data_type)
            
            logger.debug(
                "%s Normalized publication_number=%s source=%s → TASK/DWD",
                self._log_prefix,
                normalized.get("publication_number"),
                data_source,
            )
            return True

        except Exception:
            logger.exception("%s Failed to normalize message", self._log_prefix)
            return False