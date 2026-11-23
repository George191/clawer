"""Manage explicit Published read APIs; consumers authenticate with X-API-Key."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from sqlalchemy import select

from app.web.api.dependencies import CurrentUser, SessionDep
from app.web.api.models.ontology_publications import OntologyPublication, PublicationInput
from app.web.services import ontology_publications as service

router = APIRouter(prefix="/api/ontology/v1", tags=["Published APIs"])
public_router = APIRouter(tags=["Published APIs"])


@router.get("/object-types/{definition_id}/publications")
async def publications(definition_id: UUID, session: SessionDep, user: CurrentUser, response: Response):
    grant = await service._definition_access(session, user, definition_id)
    can_manage = (await service.rights_for(session, grant[0].project_id, user.id)).manage
    rows = (await session.scalars(select(OntologyPublication).where(
        OntologyPublication.definition_id == definition_id,
    ).order_by(OntologyPublication.id))).all() if can_manage else []
    response.headers["Cache-Control"] = "private, no-store"
    return {"items": [{**service.view(row), "modelChanged": row.revision != grant[0].revision} for row in rows], "canManage": can_manage}


@router.post("/object-types/{definition_id}/publications", status_code=201)
async def create(definition_id: UUID, payload: PublicationInput, session: SessionDep, user: CurrentUser):
    return await service.save(session, user, definition_id, payload)


@router.put("/object-types/{definition_id}/publications/{publication_id}")
async def update(definition_id: UUID, publication_id: UUID, payload: PublicationInput, session: SessionDep, user: CurrentUser):
    return await service.save(session, user, definition_id, payload, publication_id)


@router.delete("/object-types/{definition_id}/publications/{publication_id}", status_code=204)
async def remove(definition_id: UUID, publication_id: UUID, session: SessionDep, user: CurrentUser):
    await service.management_access(session, user, definition_id)
    row = await service.get_publication(session, definition_id, publication_id)
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


@router.post("/object-types/{definition_id}/publications/{publication_id}/rotate-key")
async def rotate_key(definition_id: UUID, publication_id: UUID, session: SessionDep, user: CurrentUser, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    return await service.change_key(session, user, definition_id, publication_id, False)


@router.delete("/object-types/{definition_id}/publications/{publication_id}/key")
async def revoke_key(definition_id: UUID, publication_id: UUID, session: SessionDep, user: CurrentUser, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    return await service.change_key(session, user, definition_id, publication_id, True)


@public_router.get(service.PUBLICATION_DATA_PATH)
async def data(publication_id: UUID, session: SessionDep, response: Response, request: Request,
               key: Annotated[str | None, Header(alias="X-API-Key", max_length=256)] = None,
               limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=service.MAX_PUBLICATION_OFFSET)):
    if key is None:
        raise HTTPException(401, "X-API-Key is required")
    if set(request.query_params) - {"limit", "offset"}:
        raise HTTPException(422, "Supported parameters are limit and offset; the publisher controls the saved range")
    response.headers["Cache-Control"] = "private, no-store"
    return await service.read(session, publication_id, key, limit, offset)
