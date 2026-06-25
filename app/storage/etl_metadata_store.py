from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import text

from app.etl.table_layout import (
    META_SCHEMA_NAME,
    RDS_CURRENT_HASH_PARTITION_COLUMN,
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
_RECORD_ID_INLINE_PK_RE = re.compile(
    r"(^\s*record_id\s+TEXT)\s+PRIMARY\s+KEY(\s*,?\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
_TABLE_PRIMARY_KEY_RE = re.compile(
    r"^\s*PRIMARY\s+KEY\s*\([^)]*\)\s*,?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_RDS_CURRENT_BASELINE_DDLS: dict[str, str] = {
    "news": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_news (
    record_id TEXT PRIMARY KEY,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rds_news_data_source ON ts_rds.rds_news (data_source);
CREATE INDEX IF NOT EXISTS idx_rds_news_created_at ON ts_rds.rds_news (created_at DESC);
""".strip(),
    "patent": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_patent (
    record_id TEXT PRIMARY KEY,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rds_patent_data_source ON ts_rds.rds_patent (data_source);
CREATE INDEX IF NOT EXISTS idx_rds_patent_created_at ON ts_rds.rds_patent (created_at DESC);
""".strip(),
    "navwarn": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_navwarn (
    record_id TEXT PRIMARY KEY,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rds_navwarn_data_source ON ts_rds.rds_navwarn (data_source);
CREATE INDEX IF NOT EXISTS idx_rds_navwarn_created_at ON ts_rds.rds_navwarn (created_at DESC);
""".strip(),
    "intelligence": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_intelligence (
    record_id TEXT PRIMARY KEY,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rds_intelligence_data_source ON ts_rds.rds_intelligence (data_source);
CREATE INDEX IF NOT EXISTS idx_rds_intelligence_created_at ON ts_rds.rds_intelligence (created_at DESC);
""".strip(),
}

_ODS_CURRENT_BASELINE_DDLS: dict[str, str] = {
    "news": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_news (
    record_id TEXT PRIMARY KEY,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    title TEXT,
    url TEXT,
    source_url TEXT,
    source_published_at TIMESTAMPTZ,
    source_updated_at TIMESTAMPTZ,
    summary TEXT,
    content TEXT,
    content_html TEXT,
    summary_html TEXT,
    author TEXT,
    organization JSONB,
    tags JSONB,
    external_links JSONB,
    attachments JSONB,
    images JSONB,
    slides JSONB,
    thumbnail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ods_news_data_source ON ts_ods.ods_news (data_source);
CREATE INDEX IF NOT EXISTS idx_ods_news_published_at ON ts_ods.ods_news (source_published_at DESC);
CREATE INDEX IF NOT EXISTS idx_ods_news_updated_at ON ts_ods.ods_news (updated_at DESC);
""".strip(),
    "patent": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_patent (
    record_id TEXT PRIMARY KEY,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    title TEXT,
    publication_number TEXT,
    application_number TEXT,
    assignee TEXT,
    inventor TEXT,
    publication_date DATE,
    filing_date DATE,
    priority_date DATE,
    grant_date DATE,
    abstract TEXT,
    claims JSONB,
    legal_status TEXT,
    ipc_classification TEXT,
    cpc_classification TEXT,
    patent_type TEXT,
    url TEXT,
    thumbnail TEXT,
    figures JSONB,
    quality_score DOUBLE PRECISION,
    quality_flags JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ods_patent_data_source ON ts_ods.ods_patent (data_source);
CREATE INDEX IF NOT EXISTS idx_ods_patent_publication_date ON ts_ods.ods_patent (publication_date DESC);
CREATE INDEX IF NOT EXISTS idx_ods_patent_updated_at ON ts_ods.ods_patent (updated_at DESC);
""".strip(),
    "navwarn": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_navwarn (
    record_id TEXT PRIMARY KEY,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    navarea_id INTEGER,
    warning_no TEXT,
    serial_number INTEGER,
    warning_year INTEGER,
    region TEXT,
    issued_at TIMESTAMPTZ,
    message_text TEXT,
    hazard_type TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    coordinate_count INTEGER,
    quality_score DOUBLE PRECISION,
    quality_flags JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ods_navwarn_data_source ON ts_ods.ods_navwarn (data_source);
CREATE INDEX IF NOT EXISTS idx_ods_navwarn_issued_at ON ts_ods.ods_navwarn (issued_at DESC);
CREATE INDEX IF NOT EXISTS idx_ods_navwarn_updated_at ON ts_ods.ods_navwarn (updated_at DESC);
""".strip(),
    "intelligence": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_intelligence (
    record_id TEXT PRIMARY KEY,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    title TEXT,
    url TEXT,
    source_published_at TIMESTAMPTZ,
    source_updated_at TIMESTAMPTZ,
    summary TEXT,
    file_name TEXT,
    file_size TEXT,
    file_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ods_intelligence_data_source ON ts_ods.ods_intelligence (data_source);
CREATE INDEX IF NOT EXISTS idx_ods_intelligence_published_at ON ts_ods.ods_intelligence (source_published_at DESC);
CREATE INDEX IF NOT EXISTS idx_ods_intelligence_updated_at ON ts_ods.ods_intelligence (updated_at DESC);
""".strip(),
}


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

    CREATE UNIQUE INDEX IF NOT EXISTS uq_ddlregistry_version
        ON {table_name} (layer, table_role, schema_name, table_name, version);

    CREATE INDEX IF NOT EXISTS idx_ddlregistry_lookup
        ON {table_name} (layer, table_role, schema_name, table_name, version DESC);

    DELETE FROM {table_name} older
    USING {table_name} newer
    WHERE older.layer = newer.layer
      AND older.table_role = newer.table_role
      AND older.schema_name = newer.schema_name
      AND older.table_name = newer.table_name
      AND older.id < newer.id;

    DROP INDEX IF EXISTS uq_ddlregistry_active_table;

    CREATE UNIQUE INDEX IF NOT EXISTS uq_ddlregistry_table
        ON {table_name} (layer, table_role, schema_name, table_name);
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


def _rewrite_rds_current_primary_key(body: str) -> str:
    rewritten = _RECORD_ID_INLINE_PK_RE.sub(r"\1 NOT NULL\2", body, count=1)
    rewritten = _TABLE_PRIMARY_KEY_RE.sub("", rewritten)
    rewritten = _clean_table_body(rewritten)
    return _append_table_constraint(
        rewritten,
        "PRIMARY KEY (record_id, data_source, data_type)",
    )


def _baseline_current_ddls() -> dict[str, dict[str, str]]:
    return {
        "rds": _RDS_CURRENT_BASELINE_DDLS,
        "ods": _ODS_CURRENT_BASELINE_DDLS,
    }


def _rewrite_history_body(body: str) -> str:
    rewritten = _RECORD_ID_INLINE_PK_RE.sub(r"\1 NOT NULL\2", body, count=1)
    rewritten = _TABLE_PRIMARY_KEY_RE.sub("", rewritten)
    if rewritten == body:
        raise ValueError("Unsupported current DDL: missing record_id primary key")
    return _clean_table_body(rewritten)


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
    body = str(create_table["body"])
    if layer == "rds":
        body = _rewrite_rds_current_primary_key(body)
    statement = _build_create_table_statement(
        table_ref=current_ref,
        body=body,
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
    body = _rewrite_history_body(str(create_table["body"]))

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
    existing_rows = await pg.fetch_all(
        f"""
        SELECT layer, table_name, ddl_sql, description
        FROM {_ddlregistry_table_name()}
        WHERE table_role = 'current'
          AND layer IN ('rds', 'ods')
        ORDER BY layer, table_name
        """
    )
    current_rows: list[dict[str, Any]] = list(existing_rows)
    existing_keys = {
        (row["layer"], logical_table_name(row["layer"], row["table_name"]))
        for row in existing_rows
    }

    for layer, tables in _baseline_current_ddls().items():
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
        WHERE table_role = 'current'
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
