from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import partial
from typing import Any

from sqlalchemy import text

from app.config.settings import settings
from app.etl.base import ETLBase, extract_meta

logger = logging.getLogger(__name__)

_RDS_INSERT_TEMPLATE = """
INSERT INTO ts_rds.rds_{table_name}
    (record_id, data_source, data_type, raw_data, kafka_offset, kafka_partition, kafka_topic, created_at, updated_at)
VALUES
    (:record_id, :data_source, :data_type, CAST(:raw_data AS jsonb), :kafka_offset, :kafka_partition, :kafka_topic,
     CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz))
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    data_source = EXCLUDED.data_source,
    data_type = EXCLUDED.data_type,
    raw_data = EXCLUDED.raw_data,
    kafka_offset = EXCLUDED.kafka_offset,
    kafka_partition = EXCLUDED.kafka_partition,
    kafka_topic = EXCLUDED.kafka_topic,
    updated_at = EXCLUDED.updated_at
RETURNING *
"""


class TsRds(ETLBase):
    _layer = "rds"
    _consumer_topics = [settings.etl_raw_topic]
    _consumer_group = settings.etl_rds_consumer_group
    _producer_topic = settings.etl_rds_topic
    _producer_client_id = "etl-ts-rds-producer"

    async def _handler_news(self, message: dict[str, Any]) -> bool:
        return await self._process_rds_record(message, table="news")

    async def _handler_patent(self, message: dict[str, Any]) -> bool:
        return await self._process_rds_record(message, table="patent")

    async def _handler_navwarn(self, message: dict[str, Any]) -> bool:
        return await self._process_rds_record(message, table="navwarn")

    async def _handler_intelligence(self, message: dict[str, Any]) -> bool:
        return await self._process_rds_record(message, table="intelligence")

    async def _write_current(
        self,
        *,
        table: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        current_sql = _RDS_INSERT_TEMPLATE.replace("{table_name}", table)

        async with self._pg.session() as session:
            result = await session.execute(text(current_sql), payload)
            current_row = result.mappings().first()
            return dict(current_row) if current_row else None

    async def _process_rds_record(self, message: dict[str, Any], table: str) -> bool:
        try:
            meta = extract_meta(message)
            data_source = meta.get("data_source") or ""
            data_type = meta.get("data_type") or table
            record_id = meta.get("record_id") or message.get("record_id") or ""

            if not record_id:
                logger.warning("%s Message missing record_id, skipping table=%s", self._log_prefix, table)
                return False

            raw_data = json.loads(json.dumps(message, default=str))
            kafka_meta = message.get("_kafka_meta", {}) or {}
            now = datetime.now(timezone.utc)
            payload = {
                "record_id": record_id,
                "data_source": data_source,
                "data_type": data_type,
                "raw_data": json.dumps(raw_data, ensure_ascii=False),
                "kafka_offset": kafka_meta.get("kafka_offset"),
                "kafka_partition": kafka_meta.get("kafka_partition"),
                "kafka_topic": kafka_meta.get("kafka_topic"),
                "created_at": now,
                "updated_at": now,
            }

            result = await self._execute_with_table_recovery(
                table,
                partial(self._write_current, table=table, payload=payload),
                payload=payload,
            )

            await self._emit(result, record_id=record_id, data_source=data_source, data_type=data_type)
            logger.debug(
                "%s Upserted table=rds_%s record_id=%s source=%s -> ODS",
                self._log_prefix,
                table,
                record_id,
                data_source,
            )
            return True
        except Exception:
            logger.exception("%s Failed to process rds_%s record", self._log_prefix, table)
            return False
