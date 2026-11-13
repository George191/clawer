from __future__ import annotations

import asyncio
import inspect
import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError

from app.adapters.runtime import get_source_module
from app.config.settings import settings
from app.etl.base import ETLBase, extract_meta
from app.etl.normalizers import get_normalizer
from app.etl.normalizers.base import safe_date, safe_datetime, safe_str
from app.etl.orbital_history import ensure_history_schema, event_lineage, write_orbital_output
from app.etl.schema.catalogs import (
    CATALOG_ODS_COLUMNS,
    current_insert_sql,
    current_table_ddl,
    ods_columns,
    validate_table_name,
)
from app.etl.schema.registry import (
    ensure_ontology_tables,
    get_registered_ddl,
    save_registered_ddl,
)
from app.etl.source_fences import POSITION_COLUMNS, source_revision_current
from app.logger import get_logger

logger = get_logger(__name__)
_ODS_WRITE_MAX_ATTEMPTS = 3
_ODS_WRITE_RETRY_WAIT = 0.5
_ODS_BATCH_MAX_ROWS = 50
_ODS_BATCH_MAX_BYTES = 4 * 1024 * 1024


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
        await ensure_ontology_tables(self._pg)
        for table in CATALOG_ODS_COLUMNS:
            if not self._table_name or table == self._table_name:
                await self._ensure_registered_table(table, "current", create_partitions=False)
                if table == "orbital":
                    await ensure_history_schema(self._pg)

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
        if table == "orbital":
            await ensure_history_schema(self._pg)
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

    async def _dispatch_batch(self, messages, owner, partition):
        if not 2 <= len(messages) <= _ODS_BATCH_MAX_ROWS:
            return None
        try:
            inputs = [message.value for message in messages]
            if any(not isinstance(message, dict)
                   or not message.get("data_source")
                   or not message.get("record_id") for message in inputs):
                return None
            table = validate_table_name(inputs[0].get("data_type"))
            if table == "orbital":
                return None
            data_source = inputs[0]["data_source"]
            if (self._table_name and self._table_name != table
                    or any(message.get("data_type") != table
                           or message["data_source"] != data_source for message in inputs)):
                return None
            def identity(row):
                return tuple(row[key] for key in ("record_id", "data_source", "data_type"))
            identities = [identity(message) for message in inputs]
            if len(set(identities)) != len(identities):
                return None
            module = await get_source_module(data_source, export="NORMALIZERS")
            normalizer = getattr(module, "NORMALIZERS", {}).get(table) or get_normalizer(table, data_source)
            if normalizer is None:
                return None
            fields = getattr(module, "ODS_FIELDS", {})
            now = datetime.now(timezone.utc)
            prepared, sources, source_outputs = [], [], []
            total_bytes = 0
            for message in inputs:
                total_bytes += len(json.dumps(message, ensure_ascii=False, default=str).encode("utf8"))
                if total_bytes > _ODS_BATCH_MAX_BYTES:
                    return None
                raw_data = {**message.get("raw_data", message), "data_source": message["data_source"]}
                raw_meta = extract_meta(raw_data)
                if tuple(raw_meta.get(key) for key in ("record_id", "data_source", "data_type")) != identity(message):
                    return None
                normalized = normalizer(raw_data)
                if inspect.isawaitable(normalized):
                    normalized = await normalized
                if isinstance(normalized, list) and len(normalized) == 1:
                    normalized = normalized[0]
                if isinstance(normalized, dict):
                    normalized_records = [normalized]
                else:
                    return None
                source = {
                    **{name: message[name] for name in ("record_id", "data_source", "data_type")},
                    "meta": raw_data.get("_meta") or {},
                    "position": {name: message.get(name) for name in POSITION_COLUMNS},
                }
                outputs = []
                for normalized_record in normalized_records:
                    payload = await self._prepare_payload(normalized_record, source, fields, now)
                    total_bytes += len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf8"))
                    if total_bytes > _ODS_BATCH_MAX_BYTES:
                        return None
                    if (identity(payload) != identity(source)
                            or (prepared and payload.keys() != prepared[0].keys())):
                        return None
                    outputs.append(payload)
                prepared.extend(outputs)
                sources.append(source)
                source_outputs.append(outputs)
            # Shared document identities require the original sequential semantics.
            if len({identity(payload) for payload in prepared}) != len(prepared):
                return None
            if not self._batch_owned(owner, partition):
                return False
            ordered = sorted(sources, key=identity)
            parameters = {
                f"{key}_{index}": source[key]
                for index, source in enumerate(ordered)
                for key in ("record_id", "data_source", "data_type")
            }
            values = ", ".join(
                f"(CAST(:record_id_{index} AS text), CAST(:data_source_{index} AS text), CAST(:data_type_{index} AS text))"
                for index in range(len(ordered))
            )
            source_sql = (
                "SELECT src.record_id, src.data_source, src.data_type, src.raw_data->'_meta' AS meta, "
                "src.kafka_topic,src.kafka_partition,src.kafka_offset "
                f"FROM ts_rds.rds_{table} AS src JOIN (VALUES " + values + ") "
                "AS incoming(record_id, data_source, data_type) "
                "USING (record_id, data_source, data_type) "
                "ORDER BY src.record_id, src.data_source, src.data_type FOR SHARE OF src"
            )
            results = {}
            async with self._pg.session() as session:
                rows = (await session.execute(text(source_sql), parameters)).mappings().all()
                current = {identity(row): row for row in rows}
                if len(current) != len(identities) or current.keys() != set(identities):
                    raise ValueError("ODS batch source RDS row is missing")
                eligible = []
                for source, outputs in zip(sources, source_outputs, strict=True):
                    if source_revision_current(source, current[identity(source)]):
                        eligible.extend(outputs)
                if not self._batch_owned(owner, partition):
                    raise RuntimeError("ODS batch assignment changed before commit")
                if eligible:
                    eligible.sort(key=identity)
                    groups = {}
                    for payload in eligible:
                        key = (payload["data_type"], tuple(payload))
                        groups.setdefault(key, []).append(payload)
                    for (output_table, names), payloads in sorted(groups.items()):
                        columns = {name: self._columns[output_table][name] for name in names}
                        for start in range(0, len(payloads), _ODS_BATCH_MAX_ROWS):
                            batch = payloads[start:start + _ODS_BATCH_MAX_ROWS]
                            sql = current_insert_sql("ods", output_table, columns, row_count=len(batch))
                            parameters = ({f"{key}_{index}": value for index, payload in enumerate(batch) for key, value in payload.items()}
                                          if len(batch) > 1 else batch[0])
                            rows = (await session.execute(text(sql), parameters)).mappings().all()
                            batch_results = {identity(row): dict(row) for row in rows}
                            if len(batch_results) != len(batch) or batch_results.keys() != {identity(payload) for payload in batch}:
                                raise ValueError("Unexpected ODS batch RETURNING identities")
                            results.update(batch_results)
                    if len(results) != len(eligible) or results.keys() != {identity(payload) for payload in eligible}:
                        raise ValueError("Unexpected ODS batch RETURNING identities")
                if not self._batch_owned(owner, partition):
                    raise RuntimeError("ODS batch assignment changed before commit")
        except Exception:
            # Includes uncertain COMMIT ACKs; close the session before replay.
            logger.exception("%s ODS batch failed; using single-record path", self._log_prefix)
            return None
        try:
            for payload in prepared:
                if not self._batch_owned(owner, partition):
                    return False
                result = results.get(identity(payload))
                if result is not None:
                    await self._emit(result, record_id=payload["record_id"], data_source=payload["data_source"], data_type=payload["data_type"])
        except Exception:
            logger.exception("%s ODS batch output failed; replaying input batch", self._log_prefix)
            return False
        return True

    async def _source_is_current(self, session, source: dict[str, Any]) -> bool:
        source_table = source["data_type"]
        validate_table_name(source_table)
        if source_table == "orbital":
            row = (await session.execute(text(
                "SELECT raw_data = CAST(:raw_data AS jsonb) AS identical FROM ts_rds.rds_orbital "
                "WHERE record_id=:record_id AND data_source=:data_source AND data_type=:data_type FOR SHARE"
            ), {**source, "raw_data": json.dumps(source["raw_data"], ensure_ascii=False, default=str)})).mappings().first()
            if row is None or not row["identical"]:
                raise ValueError("ODS orbital input differs from its immutable RDS event")
            return True
        source_row = (await session.execute(text(
            f"SELECT raw_data->'_meta' AS meta,kafka_topic,kafka_partition,kafka_offset FROM ts_rds.rds_{source_table} "
            "WHERE record_id=:record_id AND data_source=:data_source "
            "AND data_type=:data_type FOR SHARE"
        ), {name: source[name] for name in ("record_id", "data_source", "data_type")})).mappings().first()
        if source_row is None:
            raise ValueError("ODS source RDS row is missing")
        return source_revision_current(source, source_row)

    async def _prepare_payload(self, normalized, source, fields, now) -> dict[str, Any]:
        if not isinstance(normalized, dict):
            raise ValueError("Normalizer must return a dictionary or list of dictionaries")
        output_table = normalized.get("data_type")
        if output_table is None or output_table == "":
            output_table = source["data_type"]
        validate_table_name(output_table)
        normalized_record_id = normalized.get("record_id")
        if normalized_record_id is None or normalized_record_id == "":
            normalized_record_id = source["record_id"]
        if not isinstance(normalized_record_id, str) or not normalized_record_id.strip():
            raise ValueError("ODS output record_id must be a non-empty string")
        await self._ensure_type(output_table, fields.get(output_table))
        columns = self._columns[output_table]
        if source["data_type"] == "orbital":
            if output_table != "orbital":
                raise ValueError("Orbital events must normalize to orbital")
            normalized_record_id = source["meta"]["record_id"]
            normalized = {**dict.fromkeys(columns), **normalized, **event_lineage(source["meta"])}
        unknown = normalized.keys() - columns.keys()
        if unknown:
            raise ValueError(f"Undeclared ODS fields for {output_table}: {sorted(unknown)}")
        payload = {
            **normalized, "record_id": normalized_record_id, "data_source": source["data_source"],
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
        return payload

    async def _write_outputs(
        self, prepared: list[dict[str, Any]], source: dict[str, Any],
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """Commit all outputs of one source revision together, then allow emission."""
        # Stable target lock order; duplicate identities retain their input order.
        # Results below retain the normalizer's original output order.
        order = sorted(range(len(prepared)), key=lambda i: (
            prepared[i]["data_type"], str(prepared[i]["record_id"]), prepared[i]["data_source"],
        ))
        statements = [text(current_insert_sql(
            "ods", payload["data_type"],
            {name: self._columns[payload["data_type"]][name] for name in payload},
        )) for payload in prepared]
        for attempt in range(_ODS_WRITE_MAX_ATTEMPTS):
            failed_payload = None
            try:
                results: dict[int, dict[str, Any]] = {}
                async with self._pg.session() as session:
                    if not await self._source_is_current(session, source):
                        return []
                    for index in order:
                        failed_payload = prepared[index]
                        if source["data_type"] == "orbital":
                            row = await write_orbital_output(session, failed_payload, self._columns["orbital"])
                            if row is not None:
                                results[index] = row
                            continue
                        row = (await session.execute(statements[index], failed_payload)).mappings().first()
                        if row is None:
                            raise ValueError("ODS upsert returned no record")
                        results[index] = dict(row)
                return [(results[index], payload) for index, payload in enumerate(prepared) if index in results]
            except Exception as exc:
                if attempt == _ODS_WRITE_MAX_ATTEMPTS - 1:
                    raise
                # The session has rolled back before repair or retry. Never
                # fall back to committing outputs separately.
                if failed_payload is not None and self._is_recoverable_table_error(exc):
                    table = failed_payload["data_type"]
                    await self._recover_registered_table(table)
                    if table == "orbital":
                        await ensure_history_schema(self._pg)
                    await self._ensure_partitions_for_payload(table, failed_payload)
                    continue
                if not isinstance(exc, InterfaceError | OperationalError):
                    raise
                delay = _ODS_WRITE_RETRY_WAIT * (2 ** attempt)
                logger.warning(
                    "%s ODS transaction retry %d/%d source=%s record_id=%s error=%s",
                    self._log_prefix, attempt + 1, _ODS_WRITE_MAX_ATTEMPTS,
                    source["data_source"], source["record_id"], exc,
                )
                await asyncio.sleep(delay)
        raise RuntimeError("ODS write exhausted retries")

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
            event_raw = message.get("raw_data", message)
            if table == "orbital":
                lineage = event_lineage(event_raw.get("_meta") or {})
                if lineage["source_event_id"] != record_id:
                    raise ValueError("Orbital RDS identity must equal event_id")
            raw_data = {**event_raw, "data_source": data_source}
            normalized_records = normalizer(raw_data)
            if inspect.isawaitable(normalized_records):
                normalized_records = await normalized_records
            if isinstance(normalized_records, dict):
                normalized_records = [normalized_records]
            if not isinstance(normalized_records, list) or not normalized_records:
                raise ValueError(f"Normalizer returned no records: {table}:{data_source}")
            now = datetime.now(timezone.utc)
            source = {
                "record_id": record_id, "data_source": data_source, "data_type": table,
                "meta": raw_data.get("_meta") or {},
                "position": {name: message.get(name) for name in POSITION_COLUMNS},
                "raw_data": event_raw,
            }
            prepared = [await self._prepare_payload(normalized, source, fields, now)
                        for normalized in normalized_records]
            emitted_records = await self._write_outputs(prepared, source)
            for result, payload in emitted_records:
                await self._emit(
                    result, record_id=payload["record_id"], data_source=data_source,
                    data_type=payload["data_type"],
                )
            return True
        except Exception:
            logger.exception("%s Failed to normalize table=%s", self._log_prefix, table)
            return False
