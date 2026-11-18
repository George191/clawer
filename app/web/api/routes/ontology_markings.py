"""Manage project marking clearances without changing published model versions."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.web.api.dependencies import CurrentUser, SessionDep
from app.web.api.models.ontology_markings import (
    ChangeMarking,
    NewMarking,
    OntologyMarking,
    OntologyMarkingChange,
)
from app.web.services.ontology_registry_identity import eligible_principals, project_access_context
from app.web.services.ontology_revisions import rights_for

router = APIRouter(prefix="/projects/{project_id}/markings", tags=["ontology markings"])


async def manager(session, user, project_id):
    if not (await rights_for(session, project_id, user.id, lock=True)).manage:
        raise HTTPException(404, "Project not found")


async def grant_list(session, project_id, payload):
    requested = set(payload.readers)
    if requested != await eligible_principals(session, project_id, requested):
        raise HTTPException(422, "Select enabled principals from this project")
    return sorted(
        (p.model_dump(mode="json") for p in requested), key=lambda p: (p["kind"], p["id"])
    )


def view(row, manage):
    result = {"id": row.id, "name": row.name}
    if manage:
        result.update(revision=row.revision, readers=row.readers)
    return result


@router.get("")
async def list_markings(
    project_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100_000),
):
    context = await project_access_context(
        session, user, project_id, source_access=True, lock_membership=True
    )
    if not context.project_access:
        raise HTTPException(404, "Project not found")
    manage = (await rights_for(session, project_id, user.id)).manage
    query = select(OntologyMarking).where(OntologyMarking.project_id == project_id)
    if not manage:
        from sqlalchemy import or_

        query = query.where(
            or_(
                *(
                    OntologyMarking.readers.contains([p.model_dump(mode="json")])
                    for p in context.principals
                )
            )
        )
    rows = (
        await session.scalars(
            query.order_by(OntologyMarking.name, OntologyMarking.id).offset(offset).limit(limit + 1)
        )
    ).all()
    return {
        "items": [view(r, manage) for r in rows[:limit]],
        "hasMore": len(rows) > limit,
        "canManage": manage,
    }


@router.post("", status_code=201)
async def create_marking(
    project_id: UUID, payload: NewMarking, session: SessionDep, user: CurrentUser
):
    await manager(session, user, project_id)
    readers = await grant_list(session, project_id, payload)
    row = await session.scalar(
        insert(OntologyMarking)
        .values(project_id=project_id, name=payload.name, readers=readers, revision=1)
        .on_conflict_do_nothing(constraint="uq_ontology_marking_name")
        .returning(OntologyMarking)
    )
    if row is None:
        raise HTTPException(409, "This marking name is already in use")
    session.add(
        OntologyMarkingChange(marking_id=row.id, revision=1, actor_id=user.id, readers=readers)
    )
    result = view(row, True)
    await session.commit()
    return result


@router.put("/{marking_id}")
async def change_marking(
    project_id: UUID,
    marking_id: UUID,
    payload: ChangeMarking,
    session: SessionDep,
    user: CurrentUser,
):
    await manager(session, user, project_id)
    row = await session.scalar(
        select(OntologyMarking)
        .where(OntologyMarking.project_id == project_id, OntologyMarking.id == marking_id)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "Marking not found")
    if row.revision != payload.revision:
        raise HTTPException(409, "The marking changed. Refresh and review its current clearances.")
    readers = await grant_list(session, project_id, payload)
    if readers != row.readers:
        row.revision += 1
        row.readers = readers
        session.add(
            OntologyMarkingChange(
                marking_id=row.id, revision=row.revision, actor_id=user.id, readers=readers
            )
        )
    result = view(row, True)
    await session.commit()
    return result


@router.get("/{marking_id}/history")
async def marking_history(
    project_id: UUID,
    marking_id: UUID,
    session: SessionDep,
    user: CurrentUser,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100_000),
):
    await manager(session, user, project_id)
    change = OntologyMarkingChange
    if not await session.scalar(
        select(OntologyMarking.id).where(
            OntologyMarking.id == marking_id, OntologyMarking.project_id == project_id
        )
    ):
        raise HTTPException(404, "Marking not found")
    rows = (
        await session.scalars(
            select(change)
            .where(change.marking_id == marking_id)
            .order_by(change.revision.desc())
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    return {
        "items": [
            {
                "revision": r.revision,
                "actorId": r.actor_id,
                "createdAt": r.created_at,
                "readers": r.readers,
            }
            for r in rows[:limit]
        ],
        "hasMore": len(rows) > limit,
    }
