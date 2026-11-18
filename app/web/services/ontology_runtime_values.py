"""Strict property validation and deterministic business record identity."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from pydantic import JsonValue

if TYPE_CHECKING:
    from app.web.api.models.ontology_registry import Definition, Property


def _bad(message: str) -> None:
    raise HTTPException(422, message)


def _normalized_value(prop: Property, value: JsonValue) -> Any:
    if value is None:
        if prop.required:
            _bad(f"{prop.id} cannot be null")
        return None
    category = prop.type
    valid = False
    if category == "string":
        valid = isinstance(value, str) and len(value) <= 16_384
    elif category == "integer":
        valid = type(value) is int and -(2**63) <= value < 2**63
    elif category == "number":
        valid = (
            type(value) is int
            and -(2**63) <= value < 2**63
            or type(value) is float
            and math.isfinite(value)
        )
    elif category == "boolean":
        valid = type(value) is bool
    elif category in ("date", "datetime") and isinstance(value, str):
        try:
            if category == "date":
                parsed = date.fromisoformat(value).isoformat()
                if parsed == value:
                    return parsed
            else:
                if "T" not in value:
                    _bad(f"{prop.id} timestamp must use ISO 8601")
                if re.search(r"[.,][0-9]{6}[0-9]*[1-9]", value):
                    _bad(f"{prop.id} timestamp must be representable at microsecond precision")
                timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if timestamp.tzinfo is not None:
                    return timestamp.astimezone(timezone.utc).isoformat()
        except (OverflowError, ValueError):
            pass
    elif category == "json":
        valid = True
    if not valid:
        _bad(f"{prop.id} must be a valid {category}")
    return value


def normalize_fields(definition: Definition, values: dict[str, JsonValue]) -> dict[str, Any]:
    """Normalize declared field values without type coercions or non-JSON numbers."""
    properties = {prop.id: prop for prop in definition.properties}
    if values.keys() - properties.keys():
        _bad("Action references unknown fields")
    normalized = {key: _normalized_value(properties[key], value) for key, value in values.items()}
    check_record_json(normalized)
    return normalized


def check_record_json(values: dict[str, Any]) -> None:
    """Bound cumulative record size and reject invalid PostgreSQL text values."""
    remaining: list[Any] = [values]
    while remaining:
        value = remaining.pop()
        if isinstance(value, dict):
            remaining.extend(value.keys())
            remaining.extend(value.values())
        elif isinstance(value, list):
            remaining.extend(value)
        elif isinstance(value, str) and "\x00" in value:
            _bad("Record must not contain null characters")
    try:
        encoded = json.dumps(values, ensure_ascii=False, allow_nan=False).encode("utf-8")
        valid = len(encoded) <= 262_144
    except (ValueError, OverflowError, RecursionError):
        valid = False
    if not valid:
        _bad("Action record must contain valid finite JSON and fit within 256 KiB")


def record_key(definition: Definition, values: dict[str, Any]) -> str:
    """Hash a typed and ordered business key; equal numeric keys normalize identically."""
    types = {prop.id: prop.type for prop in definition.properties}
    components: list[Any] = []
    for field in definition.primaryKey:
        value = values.get(field)
        if value is None:
            _bad("Primary key values are required")
        if types[field] == "number":
            # Fixed-format Decimal avoids float/int inverse coercion and signed-zero ambiguity.
            decimal = Decimal(str(value))
            value = "0" if not decimal else format(decimal, "f")
            if "." in value:
                value = value.rstrip("0").rstrip(".")
        components.append([field, types[field], value])
    canonical = json.dumps(components, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()
