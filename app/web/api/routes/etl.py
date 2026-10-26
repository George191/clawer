from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.web.api.dependencies.common import get_current_active_superuser, get_current_user
from app.web.api.models.etl import CodePayload, DataQuery, OffsetUpdate, SqlQuery
from app.web.services import etl_runtime
from app.web.services.etl_layers import get_etl_layers
from app.web.services.etl_workspace import get_etl_workspace

router = APIRouter(prefix="/etl", tags=["etl"])
read = APIRouter(dependencies=[Depends(get_current_user)])
write = APIRouter(dependencies=[Depends(get_current_active_superuser)])


@router.get("/published/{token}")
async def published_data(token: str, limit: int = Query(50, ge=1, le=500),
                         offset: int = Query(0, ge=0, le=1_000_000)) -> dict[str, Any]:
    return await get_etl_workspace().published_data(token, {"limit": limit, "offset": offset})


@read.get("/layers")
async def layers() -> list[dict[str, Any]]:
    return await get_etl_layers().layers()


@read.get("/connections")
async def connections() -> list[dict[str, Any]]:
    return await get_etl_workspace().connections()


@write.post("/connections")
async def create_connection(payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().save_connection(payload)


@write.put("/connections/{connection_id}")
async def update_connection(connection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().save_connection(payload, connection_id)


@write.delete("/connections/{connection_id}")
async def delete_connection(connection_id: str) -> dict[str, Any]:
    return await get_etl_workspace().delete_connection(connection_id)


@write.put("/schemas/{schema}")
async def update_schema(schema: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().rename_schema(schema, payload)


@write.delete("/schemas/{schema}")
async def delete_schema(schema: str) -> dict[str, Any]:
    return await get_etl_workspace().delete_schema(schema)


@read.get("/{layer}/tables")
async def tables(layer: str, tableRole: str = "current") -> list[dict[str, Any]]:
    return await get_etl_layers().tables(layer, tableRole)


@write.post("/{layer}/tables")
async def create_table(layer: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().create_table(layer, payload)


@read.get("/{layer}/{table}/data")
async def table_data(layer: str, table: str, limit: int = Query(50, ge=1, le=500),
                     offset: int = Query(0, ge=0, le=1_000_000),
                     partition: str | None = None) -> dict[str, Any]:
    return await get_etl_layers().data(layer, table, {"limit": limit, "offset": offset, "partition": partition})


@read.post("/{layer}/{table}/data")
async def filtered_data(layer: str, table: str, payload: DataQuery) -> dict[str, Any]:
    return await get_etl_layers().data(layer, table, payload.model_dump())


@write.patch("/{layer}/{table}/rows")
async def update_row(layer: str, table: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().patch_row(layer, table, payload)


@write.delete("/{layer}/{table}/rows")
async def delete_row(layer: str, table: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().delete_row(layer, table, payload)


@read.get("/{layer}/{table}/partitions")
async def partitions(layer: str, table: str) -> list[dict[str, Any]]:
    return await get_etl_layers().partitions(layer, table)


@read.get("/{layer}/{table}/stream")
async def stream(layer: str, table: str, topic: str | None = None) -> dict[str, Any]:
    return await etl_runtime.stream(layer, table, topic)


@write.put("/{layer}/{table}/offset")
async def set_offset(layer: str, table: str, payload: OffsetUpdate) -> dict[str, Any]:
    return await etl_runtime.set_offset(layer, table, payload.model_dump())


@read.get("/{layer}/{table}/script")
async def script(layer: str, table: str) -> dict[str, Any]:
    return await etl_runtime.script(layer, table)


@write.post("/query")
async def query(payload: SqlQuery) -> dict[str, Any]:
    return await get_etl_layers().query(payload.sql)


@read.get("/handlers/{layer}/{table}")
async def handler(layer: str, table: str) -> dict[str, Any]:
    return await etl_runtime.read_handler(layer, table)


@write.put("/handlers/{layer}/{table}")
async def save_handler(layer: str, table: str, payload: CodePayload) -> dict[str, Any]:
    return await etl_runtime.save_handler(layer, table, payload.code)


@read.post("/handlers/{layer}/{table}/validate")
async def validate_handler(layer: str, table: str, payload: CodePayload) -> dict[str, Any]:
    await get_etl_layers().columns(layer, table)
    return etl_runtime.validate(payload.code)


@read.post("/{layer}/{table}/dry-run")
async def dry_run(layer: str, table: str, payload: CodePayload) -> dict[str, Any]:
    return await etl_runtime.dry_run(layer, table, payload.model_dump())


@write.get("/{layer}/{table}/apis")
async def publications(layer: str, table: str) -> list[dict[str, Any]]:
    return await get_etl_workspace().publications(layer, table)


@write.post("/{layer}/{table}/apis")
async def publish(layer: str, table: str, payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().save_publication(layer, table, payload)


@write.put("/{layer}/{table}/apis/{publication_id}")
async def update_publication(layer: str, table: str, publication_id: str,
                             payload: dict[str, Any]) -> dict[str, Any]:
    return await get_etl_workspace().save_publication(layer, table, payload, publication_id)


@write.delete("/{layer}/{table}/apis/{publication_id}")
async def revoke_publication(layer: str, table: str, publication_id: str) -> dict[str, Any]:
    return await get_etl_workspace().delete_publication(layer, table, publication_id)


router.include_router(read)
router.include_router(write)
