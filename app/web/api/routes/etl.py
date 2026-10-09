from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/etl", tags=["etl"])

_LAYERS = ["RDS", "ODS", "TASK", "DWD", "DWS", "ADS"]


@router.get("/layers")
async def layers() -> list[dict[str, Any]]:
    return [
        {"key": layer.lower(), "label": layer, "icon": "database", "status": "stopped",
         "rateIn": 0, "rateOut": 0, "lag": 0, "tables": 0}
        for layer in _LAYERS
    ]


@router.get("/{layer}/tables")
async def tables(layer: str, tableRole: str = "current") -> list[dict[str, Any]]:
    return []


@router.get("/{layer}/{table}/data")
async def table_data(layer: str, table: str, limit: int = 50, partition: str | None = None) -> dict[str, Any]:
    return {"columns": [], "rows": [], "rowCount": 0, "elapsed": 0}


@router.get("/{layer}/{table}/partitions")
async def partitions(layer: str, table: str) -> list[dict[str, str]]:
    return []


@router.get("/{layer}/{table}/stream")
async def stream(layer: str, table: str) -> dict[str, Any]:
    return {"available": False, "reason": "ETL stream metrics are not configured", "throughput": None}


@router.get("/{layer}/{table}/script")
async def script(layer: str, table: str) -> dict[str, Any]:
    return {"available": False, "reason": "No script registry configured", "code": ""}


@router.post("/query")
async def query(payload: dict[str, str]) -> dict[str, Any]:
    return {"columns": [], "rows": [], "rowCount": 0, "elapsed": 0}


@router.get("/handlers/{layer}/{table}")
async def handler(layer: str, table: str) -> dict[str, Any]:
    return {"layer": layer, "table": table, "code": "", "updatedAt": ""}


@router.put("/handlers/{layer}/{table}")
async def save_handler(layer: str, table: str, payload: dict[str, str]) -> dict[str, Any]:
    return {"layer": layer, "table": table, "code": payload.get("code", ""), "updatedAt": ""}


@router.post("/handlers/{layer}/{table}/validate")
async def validate_handler(layer: str, table: str, payload: dict[str, str]) -> dict[str, Any]:
    return {"valid": bool(payload.get("code", "").strip()), "errors": [] if payload.get("code", "").strip() else ["代码不能为空"]}
