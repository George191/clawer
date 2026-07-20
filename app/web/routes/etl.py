from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.config.settings import settings
from app.etl.table_layout import normalize_table_role, schema_name_for
from app.logger import get_logger
from app.storage.etl_metadata_store import (
    get_registered_ddl_record,
    list_registered_ddl_records,
    save_registered_ddl,
)

logger = get_logger(__name__)

router = APIRouter()

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KNOWN_LAYERS = [
    {"key": "rds", "label": "RDS 原始层", "icon": "DatabaseOutlined"},
    {"key": "ods", "label": "ODS 操作层", "icon": "FolderOutlined"},
    {"key": "task", "label": "TASK 任务层", "icon": "ScheduleOutlined"},
    {"key": "dwd", "label": "DWD 明细层", "icon": "TableOutlined"},
    {"key": "dws", "label": "DWS 汇总层", "icon": "BarChartOutlined"},
    {"key": "dim", "label": "DIM 维度层", "icon": "TagsOutlined"},
    {"key": "ads", "label": "ADS 应用层", "icon": "DashboardOutlined"},
]

_handler_store: dict[str, str] = {}


def _ok(data: Any, message: str = "success") -> dict[str, Any]:
    return {
        "code": 0,
        "data": data,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _err(message: str, code: int = 400) -> dict[str, Any]:
    return {
        "code": code,
        "data": None,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _valid_layers() -> set[str]:
    return {layer["key"] for layer in _KNOWN_LAYERS}


def _require_layer(layer: str) -> str:
    if layer not in _valid_layers():
        raise HTTPException(status_code=404, detail=f"Unknown layer: {layer}")
    return layer


def _require_table_role(table_role: str | None) -> str:
    try:
        return normalize_table_role(table_role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _schema_name(layer: str, table_role: str | None = None) -> str:
    return schema_name_for(layer, _require_table_role(table_role))


def _require_identifier(name: str, label: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: {name}")
    return name


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    try:
        str(value)
        return value
    except Exception:
        return str(value)


def _serialize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _serialize_value(value) for key, value in row.items()} for row in rows]


def _serialize_ddl_row(row: dict[str, Any], include_ddl: bool = False) -> dict[str, Any]:
    layer = row["layer"]
    table_name = row["table_name"]
    prefix = f"{layer}_"
    logical_table = table_name[len(prefix):] if table_name.startswith(prefix) else table_name
    data = {
        "id": row["id"],
        "layer": layer,
        "tableRole": row.get("table_role", "current"),
        "schemaName": row["schema_name"],
        "table": logical_table,
        "tableName": table_name,
        "version": row["version"],
        "checksum": row["checksum"],
        "description": row.get("description"),
        "partitionType": row.get("partition_type", "none"),
        "partitionColumn": row.get("partition_column"),
        "partitionGranularity": row.get("partition_granularity"),
        "partitionCount": row.get("partition_count"),
        "updatedBy": row.get("updated_by"),
        "createdAt": _serialize_value(row.get("created_at")),
        "updatedAt": _serialize_value(row.get("updated_at")),
    }
    if include_ddl:
        data["ddlSql"] = row.get("ddl_sql", "")
    return data


async def _pg_available() -> bool:
    if not settings.pg_url or settings.pg_url == settings.__class__.model_fields["pg_url"].default:
        return False
    try:
        from app.storage.postgres_client import get_pg_client

        pg = get_pg_client()
        await pg.connect()
        return pg._connected
    except Exception:
        return False


async def _get_layer_tables(
    pg,
    layer: str,
    table_role: str = "current",
) -> list[dict[str, Any]]:
    schema_name = _schema_name(_require_layer(layer), table_role)
    rows = await pg.fetch_all(
        """
        SELECT
            c.relname AS name,
            n.nspname AS schema_name,
            CASE WHEN c.relkind = 'p' THEN TRUE ELSE FALSE END AS is_partitioned,
            COALESCE(s.n_live_tup, 0)::bigint AS row_count,
            pg_size_pretty(pg_total_relation_size(c.oid)) AS size,
            NOW()::text AS updated_at
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_user_tables s
            ON s.schemaname = n.nspname AND s.relname = c.relname
        LEFT JOIN pg_inherits i ON i.inhrelid = c.oid
        WHERE n.nspname = :schema_name
          AND c.relkind IN ('r', 'p')
          AND i.inhrelid IS NULL
        ORDER BY c.relname
        """,
        {"schema_name": schema_name},
    )
    return [
        {
            "name": row["name"],
            "schemaName": row["schema_name"],
            "tableRole": table_role,
            "partitioned": row["is_partitioned"],
            "rowCount": row["row_count"],
            "size": row["size"] or "0 bytes",
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]


@router.get("/etl/layers")
async def get_layers() -> dict[str, Any]:
    pg_ok = await _pg_available()
    layers = []

    for layer_def in _KNOWN_LAYERS:
        layer_key = layer_def["key"]
        status = "stopped"
        table_count = 0

        if pg_ok:
            try:
                from app.storage.postgres_client import get_pg_client

                pg = get_pg_client()
                rows = await pg.fetch_all(
                    """
                    SELECT count(*) AS cnt
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    LEFT JOIN pg_inherits i ON i.inhrelid = c.oid
                    WHERE n.nspname = :schema_name
                      AND c.relkind IN ('r', 'p')
                      AND i.inhrelid IS NULL
                    """,
                    {"schema_name": _schema_name(layer_key)},
                )
                table_count = rows[0]["cnt"] if rows else 0
                if table_count > 0:
                    status = "running"
            except Exception:
                logger.exception("Failed to load ETL layer status for %s", layer_key)

        layers.append(
            {
                "key": layer_key,
                "label": layer_def["label"],
                "icon": layer_def["icon"],
                "status": status,
                "rate": 0,
                "lag": 0,
                "tables": table_count,
            }
        )

    return _ok(layers)


@router.get("/etl/{layer}/tables")
async def get_layer_tables_route(
    layer: str,
    table_role: str = Query(default="current", alias="tableRole"),
) -> dict[str, Any]:
    if not await _pg_available():
        return _ok([])

    layer = _require_layer(layer)
    table_role = _require_table_role(table_role)

    try:
        from app.storage.postgres_client import get_pg_client

        pg = get_pg_client()
        tables = await _get_layer_tables(pg, layer, table_role)
        return _ok(tables)
    except Exception as e:
        logger.warning("Failed to load tables for layer %s: %s", layer, e)
        return _ok([])


@router.get("/etl/{layer}/{table}/data")
async def get_table_data(
    layer: str,
    table: str,
    table_role: str = Query(default="current", alias="tableRole"),
    limit: int = Query(default=50, ge=1, le=5000),
) -> dict[str, Any]:
    if not await _pg_available():
        return _ok({"columns": [], "rows": [], "rowCount": 0, "elapsed": 0})

    layer = _require_layer(layer)
    table = _require_identifier(table, "table")
    table_role = _require_table_role(table_role)

    try:
        from app.storage.postgres_client import get_pg_client

        pg = get_pg_client()
        started_at = datetime.now(timezone.utc)
        rows = await pg.fetch_all(
            f'SELECT * FROM "{_schema_name(layer, table_role)}"."{table}" LIMIT :limit',
            {"limit": limit},
        )
        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        safe_rows = _serialize_rows(rows)
        columns = list(safe_rows[0].keys()) if safe_rows else []
        return _ok(
            {
                "columns": columns,
                "rows": safe_rows,
                "rowCount": len(safe_rows),
                "elapsed": round(elapsed, 3),
            }
        )
    except Exception as e:
        error_msg = f"Query failed for {layer}/{table}: {e}"
        logger.warning(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


@router.post("/etl/query")
async def execute_query(body: dict[str, Any]) -> dict[str, Any]:
    sql = body.get("sql", "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL cannot be empty")

    if not sql.lstrip().upper().startswith("SELECT"):
        raise HTTPException(status_code=403, detail="Only SELECT queries are allowed")

    if not await _pg_available():
        return _ok({"columns": [], "rows": [], "rowCount": 0, "elapsed": 0})

    try:
        from app.storage.postgres_client import get_pg_client

        pg = get_pg_client()
        started_at = datetime.now(timezone.utc)
        rows = await pg.fetch_all(sql)
        elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        safe_rows = _serialize_rows(rows)
        columns = list(safe_rows[0].keys()) if safe_rows else []
        return _ok(
            {
                "columns": columns,
                "rows": safe_rows,
                "rowCount": len(safe_rows),
                "elapsed": round(elapsed, 3),
            }
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/etl/ddl")
async def list_ddl_registry(
    layer: str | None = Query(default=None),
    table_role: str | None = Query(default=None, alias="tableRole"),
) -> dict[str, Any]:
    if not await _pg_available():
        raise HTTPException(status_code=503, detail="Postgres is unavailable")

    layer_filter = _require_layer(layer) if layer else None
    role_filter = _require_table_role(table_role) if table_role else None

    try:
        from app.storage.postgres_client import get_pg_client

        pg = get_pg_client()
        rows = await list_registered_ddl_records(pg, layer_filter, role_filter)
        return _ok([_serialize_ddl_row(row) for row in rows])
    except Exception as e:
        logger.warning("Failed to list ddl registry records: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/etl/ddl/{layer}/{table}")
async def get_ddl_registry_detail(
    layer: str,
    table: str,
    table_role: str = Query(default="current", alias="tableRole"),
) -> dict[str, Any]:
    if not await _pg_available():
        raise HTTPException(status_code=503, detail="Postgres is unavailable")

    layer = _require_layer(layer)
    table = _require_identifier(table, "table")
    table_role = _require_table_role(table_role)

    try:
        from app.storage.postgres_client import get_pg_client

        pg = get_pg_client()
        row = await get_registered_ddl_record(pg, layer, table, table_role)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"DDL not found for {layer}/{table} ({table_role})",
            )
        return _ok(_serialize_ddl_row(row, include_ddl=True))
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Failed to load ddl registry record for %s/%s: %s", layer, table, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/etl/ddl/{layer}/{table}")
async def save_ddl_registry_detail(layer: str, table: str, body: dict[str, Any]) -> dict[str, Any]:
    if not await _pg_available():
        raise HTTPException(status_code=503, detail="Postgres is unavailable")

    layer = _require_layer(layer)
    table = _require_identifier(table, "table")
    table_role = _require_table_role(body.get("tableRole") or body.get("table_role"))
    ddl_sql = str(body.get("ddlSql") or body.get("ddl_sql") or "").strip()
    if not ddl_sql:
        raise HTTPException(status_code=400, detail="ddlSql is required")

    try:
        from app.storage.postgres_client import get_pg_client

        pg = get_pg_client()
        partition_count = body.get("partitionCount")
        if partition_count is None:
            partition_count = body.get("partition_count")
        save_result = await save_registered_ddl(
            pg,
            layer=layer,
            table=table,
            ddl_sql=ddl_sql,
            table_role=table_role,
            partition_type=body.get("partitionType") or body.get("partition_type"),
            partition_column=body.get("partitionColumn") or body.get("partition_column"),
            partition_granularity=(
                body.get("partitionGranularity") or body.get("partition_granularity")
            ),
            partition_count=partition_count,
            description=body.get("description"),
            updated_by=body.get("updatedBy") or body.get("updated_by"),
        )
        row = await get_registered_ddl_record(pg, layer, table, table_role)
        if row is None:
            raise HTTPException(
                status_code=500,
                detail=f"Saved DDL but failed to reload {layer}/{table} ({table_role})",
            )

        payload = _serialize_ddl_row(row, include_ddl=True)
        payload["changed"] = save_result["changed"]
        return _ok(payload)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Failed to save ddl registry record for %s/%s: %s", layer, table, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/etl/handlers/{layer}/{table}")
async def get_handler_code(layer: str, table: str) -> dict[str, Any]:
    key = f"{layer}/{table}"
    code = _handler_store.get(key, _default_handler_code(layer, table))
    return _ok(
        {
            "layer": layer,
            "table": table,
            "code": code,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }
    )


@router.put("/etl/handlers/{layer}/{table}")
async def save_handler_code(layer: str, table: str, body: dict[str, Any]) -> dict[str, Any]:
    code = body.get("code", "")
    key = f"{layer}/{table}"
    _handler_store[key] = code
    logger.info("Handler saved: %s (%d chars)", key, len(code))
    return _ok(None, f"处理器 {key} 已保存")


@router.post("/etl/handlers/{layer}/{table}/validate")
async def validate_handler_code(layer: str, table: str, body: dict[str, Any]) -> dict[str, Any]:
    code = body.get("code", "")
    errors: list[str] = []

    try:
        compile(code, f"<handler:{layer}/{table}>", "exec")
    except SyntaxError as e:
        errors.append(f"Syntax error at line {e.lineno}: {e.msg}")
    except Exception as e:
        errors.append(f"Compile error: {e}")

    if "def handler" not in code and "def _handler" not in code:
        errors.append("Missing handler function definition")

    return _ok({"valid": len(errors) == 0, "errors": errors})


def _default_handler_code(layer: str, table: str) -> str:
    return f'''"""
ETL Handler: {layer}/{table}
Layer: {layer}
Table: {table}
"""

from typing import Any


def handler(message: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
    # TODO: implement {layer}/{table}
    return message
'''
