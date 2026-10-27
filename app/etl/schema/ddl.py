"""Pure DDL transformation and checksum helpers for ETL schemas."""

from __future__ import annotations

import hashlib
import json
import re

from app.etl.schema.catalogs import RECORD_IDENTITY_COLUMNS, TABLE_DEFINITIONS
from app.etl.schema.layout import default_table_layout

_CREATE_TABLE_RE = re.compile(
    r"(?P<statement>"
    r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(?P<table_ref>[A-Za-z0-9_\".]+)\s*"
    r"\((?P<body>.*?)\)\s*"
    r"(?:PARTITION BY\s+(?P<partition_type>RANGE|HASH)\s*"
    r"\((?P<partition_column>[^)]+)\))?\s*;)",
    re.IGNORECASE | re.DOTALL,
)
_RECORD_ID_INLINE_PK_RE = re.compile(
    r"(^\s*record_id\s+TEXT)\s+PRIMARY\s+KEY(\s*,?\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
_TABLE_PRIMARY_KEY_RE = re.compile(
    r"^\s*PRIMARY\s+KEY\s*\([^)]*\)\s*,?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_DECLARED_PRIMARY_KEY_RE = re.compile(r"\bPRIMARY\s+KEY\s*\(([^)]+)\)", re.IGNORECASE)


def ddl_checksum(
    ddl_sql: str,
    *,
    table_role: str,
    partition_type: str,
    partition_column: str | None,
    partition_granularity: str | None,
    partition_count: int | None,
) -> str:
    payload = json.dumps(
        {
            "ddl_sql": ddl_sql,
            "table_role": table_role,
            "partition_type": partition_type,
            "partition_column": partition_column,
            "partition_granularity": partition_granularity,
            "partition_count": partition_count,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _current_table_ref(layer: str, logical_table: str) -> str:
    layout = default_table_layout(layer, logical_table, "current")
    return f"{layout.schema_name}.{layout.table_name}"


def _parse_create_table_statement(ddl_sql: str) -> dict[str, str | None]:
    match = _CREATE_TABLE_RE.search(ddl_sql)
    if not match:
        raise ValueError("Unsupported DDL format: CREATE TABLE statement not found")
    return {
        "statement": match.group("statement").strip(),
        "table_ref": match.group("table_ref"),
        "body": match.group("body").strip("\n"),
        "partition_type": (
            match.group("partition_type").lower() if match.group("partition_type") else None
        ),
        "partition_column": (
            match.group("partition_column").strip() if match.group("partition_column") else None
        ),
    }


def _replace_create_table_statement(
    ddl_sql: str,
    *,
    statement: str,
    replacement: str,
) -> str:
    return ddl_sql.replace(statement, replacement.rstrip(), 1).rstrip()


def _build_create_table_statement(
    *,
    table_ref: str,
    body: str,
    partition_type: str,
    partition_column: str | None,
) -> str:
    ddl_sql = f"CREATE TABLE IF NOT EXISTS {table_ref} (\n{body}\n)"
    if partition_type == "range" and partition_column:
        ddl_sql += f" PARTITION BY RANGE ({partition_column})"
    elif partition_type == "hash" and partition_column:
        ddl_sql += f" PARTITION BY HASH ({partition_column})"
    return f"{ddl_sql};"


def _clean_table_body(body: str) -> str:
    lines = [line.rstrip() for line in body.splitlines() if line.strip()]
    if lines and lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]
    return "\n".join(lines)


def _append_table_constraint(body: str, constraint_sql: str) -> str:
    lines = [line.rstrip() for line in body.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Unsupported DDL format: CREATE TABLE body is empty")
    if not lines[-1].endswith(","):
        lines[-1] = f"{lines[-1]},"
    lines.append(f"    {constraint_sql}")
    return "\n".join(lines)


def _rewrite_current_primary_key(body: str, columns: tuple[str, ...]) -> str:
    rewritten = _RECORD_ID_INLINE_PK_RE.sub(r"\1 NOT NULL\2", body, count=1)
    rewritten = _TABLE_PRIMARY_KEY_RE.sub("", rewritten)
    rewritten = _clean_table_body(rewritten)
    return _append_table_constraint(
        rewritten,
        f"PRIMARY KEY ({', '.join(columns)})",
    )


def build_current_ddl(
    *,
    layer: str,
    logical_table: str,
    current_ddl_sql: str,
    partition_type: str,
    partition_column: str | None,
) -> str:
    current_ref = _current_table_ref(layer, logical_table)
    create_table = _parse_create_table_statement(current_ddl_sql)
    body = str(create_table["body"])
    is_export_ddl = bool(
        re.search(
            r"CREATE TABLE\s+\"[^\"]+\"\.\"[^\"]+\"",
            current_ddl_sql,
            re.IGNORECASE,
        )
    )
    if not is_export_ddl:
        definition = TABLE_DEFINITIONS.get(layer, {}).get(logical_table)
        declared = _DECLARED_PRIMARY_KEY_RE.search(body)
        declared_columns = (
            tuple(column.strip().strip('"') for column in declared.group(1).split(","))
            if declared else ()
        )
        primary_key = definition["primary_key"] if definition else declared_columns
        legacy_record_key = declared_columns == ("record_id",) or (
            not declared_columns and _RECORD_ID_INLINE_PK_RE.search(body)
        )
        partition_columns = tuple(
            column.strip().strip('"') for column in (partition_column or "").split(",")
        )
        if (
            not definition
            and legacy_record_key
            and partition_type == "hash"
            and partition_columns == RECORD_IDENTITY_COLUMNS
        ):
            primary_key = RECORD_IDENTITY_COLUMNS
        if primary_key and declared_columns != primary_key:
            body = _rewrite_current_primary_key(body, primary_key)
    statement = _build_create_table_statement(
        table_ref=current_ref,
        body=body,
        partition_type=partition_type,
        partition_column=partition_column,
    )
    rebuilt_ddl = _replace_create_table_statement(
        current_ddl_sql,
        statement=str(create_table["statement"]),
        replacement=statement,
    )
    if is_export_ddl:
        table_ref = re.escape(str(create_table["table_ref"]))
        rebuilt_ddl = re.sub(
            rf"ALTER TABLE\s+{table_ref}\s+ADD CONSTRAINT\s+[^;]+PRIMARY KEY\s*\([^;]+\)\s*;",
            "",
            rebuilt_ddl,
            count=1,
            flags=re.IGNORECASE,
        )
        rebuilt_ddl = re.sub(
            r"(?m)^\s*CREATE INDEX\s+(?!IF NOT EXISTS\b)",
            "CREATE INDEX IF NOT EXISTS ",
            rebuilt_ddl,
            flags=re.IGNORECASE,
        )
    return rebuilt_ddl
