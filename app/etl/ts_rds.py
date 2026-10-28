from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from functools import partial
from typing import Any

from sqlalchemy import text

from app.config.settings import settings
from app.etl.base import ETLBase, extract_meta
from app.etl.schema.baselines import RDS_CURRENT_BASELINE_DDLS
from app.etl.schema.catalogs import (
    current_insert_sql,
    current_table_ddl,
    table_columns,
    validate_table_name,
)
from app.etl.schema.registry import (
    ensure_ddlregistry_table,
    get_registered_ddl,
    save_registered_ddl,
)
from app.logger import get_logger
from app.records.values import require_canonical_record

logger = get_logger(__name__)


class TsRds(ETLBase):
    _layer = "rds"
    _consumer_pattern = (
        rf"^(?:{re.escape(settings.etl_raw_topic)}|{re.escape(settings.kafka_topic)}-[a-z][a-z0-9_]*)$"
    )
    _consumer_group = settings.etl_rds_consumer_group
    _producer_topic = settings.etl_rds_topic
    _producer_client_id = "etl-ts-rds-producer"

    def __init__(self, table_name: str = "") -> None:
        super().__init__(table_name)
        self._ready_types: set[str] = set()
        if table_name:
            validate_table_name(table_name)
            self._consumer_topics = [f"{settings.kafka_topic}-{table_name}"]
            self._consumer_pattern = ""

    async def _on_init_schema(self) -> None:
        await self._pg.execute("CREATE SCHEMA IF NOT EXISTS ts_rds")
        await ensure_ddlregistry_table(self._pg)
        for table in RDS_CURRENT_BASELINE_DDLS:
            if not self._table_name or table == self._table_name:
                await self._ensure_registered_table(table, "current", create_partitions=False)

    async def _ensure_type(self, table: str) -> None:
        validate_table_name(table)
        if table in self._ready_types:
            return
        if not await get_registered_ddl(self._pg, "rds", table, "current"):
            await save_registered_ddl(
                self._pg, layer="rds", table=table,
                ddl_sql=current_table_ddl("rds", table), updated_by="system:rds", allow_update=False,
            )
        await self._ensure_registered_table(table, "current")
        self._ready_types.add(table)

    async def _dispatch(self, message: dict[str, Any]) -> bool:
        table = extract_meta(message).get("data_type")
        try:
            validate_table_name(table)
        except ValueError:
            logger.exception("%s Invalid record data_type", self._log_prefix)
            return False
        if self._table_name and table != self._table_name:
            return False
        return await self._process_rds_record(message, table)

    async def _write_current(
        self,
        *,
        table: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        current_sql = current_insert_sql("rds", table, table_columns("rds", table))

        async with self._pg.session() as session:
            # Legacy BSON timestamps were serialized without an offset but represent UTC.
            await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            result = await session.execute(text(current_sql), payload)
            current_row = result.mappings().first()
            return dict(current_row) if current_row else None

    def _prepare_payload(self, message: dict[str, Any], table: str) -> dict[str, Any]:
        require_canonical_record(message)
        meta = extract_meta(message)
        data_source = meta.get("data_source") or ""
        data_type = meta.get("data_type") or table
        record_id = meta.get("record_id") or message.get("record_id") or ""

        if not record_id:
            logger.warning("%s Message missing record_id, skipping table=%s", self._log_prefix, table)
            raise ValueError("RDS input identity is missing")
        if not data_source:
            logger.warning("%s Message missing source template, skipping table=%s", self._log_prefix, table)
            raise ValueError("RDS input identity is missing")

        raw_data = dict(message)
        kafka_meta = message.get("_kafka_meta", {}) or {}
        now = datetime.now(timezone.utc)
        payload = {
            "record_id": record_id,
            "data_source": data_source,
            "data_type": data_type,
            "raw_data": json.dumps(raw_data, ensure_ascii=False, default=str),
            "kafka_offset": kafka_meta.get("kafka_offset"),
            "kafka_partition": kafka_meta.get("kafka_partition"),
            "kafka_topic": kafka_meta.get("kafka_topic"),
            "created_at": now,
            "updated_at": now,
        }
        return payload

    async def _dispatch_batch(self, messages, owner, partition):
        # A batch failure falls back to the existing one-record/repair path.
        # Unknown commit ACKs are replayed, never treated as successful offsets.
        if len(messages) < 2 or len(messages) > 50:
            return None
        prepared = []
        total_bytes = 0
        try:
            table = None
            for message in messages:
                if message.value is None:
                    return None
                candidate = extract_meta(message.value).get("data_type")
                validate_table_name(candidate)
                if table is not None and candidate != table:
                    return None
                if self._table_name and candidate != self._table_name:
                    return None
                table = candidate
                message.value["_kafka_meta"] = {
                    "kafka_offset": message.offset,
                    "kafka_partition": message.partition,
                    "kafka_topic": message.topic,
                }
                payload = self._prepare_payload(message.value, table)
                total_bytes += len(payload["raw_data"].encode("utf-8"))
                if total_bytes > 4 * 1024 * 1024:
                    return None
                prepared.append(payload)
            await self._ensure_type(table)
            if not self._batch_owned(owner, partition):
                return False
            current_sql = text(current_insert_sql("rds", table, table_columns("rds", table)))
            results = []
            async with self._pg.session() as session:
                await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
                for payload in prepared:
                    if not self._batch_owned(owner, partition):
                        raise RuntimeError("RDS batch assignment changed before commit")
                    row = (await session.execute(current_sql, payload)).mappings().first()
                    results.append(dict(row) if row else None)
                if not self._batch_owned(owner, partition):
                    raise RuntimeError("RDS batch assignment changed before commit")
        except Exception:
            logger.exception("%s RDS transaction batch failed; using single-record path", self._log_prefix)
            return None
        # No output is visible to downstream before the database commit succeeds.
        try:
            for payload, result in zip(prepared, results, strict=True):
                if not self._batch_owned(owner, partition):
                    return False
                if result is not None:
                    await self._emit(
                        result, record_id=payload["record_id"],
                        data_source=payload["data_source"], data_type=payload["data_type"],
                        topic=f"{settings.etl_rds_topic}-{table}",
                    )
        except Exception:
            logger.exception("%s RDS batch output failed; replaying input batch", self._log_prefix)
            return False
        return True

    async def _process_rds_record(self, message: dict[str, Any], table: str) -> bool:
        try:
            payload = self._prepare_payload(message, table)
            record_id = payload["record_id"]
            data_source = payload["data_source"]
            data_type = payload["data_type"]

            await self._ensure_type(table)
            result = await self._execute_with_table_recovery(
                table,
                partial(self._write_current, table=table, payload=payload),
                payload=payload,
            )
            if result is None:
                # A newer source version already landed; consume without re-emitting.
                return True

            await self._emit(
                result, record_id=record_id, data_source=data_source, data_type=data_type,
                topic=f"{settings.etl_rds_topic}-{table}",
            )
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
