"""ETL Pipeline API — 各层状态、表列表、数据查询、处理器管理。

Endpoints:
    GET  /api/etl/layers                    — ETL 各层状态
    GET  /api/etl/{layer}/tables            — 某层的表列表
    GET  /api/etl/{layer}/{table}/data      — 查询表数据
    POST /api/etl/query                     — 执行自定义 SQL
    GET  /api/etl/handlers/{layer}/{table}  — 获取处理器代码
    PUT  /api/etl/handlers/{layer}/{table}  — 保存处理器代码
    POST /api/etl/handlers/{layer}/{table}/validate — 校验处理器代码
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Known ETL layers ─────────────────────────────────────────────────────────

_KNOWN_LAYERS = [
    {"key": "rds", "label": "RDS 原始层", "icon": "DatabaseOutlined"},
    {"key": "ods", "label": "ODS 操作层", "icon": "FolderOutlined"},
    {"key": "task", "label": "TASK 任务层", "icon": "ScheduleOutlined"},
    {"key": "dwd", "label": "DWD 明细层", "icon": "TableOutlined"},
    {"key": "dws", "label": "DWS 汇总层", "icon": "BarChartOutlined"},
    {"key": "dim", "label": "DIM 维度层", "icon": "TagsOutlined"},
    {"key": "ads", "label": "ADS 应用层", "icon": "DashboardOutlined"},
]

# ── Helpers ──────────────────────────────────────────────────────────────────


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


async def _pg_available() -> bool:
    """检查 Postgres 是否可用。"""
    if not settings.pg_url or settings.pg_url == settings.__class__.model_fields["pg_url"].default:
        return False
    try:
        from app.storage.postgres_client import get_pg_client
        pg = get_pg_client()
        await pg.connect()
        return pg._connected
    except Exception:
        return False


async def _get_layer_tables(pg, layer: str) -> list[dict[str, Any]]:
    """查询某层的所有数据表及其行数/大小。"""
    rows = await pg.fetch_all("""
        SELECT
            t.tablename AS name,
            COALESCE(s.n_live_tup, 0)::bigint AS row_count,
            pg_size_pretty(pg_total_relation_size(quote_ident(t.schemaname) || '.' || quote_ident(t.tablename))) AS size,
            NOW()::text AS updated_at
        FROM pg_catalog.pg_tables t
        LEFT JOIN pg_stat_user_tables s
            ON s.schemaname = t.schemaname AND s.relname = t.tablename
        WHERE t.schemaname = $1
        ORDER BY t.tablename
    """, {"$1": layer})

    return [
        {
            "name": r["name"],
            "rowCount": r["row_count"],
            "size": r["size"] or "0 bytes",
            "updatedAt": r["updated_at"],
        }
        for r in rows
    ]


# ══════════════════════════════════════════════════════════════════════════════
#  Routes
# ══════════════════════════════════════════════════════════════════════════════


@router.get("/etl/layers")
async def get_layers() -> dict[str, Any]:
    """返回 ETL 各层状态：status / rate / lag / tables。"""
    pg_ok = await _pg_available()

    layers = []
    for layer_def in _KNOWN_LAYERS:
        layer_key = layer_def["key"]
        status: str = "stopped"
        table_count = 0

        if pg_ok:
            try:
                from app.storage.postgres_client import get_pg_client
                pg = get_pg_client()
                # 检查该层的 schema 是否存在
                schema_rows = await pg.fetch_all(
                    "SELECT count(*) AS cnt FROM pg_catalog.pg_tables WHERE schemaname = $1",
                    {"$1": layer_key},
                )
                count = schema_rows[0]["cnt"] if schema_rows else 0
                if count > 0:
                    status = "running"
                table_count = count
            except Exception:
                pass

        layers.append({
            "key": layer_key,
            "label": layer_def["label"],
            "icon": layer_def["icon"],
            "status": status,
            "rate": 0,  # 无法从 web 进程直接获取实时吞吐量
            "lag": 0,   # 无法从 web 进程直接获取消费 lag
            "tables": table_count,
        })

    return _ok(layers)


@router.get("/etl/{layer}/tables")
async def get_layer_tables_route(layer: str) -> dict[str, Any]:
    """返回某层的数据表列表。"""
    if not await _pg_available():
        return _ok([])

    # 验证 layer 名称
    valid_layers = {l["key"] for l in _KNOWN_LAYERS}
    if layer not in valid_layers:
        raise HTTPException(status_code=404, detail=f"Unknown layer: {layer}")

    try:
        from app.storage.postgres_client import get_pg_client
        pg = get_pg_client()
        tables = await _get_layer_tables(pg, layer)
        return _ok(tables)
    except Exception as e:
        logger.warning("获取 %s 层表列表失败: %s", layer, e)
        return _ok([])


@router.get("/etl/{layer}/{table}/data")
async def get_table_data(
    layer: str,
    table: str,
    limit: int = Query(default=50, ge=1, le=5000),
) -> dict[str, Any]:
    """查询某表的最近数据。"""
    if not await _pg_available():
        return _ok({"columns": [], "rows": [], "rowCount": 0, "elapsed": 0})

    try:
        from app.storage.postgres_client import get_pg_client
        pg = get_pg_client()

        t_start = datetime.now(timezone.utc)

        rows = await pg.fetch_all(
            f'SELECT * FROM "{layer}"."{table}" LIMIT $1',
            {"$1": limit},
        )

        elapsed = (datetime.now(timezone.utc) - t_start).total_seconds()
        columns = list(rows[0].keys()) if rows else []

        # 转换不可序列化的类型
        safe_rows = []
        for row in rows:
            safe_row: dict[str, Any] = {}
            for k, v in row.items():
                if isinstance(v, datetime):
                    safe_row[k] = v.isoformat()
                elif isinstance(v, bytes):
                    safe_row[k] = v.decode("utf-8", errors="replace")
                else:
                    try:
                        str(v)
                        safe_row[k] = v
                    except Exception:
                        safe_row[k] = str(v)
            safe_rows.append(safe_row)

        return _ok({
            "columns": columns,
            "rows": safe_rows,
            "rowCount": len(safe_rows),
            "elapsed": round(elapsed, 3),
        })
    except Exception as e:
        error_msg = f"查询 {layer}/{table} 失败: {e}"
        logger.warning(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


@router.post("/etl/query")
async def execute_query(body: dict[str, Any]) -> dict[str, Any]:
    """执行自定义 SQL 查询。"""
    sql = body.get("sql", "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL 不能为空")

    # 仅允许 SELECT
    sql_upper = sql.lstrip().upper()
    if not sql_upper.startswith("SELECT"):
        raise HTTPException(status_code=403, detail="仅允许 SELECT 查询")

    if not await _pg_available():
        return _ok({"columns": [], "rows": [], "rowCount": 0, "elapsed": 0})

    try:
        from app.storage.postgres_client import get_pg_client
        pg = get_pg_client()

        t_start = datetime.now(timezone.utc)
        rows = await pg.fetch_all(sql)
        elapsed = (datetime.now(timezone.utc) - t_start).total_seconds()
        columns = list(rows[0].keys()) if rows else []

        safe_rows = []
        for row in rows:
            safe_row: dict[str, Any] = {}
            for k, v in row.items():
                if isinstance(v, datetime):
                    safe_row[k] = v.isoformat()
                elif isinstance(v, bytes):
                    safe_row[k] = v.decode("utf-8", errors="replace")
                else:
                    safe_row[k] = v
            safe_rows.append(safe_row)

        return _ok({
            "columns": columns,
            "rows": safe_rows,
            "rowCount": len(safe_rows),
            "elapsed": round(elapsed, 3),
        })
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
#  Handlers (处理器代码)
# ══════════════════════════════════════════════════════════════════════════════

# 内存存储（生产环境应使用 DB）
_handler_store: dict[str, str] = {}


@router.get("/etl/handlers/{layer}/{table}")
async def get_handler_code(layer: str, table: str) -> dict[str, Any]:
    """获取处理器代码。"""
    key = f"{layer}/{table}"
    code = _handler_store.get(key, _default_handler_code(layer, table))
    return _ok({
        "layer": layer,
        "table": table,
        "code": code,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    })


@router.put("/etl/handlers/{layer}/{table}")
async def save_handler_code(layer: str, table: str, body: dict[str, Any]) -> dict[str, Any]:
    """保存处理器代码。"""
    code = body.get("code", "")
    key = f"{layer}/{table}"
    _handler_store[key] = code
    logger.info("Handler saved: %s (%d chars)", key, len(code))
    return _ok(None, f"处理器 {key} 已保存")


@router.post("/etl/handlers/{layer}/{table}/validate")
async def validate_handler_code(layer: str, table: str, body: dict[str, Any]) -> dict[str, Any]:
    """校验处理器代码语法。"""
    code = body.get("code", "")
    errors: list[str] = []

    # Python 语法检查
    try:
        compile(code, f"<handler:{layer}/{table}>", "exec")
    except SyntaxError as e:
        errors.append(f"Syntax error at line {e.lineno}: {e.msg}")
    except Exception as e:
        errors.append(f"Compile error: {e}")

    # 检查是否存在 handler 函数
    if "def handler" not in code and "def _handler" not in code:
        errors.append("Missing handler function definition")

    return _ok({
        "valid": len(errors) == 0,
        "errors": errors,
    })


def _default_handler_code(layer: str, table: str) -> str:
    return f'''"""
ETL Handler: {layer}/{table}
Layer: {layer}
Table: {table}
"""

from typing import Any


def handler(message: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
    """处理单条消息，返回转换后的记录或 None（过滤）。"""
    # TODO: 实现 {layer}/{table} 的处理逻辑
    return message
'''