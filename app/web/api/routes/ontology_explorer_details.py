"""Authorized media and object reads for the shared Explorer detail components."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from app.web.api.dependencies import CurrentUser, SessionDep
from app.web.api.models.ontology_explorer import Identity
from app.web.api.models.ontology_registry import Identifier
from app.web.services import ontology_explorer_details as details
from app.web.services.explorer_files import storage_configuration
from app.web.services.ontology_action_runtime import _definition_access

router = APIRouter()


class RecordRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: Identity = Field(min_length=1, max_length=16)


class FileRead(RecordRead):
    field: Annotated[Identifier, Field(min_length=1)]
    download: bool = True
    path: list[StrictStr | StrictInt] = Field(default_factory=list, max_length=12)


@router.get("/object-storage")
async def storage(definition_id: UUID, session: SessionDep, user: CurrentUser, response: Response):
    await _definition_access(session, user, definition_id)
    response.headers["Cache-Control"] = "private, no-store"
    return storage_configuration()


@router.post("/record")
async def read_record(definition_id: UUID, payload: RecordRead, session: SessionDep, user: CurrentUser, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    return await details.record(session, user, definition_id, payload.key)


@router.post("/record-file")
async def read_file(definition_id: UUID, payload: FileRead, session: SessionDep, user: CurrentUser, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    return await details.file(session, user, definition_id, payload.key, payload.field, download=payload.download, path=payload.path)


@router.post("/record-html")
async def read_html(definition_id: UUID, payload: FileRead, session: SessionDep, user: CurrentUser, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    return await details.html(session, user, definition_id, payload.key, payload.field)
