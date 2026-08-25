from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config.settings import settings
from app.etl.table_layout import normalize_table_role, schema_name_for
from app.logger import get_logger
from app.storage.etl_metadata_store import (
    get_registered_ddl_record,
    list_registered_ddl_records,
    save_registered_ddl,
)
from app.web.api.dependencies.common import CurrentActiveSuperuser

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

_LAYER_STREAMS = {
    "rds": ("etl_rds_consumer_group", "etl_raw_topic"),
    "ods": ("etl_ods_consumer_group", "etl_rds_topic"),
    "task": ("etl_task_consumer_group", "etl_ods_topic"),
    "dwd": ("etl_dwd_consumer_group", "etl_ods_topic"),
    "dws": ("etl_dws_consumer_group", "etl_dwd_topic"),
    "dim": ("etl_dim_consumer_group", "etl_ods_topic"),
}


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


async def _get_table_partitions(pg, schema_name: str, table: str) -> list[str]:
    rows = await pg.fetch_all(
        """
        SELECT child.relname AS name
        FROM pg_inherits i
        JOIN pg_class parent ON parent.oid = i.inhparent
        JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
        JOIN pg_class child ON child.oid = i.inhrelid
        WHERE parent_ns.nspname = :schema_name AND parent.relname = :table
        ORDER BY child.relname
        """,
        {"schema_name": schema_name, "table": table},
    )
    return [str(row["name"]) for row in rows]


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


@router.get("/etl/schemas")
async def get_etl_schemas() -> dict[str, Any]:
    """Catalog ETL schemas; ts_meta is governance metadata, not a data layer."""
    if not await _pg_available():
        return _ok([])
    try:
        from app.storage.postgres_client import get_pg_client

        rows = await get_pg_client().fetch_all(
            """
            SELECT n.nspname AS name, count(c.oid)::int AS table_count
            FROM pg_namespace n
            LEFT JOIN pg_class c ON c.relnamespace = n.oid AND c.relkind IN ('r', 'p')
            WHERE n.nspname = 'ts_meta' OR n.nspname ~ '^ts_(rds|ods|task|dwd|dws|dim|ads)(_hist)?$'
            GROUP BY n.nspname ORDER BY n.nspname
            """
        )
        return _ok([
            {"name": row["name"], "tableCount": row["table_count"], "kind": "metadata" if row["name"] == "ts_meta" else "data"}
            for row in rows
        ])
    except Exception as exc:
        logger.warning("Failed to list ETL schemas: %s", exc)
        return _ok([])


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
    partition: str | None = Query(default=None),
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
        schema_name = _schema_name(layer, table_role)
        target_table = table
        if partition:
            partition = _require_identifier(partition, "partition")
            partitions = await _get_table_partitions(pg, schema_name, table)
            if partition not in partitions:
                raise HTTPException(status_code=404, detail="Unknown table partition")
            target_table = partition
        rows = await pg.fetch_all(
            f'SELECT * FROM "{schema_name}"."{target_table}" LIMIT :limit',
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


@router.get("/etl/{layer}/{table}/partitions")
async def get_table_partitions(
    layer: str, table: str, table_role: str = Query(default="current", alias="tableRole"),
) -> dict[str, Any]:
    if not await _pg_available():
        return _ok([])
    layer, table = _require_layer(layer), _require_identifier(table, "table")
    try:
        from app.storage.postgres_client import get_pg_client

        partitions = await _get_table_partitions(get_pg_client(), _schema_name(layer, table_role), table)
        return _ok([{"name": name} for name in partitions])
    except Exception as exc:
        logger.warning("Failed to load partitions for %s/%s: %s", layer, table, exc)
        return _ok([])


@router.get("/etl/{layer}/{table}/stream")
async def get_table_stream_state(layer: str, table: str) -> dict[str, Any]:
    """Return persisted consumer position; no synthetic lag or throughput is reported."""
    layer, table = _require_layer(layer), _require_identifier(table, "table")
    group_setting, topic_setting = _LAYER_STREAMS.get(layer, (None, None))
    if not group_setting or not topic_setting:
        return _ok({"available": False, "reason": "This layer has no configured consumer stream."})
    consumer_group, topic = getattr(settings, group_setting, ""), getattr(settings, topic_setting, "")
    if not consumer_group or not topic:
        return _ok({"available": False, "reason": "Kafka consumer configuration is unavailable."})
    from app.etl.offset_manager import get_offset_manager

    offsets = await get_offset_manager().load_offsets(consumer_group, topic)
    return _ok({
        "available": True,
        "consumerGroup": consumer_group,
        "topic": topic,
        "offsets": [{"partition": partition, "offset": offset} for partition, offset in sorted(offsets.items())],
        "throughput": None,
        "throughputReason": "No windowed throughput snapshot is persisted by the ETL worker.",
        "appliesTo": f"{layer}/{table}",
    })


@router.put("/etl/{layer}/{table}/offset")
async def set_table_stream_offset(
    layer: str,
    table: str,
    body: dict[str, Any],
    current_user: CurrentActiveSuperuser,
) -> dict[str, Any]:
    """Set one Redis resume point; it is applied only after the worker restarts."""
    layer, table = _require_layer(layer), _require_identifier(table, "table")
    if body.get("confirmation") != "SET OFFSET":
        raise HTTPException(status_code=400, detail="confirmation must equal SET OFFSET")
    partition, offset = body.get("partition"), body.get("offset")
    if not isinstance(partition, int) or partition < 0 or not isinstance(offset, int) or offset < 0:
        raise HTTPException(status_code=400, detail="partition and offset must be non-negative integers")
    group_setting, topic_setting = _LAYER_STREAMS.get(layer, (None, None))
    consumer_group = getattr(settings, group_setting, "") if group_setting else ""
    topic = getattr(settings, topic_setting, "") if topic_setting else ""
    if not consumer_group or not topic:
        raise HTTPException(status_code=409, detail="Layer has no configured consumer stream")
    from app.etl.offset_manager import get_offset_manager

    if not await get_offset_manager().set_offset(consumer_group, topic, partition, offset):
        raise HTTPException(status_code=503, detail="ETL Redis is unavailable")
    logger.warning("ETL offset changed by user=%s: %s/%s %s/%s/%s -> %s", current_user.id, layer, table, consumer_group, topic, partition, offset)
    return _ok({"restartRequired": True, "consumerGroup": consumer_group, "topic": topic, "partition": partition, "offset": offset})


@router.get("/etl/{layer}/{table}/script")
async def get_layer_script(layer: str, table: str) -> dict[str, Any]:
    """Expose the deployed layer implementation for inspection, never as an editable draft."""
    layer, table = _require_layer(layer), _require_identifier(table, "table")
    source_path = Path(__file__).resolve().parents[2] / "etl" / f"ts_{layer}.py"
    if not source_path.is_file():
        return _ok({"available": False, "reason": "No layer implementation file exists.", "code": ""})
    try:
        return _ok({
            "available": True,
            "path": str(source_path),
            "language": "python",
            "code": source_path.read_text(encoding="utf-8"),
            "table": table,
        })
    except OSError as exc:
        logger.warning("Failed to read layer script for %s: %s", layer, exc)
        return _ok({"available": False, "reason": "Layer script cannot be read.", "code": ""})


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
