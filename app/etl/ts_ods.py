from __future__ import annotations

import asyncio
import inspect
import json
import re
from datetime import datetime, timezone
from functools import partial
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError

from app.adapters.runtime import get_source_module
from app.config.settings import settings
from app.etl.base import ETLBase
from app.etl.normalizers import get_normalizer
from app.etl.normalizers.base import safe_date, safe_datetime, safe_str
from app.etl.schema.catalogs import (
    CATALOG_ODS_COLUMNS,
    current_insert_sql,
    current_table_ddl,
    ods_columns,
    validate_table_name,
)
from app.etl.schema.registry import (
    ensure_ddlregistry_table,
    get_registered_ddl,
    save_registered_ddl,
)
from app.logger import get_logger

logger = get_logger(__name__)
_ODS_WRITE_MAX_ATTEMPTS = 3
_ODS_WRITE_RETRY_WAIT = 0.5


class TsOds(ETLBase):
    _layer = "ods"
    _consumer_pattern = rf"^{re.escape(settings.etl_rds_topic)}-[a-z][a-z0-9_]*$"
    _consumer_group = settings.etl_ods_consumer_group
    _producer_topic = settings.etl_ods_topic
    _producer_client_id = "etl-ts-ods-producer"

    def __init__(self, table_name: str = "") -> None:
        super().__init__(table_name)
        self._columns: dict[str, dict[str, str]] = {}
        if table_name:
            validate_table_name(table_name)
            self._consumer_topics = [f"{settings.etl_rds_topic}-{table_name}"]
            self._consumer_pattern = ""

    async def _on_init_schema(self) -> None:
        await self._pg.execute("CREATE SCHEMA IF NOT EXISTS ts_ods")
        await ensure_ddlregistry_table(self._pg)
        for table in CATALOG_ODS_COLUMNS:
            if not self._table_name or table == self._table_name:
                await self._ensure_registered_table(table, "current", create_partitions=False)

    async def _ensure_type(self, table: str, fields: dict[str, str] | None) -> None:
        validate_table_name(table)
        columns = CATALOG_ODS_COLUMNS.get(table)
        if columns is not None:
            if fields:
                for name, sql_type in fields.items():
                    if name not in columns or not columns[name].upper().startswith(sql_type.upper()):
                        raise ValueError(f"Published field conflicts with built-in ODS schema: {table}.{name}")
        elif fields:
            columns = ods_columns(table, dict(sorted(fields.items())))
        else:
            raise ValueError(f"Missing ODS_FIELDS declaration for new data_type: {table}")
        if table in self._columns:
            if self._columns[table] != columns:
                raise ValueError(f"Conflicting ODS schema for {table}; schema changes require review")
            return
        ddl = current_table_ddl("ods", table, columns)
        existing = await get_registered_ddl(self._pg, "ods", table, "current")
        if existing and table not in CATALOG_ODS_COLUMNS and existing.strip() != ddl.strip():
            raise ValueError(f"ODS schema change requires review: {table}")
        if not existing or table not in CATALOG_ODS_COLUMNS:
            await save_registered_ddl(
                self._pg, layer="ods", table=table, ddl_sql=ddl, updated_by="system:ods", allow_update=False,
            )
        await self._ensure_registered_table(table, "current")
        self._columns[table] = dict(columns)

    async def _dispatch(self, message: dict[str, Any]) -> bool:
        table = message.get("data_type")
        try:
            validate_table_name(table)
        except ValueError:
            logger.exception("%s Invalid business data_type", self._log_prefix)
            return False
        if self._table_name and table != self._table_name:
            return False
        return await self._process_ods_record(message, table)

    async def _write_current(
        self,
        *,
        table: str,
        payload: dict[str, Any],
        source: dict[str, Any],
    ) -> dict[str, Any] | None:
        """单条 ODS 入库，带可重试错误重试与显式事务语义。

        session() 上下文已提供 commit/rollback：成功提交、异常回滚。
        此处在连接级瞬时错误（InterfaceError/OperationalError）上做有限重试，
        schema 级错误由上层 _execute_with_table_recovery 处理。
        """
        columns = {name: self._columns[table][name] for name in payload}
        current_sql = current_insert_sql("ods", table, columns)
        last_error: Exception | None = None

        for attempt in range(_ODS_WRITE_MAX_ATTEMPTS):
            try:
                async with self._pg.session() as session:
                    source_table = source["data_type"]
                    validate_table_name(source_table)
                    source_row = (await session.execute(text(
                        f"SELECT raw_data->'_meta' AS meta FROM ts_rds.rds_{source_table} "
                        "WHERE record_id=:record_id AND data_source=:data_source "
                        "AND data_type=:data_type FOR SHARE"
                    ), {name: source[name] for name in ("record_id", "data_source", "data_type")})).mappings().first()
                    if source_row is None:
                        raise ValueError("ODS source RDS row is missing")
                    def version(meta: dict[str, Any]) -> tuple[datetime, int]:
                        return (
                            safe_datetime(meta.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc),
                            int(meta.get("download_version") or 0),
                        )
                    incoming_version = version(source.get("meta") or {})
                    current_version = version(source_row["meta"] or {})
                    if incoming_version < current_version:
                        return None
                    if incoming_version > current_version:
                        raise ValueError("ODS message is ahead of its RDS source version")
                    result = await session.execute(text(current_sql), payload)
                    current_row = result.mappings().first()
                    if attempt > 0:
                        logger.info(
                            "%s ODS write recovered on attempt %d/%d table=%s",
                            self._log_prefix, attempt + 1, _ODS_WRITE_MAX_ATTEMPTS, table,
                        )
                    return dict(current_row) if current_row else None
            except (InterfaceError, OperationalError) as exc:
                last_error = exc
                if attempt < _ODS_WRITE_MAX_ATTEMPTS - 1:
                    delay = _ODS_WRITE_RETRY_WAIT * (2 ** attempt)
                    logger.warning(
                        "%s ODS write retry %d/%d table=%s record_id=%s error=%s",
                        self._log_prefix, attempt + 1, _ODS_WRITE_MAX_ATTEMPTS,
                        table, payload.get("record_id"), exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
        # 理论不可达，防御性抛出
        raise last_error if last_error else RuntimeError("ODS write exhausted retries")

    async def _process_ods_record(self, message: dict[str, Any], table: str) -> bool:
        try:
            record_id = message.get("record_id")
            data_source = safe_str(message.get("data_source"))
            if not data_source or not record_id:
                raise ValueError("ODS requires business record_id and data_source from RDS")
            module = await get_source_module(data_source, export="NORMALIZERS")
            normalizers = getattr(module, "NORMALIZERS", {})
            normalizer = normalizers.get(table) or get_normalizer(table, data_source)
            if normalizer is None:
                raise ValueError(f"Missing normalizer: {table}:{data_source}")
            fields = getattr(module, "ODS_FIELDS", {})
            raw_data = {**message.get("raw_data", message), "data_source": data_source}
            normalized_records = normalizer(raw_data)
            if inspect.isawaitable(normalized_records):
                normalized_records = await normalized_records
            if isinstance(normalized_records, dict):
                normalized_records = [normalized_records]
            if not isinstance(normalized_records, list) or not normalized_records:
                raise ValueError(f"Normalizer returned no records: {table}:{data_source}")
            now = datetime.now(timezone.utc)
            emitted_records = []
            source = {
                "record_id": record_id, "data_source": data_source, "data_type": table,
                "meta": raw_data.get("_meta") or {},
            }
            for normalized in normalized_records:
                if not isinstance(normalized, dict):
                    raise ValueError("Normalizer must return a dictionary or list of dictionaries")
                output_table = normalized.get("data_type") or table
                normalized_record_id = normalized.get("record_id") or record_id
                await self._ensure_type(output_table, fields.get(output_table))
                columns = self._columns[output_table]
                unknown = normalized.keys() - columns.keys()
                if unknown:
                    raise ValueError(f"Undeclared ODS fields for {output_table}: {sorted(unknown)}")
                payload = {
                    **normalized, "record_id": normalized_record_id, "data_source": data_source,
                    "data_type": output_table, "created_at": now, "updated_at": now,
                }
                for name, value in payload.items():
                    sql_type = columns[name].split()[0].lower()
                    if sql_type == "jsonb" and value is not None and not isinstance(value, str):
                        payload[name] = json.dumps(value, ensure_ascii=False)
                    elif sql_type in {"date", "timestamptz"} and value is not None:
                        converted = safe_date(value) if sql_type == "date" else safe_datetime(value)
                        if converted is None:
                            raise ValueError(f"Invalid {sql_type} value for {output_table}.{name}")
                        payload[name] = converted
                result = await self._execute_with_table_recovery(
                    output_table, partial(self._write_current, table=output_table, payload=payload, source=source),
                    payload=payload,
                )
                if result is not None:
                    emitted_records.append((result, payload))
            for result, payload in emitted_records:
                await self._emit(
                    result, record_id=payload["record_id"], data_source=data_source,
                    data_type=payload["data_type"],
                )
            return True
        except Exception:
            logger.exception("%s Failed to normalize table=%s", self._log_prefix, table)
            return False
