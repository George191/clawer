from __future__ import annotations

from datetime import date, datetime, timezone
import re
from typing import Literal

TableRole = Literal["current", "history"]
_IDENTIFIER_PATTERN = r"[A-Za-z_][A-Za-z0-9_]*"
PG_SCHEMA_PATTERN = rf"{_IDENTIFIER_PATTERN}(?:\.{_IDENTIFIER_PATTERN})*"


def normalize_table_role(table_role: str | None) -> TableRole:
    role = (table_role or "current").strip().lower()
    if role not in {"current", "history"}:
        raise ValueError(f"Unsupported table role: {table_role}")
    return role  # type: ignore[return-value]


def is_schema_identifier(value: str) -> bool:
    return isinstance(value, str) and len(value) <= 63 and re.fullmatch(PG_SCHEMA_PATTERN, value) is not None


def sql_identifier(value: str, *, schema: bool = False) -> str:
    valid = is_schema_identifier(value) if schema else (
        isinstance(value, str) and len(value) <= 63 and re.fullmatch(_IDENTIFIER_PATTERN, value) is not None
    )
    if not valid:
        raise ValueError(f"Invalid Ontology PostgreSQL identifier: {value!r}")
    return f'"{value}"'


def qualified_table_name(schema_name: str, table_name: str) -> str:
    return f"{sql_identifier(schema_name, schema=True)}.{sql_identifier(table_name)}"


def partition_bounds(
    value: date | datetime,
    granularity: str,
) -> tuple[datetime, datetime, str]:
    if isinstance(value, date) and not isinstance(value, datetime):
        pivot = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    else:
        pivot = value.astimezone(timezone.utc)

    if granularity == "month":
        start = datetime(pivot.year, pivot.month, 1, tzinfo=timezone.utc)
        if pivot.month == 12:
            end = datetime(pivot.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(pivot.year, pivot.month + 1, 1, tzinfo=timezone.utc)
        return start, end, start.strftime("%Y%m")

    if granularity == "year":
        start = datetime(pivot.year, 1, 1, tzinfo=timezone.utc)
        end = datetime(pivot.year + 1, 1, 1, tzinfo=timezone.utc)
        return start, end, start.strftime("%Y")

    raise ValueError(f"Unsupported partition granularity: {granularity}")
