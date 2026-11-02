"""Validate source revisions against persisted rows before derived writes."""

from __future__ import annotations

import json
import hashlib
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import text

from app.etl.normalizers.base import safe_datetime
from app.etl.schema.catalogs import RECORD_IDENTITY_COLUMNS, validate_table_name

POSITION_COLUMNS = ("kafka_topic", "kafka_partition", "kafka_offset")


def business_version(meta: Mapping[str, Any]) -> tuple[datetime, int]:
    """Read the existing Mongo timestamp/download-version contract consistently."""
    return (safe_datetime(meta.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc),
            int(meta.get("download_version") or 0))


def source_revision_current(source: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    """Reject stale same-time tokens; legacy rows require their durable input position."""
    incoming_meta, current_meta = source.get("meta") or {}, current.get("meta") or {}
    incoming_version, current_version = business_version(incoming_meta), business_version(current_meta)
    if incoming_version < current_version:
        return False
    if incoming_version > current_version:
        raise ValueError("ETL message is ahead of its persisted source version")
    if incoming_meta.get("write_token") != current_meta.get("write_token"):
        return False
    if incoming_meta.get("write_token") is not None:
        return True
    position = source.get("position") or {}
    if any(position.get(name) is None or current.get(name) is None for name in POSITION_COLUMNS):
        raise ValueError("Legacy ETL source requires its persisted Kafka position")
    return all(position[name] == current[name] for name in POSITION_COLUMNS)


def reject_ambiguous_stream(incoming: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    """Different Kafka streams cannot order distinct equal-version business states."""
    def raw(row):
        value = row.get("raw_data") or {}
        return json.loads(value) if isinstance(value, str) else value

    left, right = raw(incoming), raw(current)
    if business_version(left.get("_meta") or {}) != business_version(right.get("_meta") or {}):
        return
    if (incoming.get("kafka_topic"), incoming.get("kafka_partition")) == (
        current.get("kafka_topic"), current.get("kafka_partition"),
    ):
        return
    if {key: value for key, value in left.items() if key not in {"_meta", "_kafka_meta"}} == {
        key: value for key, value in right.items() if key not in {"_meta", "_kafka_meta"}
    }:
        return
    raise ValueError("Equal-version RDS state arrived from a different Kafka stream; migrate source versions explicitly")


def _same_value(current: Any, incoming: Any) -> bool:
    if isinstance(current, Decimal):
        try:
            value = Decimal(str(incoming))
            return (current.is_nan() and value.is_nan()) or current == value
        except (InvalidOperation, TypeError, ValueError):
            return False
    if isinstance(current, datetime):
        try:
            value = datetime.fromisoformat(incoming.replace("Z", "+00:00")) if isinstance(incoming, str) else incoming
            return isinstance(value, datetime) and current == value
        except ValueError:
            return False
    if isinstance(current, (date, time)):
        try:
            value = type(current).fromisoformat(incoming) if isinstance(incoming, str) else incoming
            return current == value
        except ValueError:
            return False
    if isinstance(current, Mapping):
        return isinstance(incoming, Mapping) and current.keys() == incoming.keys() and all(
            _same_value(value, incoming[name]) for name, value in current.items()
        )
    if isinstance(current, (list, tuple)):
        return isinstance(incoming, (list, tuple)) and len(current) == len(incoming) and all(
            _same_value(left, right) for left, right in zip(current, incoming)
        )
    return type(current) is type(incoming) and current == incoming


def snapshot_matches(current: Mapping[str, Any], message: Mapping[str, Any]) -> bool:
    """Compare database-typed values with schema-decoded CDC/legacy JSON values."""
    return all(name in message and _same_value(value, message[name]) for name, value in current.items())


def snapshot_revision(row: Mapping[str, Any]) -> str:
    """Hash exact algorithm inputs; transport metadata is never an input revision.

    Task implementations currently receive the complete persisted row, so audit
    timestamps remain part of the input until tasks declare narrower inputs.
    Decimal formatting does not use a rounding context or a floating conversion.
    """
    def encode(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, datetime):
            value = value.astimezone(timezone.utc) if value.tzinfo is not None else value
            return value.isoformat(timespec="microseconds")
        if isinstance(value, (date, time)):
            return value.isoformat()
        if isinstance(value, bytes):
            return value.hex()
        raise TypeError("Unsupported source snapshot type")

    content = {name: value for name, value in row.items() if name not in {"_kafka_meta", "_cdc_meta"}}
    payload = json.dumps(content, default=encode, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _source_row(session, layer: str, message: Mapping[str, Any]) -> Mapping[str, Any]:
    """Lock one current direct upstream row around a derived result transaction."""
    if layer not in {"ods", "task"}:
        raise ValueError("Unsupported derived source layer")
    data_type = validate_table_name(message.get("data_type"))
    parameters = {name: message.get(name) for name in RECORD_IDENTITY_COLUMNS}
    if not all(parameters.values()):
        raise ValueError("Derived source identity is missing")
    predicate = " AND ".join(f"{name}=:{name}" for name in RECORD_IDENTITY_COLUMNS)
    order = ""
    if layer == "task":
        if not message.get("task"):
            raise ValueError("Task result identity is missing")
        parameters["task"] = message["task"]
        predicate += " AND task=:task"
        # Some task tables retain result history in their created_at key.
        order = " ORDER BY created_at DESC LIMIT 1"
    result = await session.execute(text(
        f"SELECT * FROM ts_{layer}.{layer}_{data_type} WHERE {predicate}{order} FOR SHARE"
    ), parameters)
    current = result.mappings().first()
    if current is None:
        raise ValueError("Derived source row is missing")
    return current


async def source_snapshot_current(session, layer: str, message: Mapping[str, Any]) -> bool:
    return snapshot_matches(await _source_row(session, layer, message), message)


async def source_snapshot_revision(session, layer: str, message: Mapping[str, Any]) -> str | None:
    current = await _source_row(session, layer, message)
    return snapshot_revision(current) if snapshot_matches(current, message) else None


async def current_ods_revision(session, message: Mapping[str, Any]) -> str:
    """Lock ODS before TASK to keep source lock ordering consistent with task writes."""
    return snapshot_revision(await _source_row(session, "ods", message))
