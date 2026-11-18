"""Discover and manage semantic links and personal dynamic sets."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.web.api.dependencies import CurrentUser, SessionDep
from app.web.api.models.ontology_graph import LinkInput, LinkTypeInput, SavedSetInput, SetQuery
from app.web.services import ontology_graph as graph
from app.web.services import ontology_graph_sets as sets

router = APIRouter(prefix="/ontology/v1", tags=["ontology graph"])
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]


@router.post("/projects/{project_id}/link-types", status_code=201)
async def create_type(
    project_id: UUID, payload: LinkTypeInput, session: SessionDep, user: CurrentUser
):
    return await graph.create_type(session, user, project_id, payload)


@router.get("/projects/{project_id}/link-targets")
async def targets(
    project_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Limit = 25,
    offset: Offset = 0,
):
    return await graph.link_targets(session, user, project_id, q.strip(), limit, offset)


@router.get("/object-types/{definition_id}/link-types")
async def list_types(
    definition_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    limit: Limit = 25,
    offset: Offset = 0,
):
    return await graph.link_types(session, user, definition_id, limit, offset)


@router.post("/link-types/{link_type_id}/links", status_code=201)
async def link(link_type_id: UUID, payload: LinkInput, session: SessionDep, user: CurrentUser):
    id_ = await graph.mutate_edge(
        session, user, link_type_id, payload.sourceRecordId, payload.targetRecordId, add=True
    )
    await session.commit()
    return {"id": id_}


@router.delete("/link-types/{link_type_id}/links/{link_id}", status_code=204)
async def unlink(
    link_type_id: UUID,
    link_id: UUID,
    sourceRecordId: UUID,
    targetRecordId: UUID,
    session: SessionDep,
    user: CurrentUser,
):
    await graph.mutate_edge(
        session,
        user,
        link_type_id,
        sourceRecordId,
        targetRecordId,
        add=False,
        expected_link_id=link_id,
    )
    await session.commit()
    return Response(status_code=204)


@router.get("/link-types/{link_type_id}/records/{record_id}/neighbors")
async def neighbors(
    link_type_id: UUID,
    record_id: UUID,
    direction: Literal["outgoing", "incoming"],
    session: SessionDep,
    user: CurrentUser,
    limit: Limit = 25,
    offset: Offset = 0,
):
    return await graph.neighbors(session, user, link_type_id, record_id, direction, limit, offset)


@router.post("/object-types/{definition_id}/sets", status_code=201)
async def create_set(
    definition_id: UUID, payload: SavedSetInput, session: SessionDep, user: CurrentUser
):
    return await sets.create_set(session, user, definition_id, payload)


@router.get("/object-types/{definition_id}/sets")
async def list_sets(
    definition_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    limit: Limit = 25,
    offset: Offset = 0,
):
    return await sets.list_sets(session, user, definition_id, limit, offset)


@router.post("/object-types/{definition_id}/sets/preview")
async def preview(
    definition_id: UUID,
    payload: SetQuery,
    session: SessionDep,
    user: CurrentUser,
    limit: Limit = 25,
    offset: Offset = 0,
    groupOffset: Offset = 0,
):
    return await sets.evaluate(session, user, definition_id, payload, limit, offset, groupOffset)


@router.get("/object-types/{definition_id}/sets/{set_id}")
async def evaluate(
    definition_id: UUID,
    set_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    limit: Limit = 25,
    offset: Offset = 0,
    groupOffset: Offset = 0,
):
    row = await sets.saved(session, user, definition_id, set_id)
    query = SetQuery.model_validate(row.query)
    result = await sets.evaluate(session, user, definition_id, query, limit, offset, groupOffset)
    return {**result, "query": query, "name": row.name}


@router.delete("/object-types/{definition_id}/sets/{set_id}", status_code=204)
async def delete_set(definition_id: UUID, set_id: UUID, session: SessionDep, user: CurrentUser):
    await graph.access(session, user, definition_id)
    await session.delete(await sets.saved(session, user, definition_id, set_id))
    await session.commit()
    return Response(status_code=204)
