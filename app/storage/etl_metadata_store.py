from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import text

from app.etl.table_layout import (
    META_SCHEMA_NAME,
    default_table_layout,
    logical_table_name,
    normalize_table_role,
)
from app.storage.postgres_client import PostgresClient

_DDLREGISTRY_READY = False
_CREATE_TABLE_RE = re.compile(
    r"(?P<statement>"
    r"CREATE TABLE IF NOT EXISTS (?P<table_ref>[A-Za-z0-9_\".]+)\s*"
    r"\((?P<body>.*?)\)\s*"
    r"(?:PARTITION BY\s+(?P<partition_type>RANGE|HASH)\s*"
    r"\((?P<partition_column>[^)]+)\))?\s*;)",
    re.IGNORECASE | re.DOTALL,
)


def _ddl_checksum(
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


def _table_identity(
    layer: str,
    table: str,
    table_role: str | None = None,
) -> tuple[str, str, str]:
    layout = default_table_layout(layer, table, table_role)
    return layout.schema_name, layout.table_name, layout.table_role


def _ddlregistry_table_name() -> str:
    return f"{META_SCHEMA_NAME}.ddlregistry"


def _ddlregistry_bootstrap_sql() -> str:
    table_name = _ddlregistry_table_name()
    return f"""
    CREATE SCHEMA IF NOT EXISTS {META_SCHEMA_NAME};

    CREATE TABLE IF NOT EXISTS {table_name} (
        id BIGSERIAL PRIMARY KEY,
        layer TEXT NOT NULL,
        table_role TEXT NOT NULL DEFAULT 'current',
        schema_name TEXT NOT NULL,
        table_name TEXT NOT NULL,
        ddl_sql TEXT NOT NULL,
        version INTEGER NOT NULL,
        checksum TEXT NOT NULL,
        partition_type TEXT NOT NULL DEFAULT 'none',
        partition_column TEXT NULL,
        partition_granularity TEXT NULL,
        partition_count INTEGER NULL,
        description TEXT NULL,
        updated_by TEXT NULL,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS table_role TEXT NOT NULL DEFAULT 'current';
    ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS partition_type TEXT NOT NULL DEFAULT 'none';
    ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS partition_column TEXT NULL;
    ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS partition_granularity TEXT NULL;
    ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS partition_count INTEGER NULL;
    ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS description TEXT NULL;
    ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS updated_by TEXT NULL;
    ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
    ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
    ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

    ALTER TABLE {table_name}
        DROP CONSTRAINT IF EXISTS ddlregistry_table_role_check;
    ALTER TABLE {table_name}
        ADD CONSTRAINT ddlregistry_table_role_check
        CHECK (table_role IN ('current', 'history'));

    ALTER TABLE {table_name}
        DROP CONSTRAINT IF EXISTS ddlregistry_partition_type_check;
    ALTER TABLE {table_name}
        ADD CONSTRAINT ddlregistry_partition_type_check
        CHECK (partition_type IN ('none', 'range', 'hash'));

    ALTER TABLE {table_name}
        DROP CONSTRAINT IF EXISTS ddlregistry_partition_granularity_check;
    ALTER TABLE {table_name}
        ADD CONSTRAINT ddlregistry_partition_granularity_check
        CHECK (
            partition_granularity IS NULL
            OR partition_granularity IN ('month', 'year')
        );

    ALTER TABLE {table_name}
        DROP CONSTRAINT IF EXISTS ddlregistry_partition_layout_check;
    ALTER TABLE {table_name}
        ADD CONSTRAINT ddlregistry_partition_layout_check
        CHECK (
            (
                partition_type = 'none'
                AND partition_column IS NULL
                AND partition_granularity IS NULL
                AND partition_count IS NULL
            )
            OR (
                partition_type = 'range'
                AND partition_column IS NOT NULL
                AND partition_granularity IS NOT NULL
                AND partition_count IS NULL
            )
            OR (
                partition_type = 'hash'
                AND partition_column IS NOT NULL
                AND partition_granularity IS NULL
                AND partition_count IS NOT NULL
                AND partition_count > 0
            )
        );

    CREATE UNIQUE INDEX IF NOT EXISTS uq_ddlregistry_active_table
        ON {table_name} (layer, table_role, schema_name, table_name)
        WHERE is_active = TRUE;

    CREATE UNIQUE INDEX IF NOT EXISTS uq_ddlregistry_version
        ON {table_name} (layer, table_role, schema_name, table_name, version);

    CREATE INDEX IF NOT EXISTS idx_ddlregistry_lookup
        ON {table_name} (layer, table_role, schema_name, table_name, version DESC);
    """


def _current_table_ref(layer: str, logical_table: str) -> str:
    layout = default_table_layout(layer, logical_table, "current")
    return f"{layout.schema_name}.{layout.table_name}"


def _history_table_ref(layer: str, logical_table: str) -> str:
    layout = default_table_layout(layer, logical_table, "history")
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


def _build_current_ddl(
    *,
    layer: str,
    logical_table: str,
    current_ddl_sql: str,
    partition_type: str,
    partition_column: str | None,
) -> str:
    current_ref = _current_table_ref(layer, logical_table)
    create_table = _parse_create_table_statement(current_ddl_sql)
    statement = _build_create_table_statement(
        table_ref=current_ref,
        body=str(create_table["body"]),
        partition_type=partition_type,
        partition_column=partition_column,
    )
    return _replace_create_table_statement(
        current_ddl_sql,
        statement=str(create_table["statement"]),
        replacement=statement,
    )


def _build_history_ddl(
    *,
    layer: str,
    logical_table: str,
    current_ddl_sql: str,
    partition_type: str,
    partition_column: str | None,
) -> str:
    current_ref = _current_table_ref(layer, logical_table)
    history_ref = _history_table_ref(layer, logical_table)
    create_table = _parse_create_table_statement(current_ddl_sql)
    body = str(create_table["body"])
    body = body.replace("record_id TEXT PRIMARY KEY,", "record_id TEXT NOT NULL,", 1)
    body = body.replace("record_id TEXT PRIMARY KEY\n", "record_id TEXT NOT NULL\n", 1)
    if body == create_table["body"]:
        raise ValueError(f"Unsupported current DDL for {current_ref}: missing record_id primary key")

    history_statement = _build_create_table_statement(
        table_ref=history_ref,
        body=f"    history_id BIGSERIAL,\n\n{body}",
        partition_type=partition_type,
        partition_column=partition_column,
    )
    ddl_sql = _replace_create_table_statement(
        current_ddl_sql,
        statement=str(create_table["statement"]),
        replacement=history_statement,
    )
    ddl_sql = ddl_sql.replace(f" ON {current_ref} ", f" ON {history_ref} ")

    table_name = default_table_layout(layer, logical_table, "history").table_name
    record_index = (
        f"CREATE INDEX IF NOT EXISTS idx_{table_name}_record_id "
        f"ON {history_ref} (record_id);\n"
    )
    if f"idx_{table_name}_record_id" not in ddl_sql:
        ddl_sql = f"{ddl_sql.rstrip()}\n{record_index}"

    if partition_type == "range" and partition_column:
        history_index_name = f"idx_{table_name}_{partition_column}"
        history_index_sql = (
            f"CREATE INDEX IF NOT EXISTS {history_index_name} "
            f"ON {history_ref} ({partition_column} DESC NULLS LAST);\n"
        )
        if history_index_name not in ddl_sql:
            ddl_sql = f"{ddl_sql.rstrip()}\n{history_index_sql}"

    return ddl_sql.rstrip()


async def _seed_current_registry_records(pg: PostgresClient) -> None:
    current_rows = await pg.fetch_all(
        f"""
        SELECT layer, table_name, ddl_sql, description
        FROM {_ddlregistry_table_name()}
        WHERE is_active = TRUE
          AND table_role = 'current'
          AND layer IN ('rds', 'ods')
        ORDER BY layer, table_name
        """
    )
    for row in current_rows:
        layer = row["layer"]
        logical_table = logical_table_name(layer, row["table_name"])
        layout = default_table_layout(layer, logical_table, "current")
        current_ddl_sql = _build_current_ddl(
            layer=layer,
            logical_table=logical_table,
            current_ddl_sql=row["ddl_sql"],
            partition_type=layout.partition_type,
            partition_column=layout.partition_column,
        )
        await save_registered_ddl(
            pg,
            layer=layer,
            table=logical_table,
            ddl_sql=current_ddl_sql,
            table_role="current",
            partition_type=layout.partition_type,
            partition_column=layout.partition_column,
            partition_granularity=layout.partition_granularity,
            partition_count=layout.partition_count,
            description=row.get("description") or f"Auto-managed current DDL for {layer}/{logical_table}",
            updated_by="system:current-bootstrap",
        )


async def _seed_history_registry_records(pg: PostgresClient) -> None:
    current_rows = await pg.fetch_all(
        f"""
        SELECT layer, table_name, ddl_sql
        FROM {_ddlregistry_table_name()}
        WHERE is_active = TRUE
          AND table_role = 'current'
          AND layer IN ('rds', 'ods')
        ORDER BY layer, table_name
        """
    )
    for row in current_rows:
        layer = row["layer"]
        logical_table = logical_table_name(layer, row["table_name"])
        history_layout = default_table_layout(layer, logical_table, "history")
        history_ddl_sql = _build_history_ddl(
            layer=layer,
            logical_table=logical_table,
            current_ddl_sql=row["ddl_sql"],
            partition_type=history_layout.partition_type,
            partition_column=history_layout.partition_column,
        )
        await save_registered_ddl(
            pg,
            layer=layer,
            table=logical_table,
            ddl_sql=history_ddl_sql,
            table_role="history",
            partition_type=history_layout.partition_type,
            partition_column=history_layout.partition_column,
            partition_granularity=history_layout.partition_granularity,
            partition_count=history_layout.partition_count,
            description=f"Auto-generated history DDL for {layer}/{logical_table}",
            updated_by="system:history-bootstrap",
        )


async def ensure_ddlregistry_table(pg: PostgresClient) -> None:
    global _DDLREGISTRY_READY
    if _DDLREGISTRY_READY:
        return

    table_name = _ddlregistry_table_name()
    await pg.init_schema([_ddlregistry_bootstrap_sql()])
    row = await pg.fetch_one(
        "SELECT to_regclass(:table_name) AS table_name",
        {"table_name": table_name},
    )
    if not row or not row.get("table_name"):
        raise RuntimeError(
            f"{table_name} is missing; create it in the ETL database before running ETL"
        )
    try:
        _DDLREGISTRY_READY = True
        await _seed_current_registry_records(pg)
        await _seed_history_registry_records(pg)
    except Exception:
        _DDLREGISTRY_READY = False
        raise


async def get_registered_ddl(
    pg: PostgresClient,
    layer: str,
    table: str,
    table_role: str | None = None,
) -> str | None:
    await ensure_ddlregistry_table(pg)
    schema_name, table_name, role = _table_identity(layer, table, table_role)
    row = await pg.fetch_one(
        f"""
        SELECT ddl_sql
        FROM {_ddlregistry_table_name()}
        WHERE layer = :layer
          AND schema_name = :schema_name
          AND table_name = :table_name
          AND table_role = :table_role
          AND is_active = TRUE
        ORDER BY version DESC
        LIMIT 1
        """,
        {
            "layer": layer,
            "schema_name": schema_name,
            "table_name": table_name,
            "table_role": role,
        },
    )
    return row["ddl_sql"] if row else None


async def get_registered_ddl_record(
    pg: PostgresClient,
    layer: str,
    table: str,
    table_role: str | None = None,
) -> dict[str, Any] | None:
    await ensure_ddlregistry_table(pg)
    schema_name, table_name, role = _table_identity(layer, table, table_role)
    return await pg.fetch_one(
        f"""
        SELECT
            id,
            layer,
            table_role,
            schema_name,
            table_name,
            ddl_sql,
            version,
            checksum,
            description,
            partition_type,
            partition_column,
            partition_granularity,
            partition_count,
            updated_by,
            created_at,
            updated_at
        FROM {_ddlregistry_table_name()}
        WHERE layer = :layer
          AND schema_name = :schema_name
          AND table_name = :table_name
          AND table_role = :table_role
          AND is_active = TRUE
        ORDER BY version DESC
        LIMIT 1
        """,
        {
            "layer": layer,
            "schema_name": schema_name,
            "table_name": table_name,
            "table_role": role,
        },
    )


async def list_registered_ddl_records(
    pg: PostgresClient,
    layer: str | None = None,
    table_role: str | None = None,
) -> list[dict[str, Any]]:
    await ensure_ddlregistry_table(pg)
    sql = f"""
        SELECT
            id,
            layer,
            table_role,
            schema_name,
            table_name,
            version,
            checksum,
            description,
            partition_type,
            partition_column,
            partition_granularity,
            partition_count,
            updated_by,
            created_at,
            updated_at
        FROM {_ddlregistry_table_name()}
        WHERE is_active = TRUE
    """
    params: dict[str, Any] = {}
    if layer:
        sql += " AND layer = :layer"
        params["layer"] = layer
    if table_role:
        sql += " AND table_role = :table_role"
        params["table_role"] = normalize_table_role(table_role)
    sql += " ORDER BY layer, schema_name, table_name"
    return await pg.fetch_all(sql, params)


async def save_registered_ddl(
    pg: PostgresClient,
    *,
    layer: str,
    table: str,
    ddl_sql: str,
    table_role: str | None = None,
    partition_type: str | None = None,
    partition_column: str | None = None,
    partition_granularity: str | None = None,
    partition_count: int | None = None,
    description: str | None = None,
    updated_by: str | None = None,
) -> dict[str, Any]:
    await ensure_ddlregistry_table(pg)

    schema_name, table_name, role = _table_identity(layer, table, table_role)
    defaults = default_table_layout(layer, table, role)
    params = {
        "layer": layer,
        "table_role": role,
        "schema_name": schema_name,
        "table_name": table_name,
    }
    effective_partition_type = partition_type or defaults.partition_type
    effective_partition_column: str | None = None
    effective_partition_granularity: str | None = None
    effective_partition_count: int | None = None

    if effective_partition_type == "hash":
        effective_partition_column = (
            partition_column if partition_column is not None else defaults.partition_column
        )
        effective_partition_count = (
            partition_count if partition_count is not None else defaults.partition_count
        )
    elif effective_partition_type == "range":
        effective_partition_column = (
            partition_column if partition_column is not None else defaults.partition_column
        )
        effective_partition_granularity = (
            partition_granularity
            if partition_granularity is not None
            else defaults.partition_granularity
        )

    checksum = _ddl_checksum(
        ddl_sql,
        table_role=role,
        partition_type=effective_partition_type,
        partition_column=effective_partition_column,
        partition_granularity=effective_partition_granularity,
        partition_count=effective_partition_count,
    )

    async with pg.locked_transaction(
        f"ddlregistry:{layer}:{role}:{schema_name}:{table_name}"
    ) as session:
        result = await session.execute(
            text(f"""
                SELECT id, version, checksum
                FROM {_ddlregistry_table_name()}
                WHERE layer = :layer
                  AND table_role = :table_role
                  AND schema_name = :schema_name
                  AND table_name = :table_name
                  AND is_active = TRUE
                ORDER BY version DESC
                LIMIT 1
            """),
            params,
        )
        current = result.mappings().first()

        if current and current["checksum"] == checksum:
            await session.execute(
                text(f"""
                    UPDATE {_ddlregistry_table_name()}
                    SET description = COALESCE(:description, description),
                        partition_type = :partition_type,
                        partition_column = :partition_column,
                        partition_granularity = :partition_granularity,
                        partition_count = :partition_count,
                        updated_by = COALESCE(:updated_by, updated_by),
                        updated_at = NOW()
                    WHERE id = :id
                """),
                {
                    "id": current["id"],
                    "description": description,
                    "partition_type": effective_partition_type,
                    "partition_column": effective_partition_column,
                    "partition_granularity": effective_partition_granularity,
                    "partition_count": effective_partition_count,
                    "updated_by": updated_by,
                },
            )
            return {
                "layer": layer,
                "table_role": role,
                "schema_name": schema_name,
                "table_name": table_name,
                "version": int(current["version"]),
                "changed": False,
            }

        next_version = int(current["version"]) + 1 if current else 1
        if current:
            await session.execute(
                text(
                    f"UPDATE {_ddlregistry_table_name()} "
                    "SET is_active = FALSE, updated_at = NOW() WHERE id = :id"
                ),
                {"id": current["id"]},
            )

        await session.execute(
            text(f"""
                INSERT INTO {_ddlregistry_table_name()} (
                    layer, table_role, schema_name, table_name,
                    ddl_sql, version, checksum,
                    partition_type, partition_column, partition_granularity, partition_count,
                    description, updated_by,
                    is_active, created_at, updated_at
                ) VALUES (
                    :layer, :table_role, :schema_name, :table_name,
                    :ddl_sql, :version, :checksum,
                    :partition_type, :partition_column, :partition_granularity, :partition_count,
                    :description, :updated_by,
                    TRUE, NOW(), NOW()
                )
            """),
            {
                **params,
                "ddl_sql": ddl_sql,
                "version": next_version,
                "checksum": checksum,
                "partition_type": effective_partition_type,
                "partition_column": effective_partition_column,
                "partition_granularity": effective_partition_granularity,
                "partition_count": effective_partition_count,
                "description": description,
                "updated_by": updated_by,
            },
        )

    return {
        "layer": layer,
        "table_role": role,
        "schema_name": schema_name,
        "table_name": table_name,
        "version": next_version,
        "changed": True,
    }
