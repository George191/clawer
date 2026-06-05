"""ETL RDS 层 — 原始数据存储层。

职责：
- 从 Kafka 消费采集推送的原始数据 (spider-crawler)
- 按 data_type 自动路由到对应 handler，数据存入 ts_rds.rds_{data_type} 分区表
- 成功入库后推送到 ODS 层 Kafka Topic

设计原则：
- 一种 data_type 对应一张分区表，通过 _handler_{data_type} 反射发现
- 需要处理的数据类型：实现对应 handler；不需要处理的类型：删除方法定义即可
- Kafka 消费偏移量自动写入 Redis，支持断点恢复
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.etl.base import ETLBase, extract_meta

logger = logging.getLogger(__name__)

_RDS_DDL_TEMPLATE = """
CREATE TABLE IF NOT EXISTS ts_rds.rds_{table_name} (
    id              BIGSERIAL,
    data_source     VARCHAR(128)    NOT NULL,
    data_type       VARCHAR(64)     NOT NULL,
    record_id       VARCHAR(256)    NOT NULL,
    raw_data        JSONB           NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(32)     NOT NULL DEFAULT 'pending',
    error_message   TEXT,
    kafka_offset    BIGINT,
    kafka_partition INTEGER,
    kafka_topic     VARCHAR(64),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_rds_{table_name} PRIMARY KEY (id, created_at),
    CONSTRAINT uq_rds_{table_name}_record UNIQUE (record_id, data_source, created_at)
) PARTITION BY RANGE (created_at);
"""

_RDS_INSERT_TEMPLATE = """
INSERT INTO ts_rds.rds_{table_name}
    (data_source, data_type, record_id, raw_data, status, kafka_offset, kafka_partition, kafka_topic, created_at)
VALUES
    (:data_source, :data_type, :record_id,
     CAST(:raw_data AS jsonb),
     :status, :kafka_offset, :kafka_partition, :kafka_topic, CAST(:created_at AS timestamptz))
ON CONFLICT (record_id, data_source, created_at) DO UPDATE SET
    raw_data = EXCLUDED.raw_data,
    status = EXCLUDED.status,
    error_message = NULL
RETURNING *
"""


class TsRds(ETLBase):
    _layer = "rds"
    _consumer_topics = [settings.etl_raw_topic]
    _consumer_group = settings.etl_rds_consumer_group
    _producer_topic = settings.etl_rds_topic
    _producer_client_id = "etl-ts-rds-producer"
    _ddl_template = _RDS_DDL_TEMPLATE

    async def _handler_patent(self, message: dict[str, Any], table: str = "patent") -> bool:
        return await self._process_rds_record(message, table=table)

    async def _process_rds_record(self, message: dict[str, Any], table: str) -> bool:
        try:
            meta = extract_meta(message)
            data_source = meta.get("data_source", "")
            data_type = meta.get("data_type", table)

            if not data_source:
                logger.warning("%s Message missing data_source, skipping", self._log_prefix)
                return False

            record_id = meta.get("record_id", "")
            if not record_id:
                record_id = str(hash(json.dumps(message, sort_keys=True, default=str)))
                logger.warning("%s Message missing _meta.record_id, fallback hash=%s", self._log_prefix, record_id)

            raw_data = json.loads(json.dumps(message, default=str))
            kafka_meta = message.get("_kafka_meta", {}) or {}

            insert_sql = _RDS_INSERT_TEMPLATE.replace("{table_name}", table)
            now = datetime.now(timezone.utc)
            result = await self._pg.fetch_one(
                insert_sql,
                {
                    "data_source": data_source,
                    "data_type": data_type,
                    "record_id": record_id,
                    "raw_data": json.dumps(raw_data),
                    "status": "processed",
                    "kafka_offset": kafka_meta.get("kafka_offset"),
                    "kafka_partition": kafka_meta.get("kafka_partition"),
                    "kafka_topic": kafka_meta.get("kafka_topic"),
                    "created_at": now,
                },
            )
            await self._emit(result, record_id=record_id, data_source=data_source, data_type=data_type)
            logger.debug(
                "%s Processed table=rds_%s record_id=%s source=%s → ODS",
                self._log_prefix, table, record_id, data_source,
            )
            return True

        except Exception:
            logger.exception("%s Failed to process rds_%s record", self._log_prefix, table)
            return False