from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app.etl.tasks.extractors.keyword_files import MAX_FILE_SIZE
from app.web.api.dependencies.common import get_current_active_superuser, get_current_user
from app.web.api.models.etl import (
    CodePayload,
    DataQuery,
    FavoriteImport,
    FavoriteMutation,
    KeywordVersion,
    OffsetUpdate,
    ObjectVisibility,
    PreviewRowsQuery,
    SqlQuery,
)
from app.web.api.models.data_connections import DataConnectionPayload
from app.web.services.data_connections import connector_types, get_data_connections
from app.web.services import etl_runtime
from app.web.services import vulnerability_keywords as keyword_files
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


@read.get("/object-types")
async def object_types(schema: str | None = None, search: str = Query("", max_length=1000),
                       group: str = "", limit: int = Query(100, ge=1, le=500),
                       offset: int = Query(0, ge=0, le=1_000_000),
                       ids: str | None = Query(None, max_length=26_000),
                       includeHidden: bool = False, current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    if includeHidden and not getattr(current_user, "is_superuser", False):
        raise HTTPException(403, "Only administrators can include hidden object types")
    if includeHidden:
        return await get_etl_layers().object_types(schema, search, group, limit, offset, ids, include_hidden=True)
    return await get_etl_layers().object_types(schema, search, group, limit, offset, ids)


@read.get("/object-types/favorites")
async def object_favorites(current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return await get_etl_workspace().object_favorites(str(current_user.id))


@read.post("/object-types/favorites/import")
async def import_object_favorites(payload: FavoriteImport,
                                  current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return await get_etl_workspace().import_object_favorites(payload.ids, str(current_user.id))


@read.get("/task-config/vulnerability-keywords")
async def vulnerability_keywords() -> dict[str, Any]:
    return await keyword_files.configuration()


@write.post("/task-config/vulnerability-keywords/versions")
async def upload_vulnerability_keywords(file: UploadFile = File(...)) -> dict[str, Any]:
    try:
        data = await file.read(MAX_FILE_SIZE + 1)
    finally:
        await file.close()
    return await keyword_files.upload(data)


@write.put("/task-config/vulnerability-keywords/current")
async def activate_vulnerability_keywords(payload: KeywordVersion) -> dict[str, Any]:
    return await keyword_files.activate(payload.version)


@read.get("/task-config/vulnerability-keywords/versions/{version}/file")
async def download_vulnerability_keywords(version: str) -> Response:
    data = await keyword_files.download(version)
    return Response(data, media_type="application/yaml", headers={
        "Content-Disposition": f'attachment; filename="satellite-vulnerability-keywords-{version}.yaml"',
    })


@read.get("/task-config/vulnerability-keywords/current/file")
@read.get("/task-config/vulnerability-keywords/example", include_in_schema=False)
async def download_current_vulnerability_keywords() -> Response:
    return Response(await keyword_files.download(), media_type="application/yaml", headers={
        "Content-Disposition": 'attachment; filename="satellite-vulnerability-keywords.yaml"',
    })


@read.get("/connections")
async def connections() -> list[dict[str, Any]]:
    return await get_etl_workspace().connections()


@write.get("/connector-types")
async def available_connector_types() -> list[dict[str, Any]]:
    return connector_types()


@write.get("/data-connections")
async def data_connections() -> list[dict[str, Any]]:
    return await get_data_connections().list()


@write.post("/data-connections/test")
async def test_data_connection(payload: DataConnectionPayload, connectionId: UUID | None = None) -> dict[str, Any]:
    return await get_data_connections().test(payload, str(connectionId) if connectionId else None)


@write.post("/data-connections")
async def create_data_connection(payload: DataConnectionPayload,
                                 current_user: Any = Depends(get_current_active_superuser)) -> dict[str, Any]:
    return await get_data_connections().save(payload, str(current_user.id))


@write.post("/data-connections/{connection_id}/test")
async def test_saved_data_connection(connection_id: UUID) -> dict[str, Any]:
    return await get_data_connections().test_saved(str(connection_id))


@write.put("/data-connections/{connection_id}")
async def update_data_connection(connection_id: UUID, payload: DataConnectionPayload,
                                 current_user: Any = Depends(get_current_active_superuser)) -> dict[str, Any]:
    return await get_data_connections().save(payload, str(current_user.id), str(connection_id))


@write.delete("/data-connections/{connection_id}")
async def delete_data_connection(connection_id: UUID) -> dict[str, Any]:
    return await get_data_connections().delete(str(connection_id))


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


@read.get("/{layer}/{table}/preview")
async def object_preview(layer: str, table: str, includeHidden: bool = False,
                         current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    if includeHidden and not getattr(current_user, "is_superuser", False):
        raise HTTPException(403, "Only administrators can include hidden object types")
    if includeHidden:
        return await get_etl_layers().preview(layer, table, include_hidden=True)
    return await get_etl_layers().preview(layer, table)


@read.get("/{layer}/{table}/columns")
async def table_columns(layer: str, table: str) -> list[dict[str, Any]]:
    return await get_etl_layers().columns(layer, table)


@read.get("/{layer}/{table}/evidence")
async def table_evidence(layer: str, table: str,
                         current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return await get_etl_layers().evidence(
        layer, table, include_hidden=bool(getattr(current_user, "is_superuser", False)),
    )


@write.put("/{layer}/{table}/visibility")
async def set_object_visibility(layer: str, table: str, payload: ObjectVisibility,
                                current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return await get_etl_workspace().set_object_visibility(layer, table, payload.visibility, str(current_user.id))


@read.put("/{layer}/{table}/favorite")
async def favorite_object(layer: str, table: str, payload: FavoriteMutation | None = None,
                           current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return await get_etl_workspace().set_object_favorite(layer, table, True, str(current_user.id))


@read.delete("/{layer}/{table}/favorite")
async def unfavorite_object(layer: str, table: str, payload: FavoriteMutation | None = None,
                             current_user: Any = Depends(get_current_user)) -> dict[str, Any]:
    return await get_etl_workspace().set_object_favorite(layer, table, False, str(current_user.id))


@read.post("/{layer}/{table}/data")
async def filtered_data(layer: str, table: str, payload: DataQuery) -> dict[str, Any]:
    return await get_etl_layers().data(layer, table, payload.model_dump())


@read.post("/{layer}/{table}/preview-rows")
async def table_preview_rows(layer: str, table: str, payload: PreviewRowsQuery) -> dict[str, Any]:
    return await get_etl_layers().data(layer, table, payload.model_dump(), preview_rows=True)


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
