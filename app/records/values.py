"""Type-aware comparisons for persisted record values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bson import BSON, Int64

from app.records.field_names import SYSTEM_ROOT_FIELDS, validate_record_field_names


def require_canonical_record(record: dict[str, Any]) -> None:
    """Reject unsupported payloads instead of silently persisting an old schema."""
    if "source_data" in record or "source_fields" in (record.get("_meta") or {}):
        raise ValueError("Record must use the current canonical adapter schema")

    def reject_nested_wrappers(value: Any) -> None:
        if isinstance(value, Mapping):
            if "source_data" in value:
                raise ValueError("Record must use the current canonical adapter schema at every business level")
            for key, child in value.items():
                if key not in SYSTEM_ROOT_FIELDS:
                    reject_nested_wrappers(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                reject_nested_wrappers(child)

    reject_nested_wrappers(record)
    meta = record.get("_meta") or {}
    validate_record_field_names(record, data_source=meta.get("data_source") or meta.get("template"))


def record_values_equal(left: Any, right: Any) -> bool:
    """Keep booleans distinct from numbers when detecting business changes."""
    if type(left) is not type(right):
        if type(left) in (int, Int64) and type(right) in (int, Int64):
            return BSON.encode({"value": left}) == BSON.encode({"value": right})
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            record_values_equal(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(
            record_values_equal(a, b) for a, b in zip(left, right)
        )
    return left == right
