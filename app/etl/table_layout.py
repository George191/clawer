from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Literal

TableRole = Literal["current", "history"]
PartitionType = Literal["none", "range", "hash"]
PartitionGranularity = Literal["month", "year"]

META_SCHEMA_NAME = "ts_meta"
DEFAULT_HASH_PARTITION_COUNT = 32
RDS_CURRENT_HASH_PARTITION_COLUMN = "record_id, data_source, data_type"


@dataclass(frozen=True)
class TableLayout:
    layer: str
    logical_table: str
    table_role: TableRole
    schema_name: str
    table_name: str
    partition_type: PartitionType = "none"
    partition_column: str | None = None
    partition_granularity: PartitionGranularity | None = None
    partition_count: int | None = None

    @property
    def is_partitioned(self) -> bool:
        if self.partition_type == "range":
            return self.partition_column is not None and self.partition_granularity is not None
        if self.partition_type == "hash":
            return self.partition_column is not None and self.partition_count is not None
        return False


def normalize_table_role(table_role: str | None) -> TableRole:
    role = (table_role or "current").strip().lower()
    if role not in {"current", "history"}:
        raise ValueError(f"Unsupported table role: {table_role}")
    return role  # type: ignore[return-value]


def schema_name_for(layer: str, table_role: str | None = None) -> str:
    role = normalize_table_role(table_role)
    return f"ts_{layer}" if role == "current" else f"ts_{layer}_hist"


def physical_table_name(layer: str, logical_table: str) -> str:
    return f"{layer}_{logical_table}"


def logical_table_name(layer: str, table_name: str) -> str:
    prefix = f"{layer}_"
    return table_name[len(prefix):] if table_name.startswith(prefix) else table_name


def infer_table_role(layer: str, schema_name: str | None) -> TableRole:
    if schema_name == schema_name_for(layer, "history"):
        return "history"
    return "current"


def default_table_layout(
    layer: str,
    logical_table: str,
    table_role: str | None = None,
) -> TableLayout:
    role = normalize_table_role(table_role)
    partition_type: PartitionType = "none"
    partition_column: str | None = None
    partition_granularity: PartitionGranularity | None = None
    partition_count: int | None = None

    if role == "current" and layer == "rds":
        partition_type = "hash"
        partition_column = RDS_CURRENT_HASH_PARTITION_COLUMN
        partition_count = DEFAULT_HASH_PARTITION_COUNT

    if role == "current" and layer == "ods":
        partition_type = "hash"
        partition_column = RDS_CURRENT_HASH_PARTITION_COLUMN
        partition_count = DEFAULT_HASH_PARTITION_COUNT

    return TableLayout(
        layer=layer,
        logical_table=logical_table,
        table_role=role,
        schema_name=schema_name_for(layer, role),
        table_name=physical_table_name(layer, logical_table),
        partition_type=partition_type,
        partition_column=partition_column,
        partition_granularity=partition_granularity,
        partition_count=partition_count,
    )


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
