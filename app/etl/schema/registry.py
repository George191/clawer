"""PostgreSQL persistence and bootstrap lifecycle for ETL DDL registration."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.etl.schema.baselines import CANONICAL_ODS_TABLES, baseline_current_ddls
from app.etl.schema.ddl import build_current_ddl, ddl_checksum
from app.etl.schema.layout import (
    default_table_layout,
    logical_table_name,
    normalize_table_role,
)
from app.storage.postgres_client import PostgresClient

META_SCHEMA_NAME = "ts_meta"
_DDLREGISTRY_READY = False


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
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT ddlregistry_table_role_check
            CHECK (table_role IN ('current', 'history')),
        CONSTRAINT ddlregistry_partition_type_check
            CHECK (partition_type IN ('none', 'range', 'hash')),
        CONSTRAINT ddlregistry_partition_granularity_check
        CHECK (
            partition_granularity IS NULL
            OR partition_granularity IN ('month', 'year')
        ),
        CONSTRAINT ddlregistry_partition_layout_check
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
        )
    );

    CREATE UNIQUE INDEX IF NOT EXISTS uq_ddlregistry_version
        ON {table_name} (layer, table_role, schema_name, table_name, version);

    CREATE INDEX IF NOT EXISTS idx_ddlregistry_lookup
        ON {table_name} (layer, table_role, schema_name, table_name, version DESC);

    CREATE UNIQUE INDEX IF NOT EXISTS uq_ddlregistry_table
        ON {table_name} (layer, table_role, schema_name, table_name);
    """


async def _ensure_postgis_extension(pg: PostgresClient) -> None:
    await pg.execute(
        """
        CREATE EXTENSION IF NOT EXISTS postgis;
        """
    )


async def _seed_current_registry_records(pg: PostgresClient) -> None:
    existing_rows = await pg.fetch_all(
        f"""
        SELECT layer, table_name, ddl_sql, description
        FROM {_ddlregistry_table_name()}
        WHERE table_role = 'current'
          AND layer IN ('rds', 'ods')
        ORDER BY layer, table_name
        """
    )
    # Seed canonical creation DDL for SEC and social tables. Existing schemas
    # are upgraded separately by maintenance scripts.
    current_rows: list[dict[str, Any]] = [
        row for row in existing_rows
        if not (
            row["layer"] == "ods"
            and logical_table_name(row["layer"], row["table_name"])
            in CANONICAL_ODS_TABLES
        )
    ]
    existing_keys = {
        (row["layer"], logical_table_name(row["layer"], row["table_name"]))
        for row in current_rows
    }

    for layer, tables in baseline_current_ddls().items():
        for logical_table, ddl_sql in tables.items():
            if (layer, logical_table) in existing_keys:
                continue
            current_rows.append(
                {
                    "layer": layer,
                    "table_name": default_table_layout(layer, logical_table, "current").table_name,
                    "ddl_sql": ddl_sql,
                    "description": f"Baseline current DDL for {layer}/{logical_table}",
                }
            )

    for row in current_rows:
        layer = row["layer"]
        logical_table = logical_table_name(layer, row["table_name"])
        layout = default_table_layout(layer, logical_table, "current")
        current_ddl_sql = build_current_ddl(
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


async def ensure_ddlregistry_table(pg: PostgresClient) -> None:
    global _DDLREGISTRY_READY
    if _DDLREGISTRY_READY:
        return

    table_name = _ddlregistry_table_name()
    await _ensure_postgis_extension(pg)
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
        WHERE 1 = 1
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

    checksum = ddl_checksum(
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
                LIMIT 1
            """),
            params,
        )
        current = result.mappings().first()
        next_version = int(current["version"]) + 1 if current else 1

        if current and current["checksum"] == checksum:
            await session.execute(
                text(f"""
                    UPDATE {_ddlregistry_table_name()}
                    SET ddl_sql = :ddl_sql,
                        checksum = :checksum,
                        description = COALESCE(:description, description),
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
                    "ddl_sql": ddl_sql,
                    "checksum": checksum,
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

        if current:
            await session.execute(
                text(f"""
                    UPDATE {_ddlregistry_table_name()}
                    SET ddl_sql = :ddl_sql,
                        version = :version,
                        checksum = :checksum,
                        partition_type = :partition_type,
                        partition_column = :partition_column,
                        partition_granularity = :partition_granularity,
                        partition_count = :partition_count,
                        description = COALESCE(:description, description),
                        updated_by = COALESCE(:updated_by, updated_by),
                        is_active = TRUE,
                        updated_at = NOW()
                    WHERE id = :id
                """),
                {
                    "id": current["id"],
                    "ddl_sql": ddl_sql,
                    "version": int(current["version"]) + 1,
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
                "version": int(current["version"]) + 1,
                "changed": True,
            }

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
        "version": 1,
        "changed": True,
    }
