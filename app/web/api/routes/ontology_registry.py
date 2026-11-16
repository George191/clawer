"""Versioned workflow for staging and publishing semantic object types."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.web.api.dependencies import CurrentUser, SessionDep
from app.web.api.models import Team
from app.web.api.models.ontology_registry import (
    Definition,
    DefinitionIssue,
    OntologyDefinitionRow,
    Revision,
    UpdateDraft,
)
from app.web.services.ontology_registry import (
    assess_definition,
    check_revision,
    get_project,
    get_published,
    projects_statement,
    require_source_access,
    source_signature,
    visible_definition,
    warehouse_table,
)
from app.web.services.ontology_registry_identity import eligible_principals
from app.web.services.ontology_registry_policy import definition_principals

router = APIRouter(prefix="/api/ontology/v1", tags=["ontology-registry"])


def dump(model):
    return model.model_dump(mode="json", by_alias=True)


def draft_view(row: OntologyDefinitionRow):
    return {"id": row.id, "revision": row.revision, "definition": row.draft_definition}


@router.get("/projects")
async def list_projects(session: SessionDep, user: CurrentUser):
    result = await session.scalars(projects_statement(user, manage=True).order_by(Team.name, Team.id))
    return {"items": [{"id": team.id, "name": team.name, "tenantId": team.tenant_id} for team in result]}


async def owned_draft(session, user, draft_id: UUID, *, locked=False) -> OntologyDefinitionRow:
    accessible = projects_statement(user, manage=True).with_only_columns(Team.id)
    statement = select(OntologyDefinitionRow).where(
        OntologyDefinitionRow.id == draft_id,
        OntologyDefinitionRow.owner_id == user.id,
        OntologyDefinitionRow.project_id.in_(accessible),
        OntologyDefinitionRow.published_at.is_(None),
    )
    if locked:
        statement = statement.with_for_update()
    row = await session.scalar(statement)
    if row is None:
        raise HTTPException(404, "Draft not found")
    require_source_access(user, Definition.model_validate(row.draft_definition).source)
    return row


@router.get("/drafts")
async def list_drafts(session: SessionDep, user: CurrentUser, limit: int = Query(100, ge=1, le=200)):
    accessible = projects_statement(user, manage=True).with_only_columns(Team.id)
    result = await session.scalars(select(OntologyDefinitionRow).where(
        OntologyDefinitionRow.owner_id == user.id,
        OntologyDefinitionRow.project_id.in_(accessible),
        OntologyDefinitionRow.published_at.is_(None),
    ).order_by(OntologyDefinitionRow.updated_at.desc(), OntologyDefinitionRow.id).limit(limit))
    return {"items": [
        draft_view(row) for row in result
        if user.is_superuser or row.draft_definition["source"]["mode"] == "none"
    ]}


@router.post("/drafts", status_code=201)
async def new_draft(payload: Definition, session: SessionDep, user: CurrentUser):
    await get_project(session, user, payload.projectId, manage=True)
    require_source_access(user, payload.source)
    row = OntologyDefinitionRow(
        project_id=payload.projectId, owner_id=user.id, semantic_id=payload.id,
        draft_definition=dump(payload),
    )
    session.add(row)
    await session.flush()
    result = draft_view(row)
    await session.commit()
    return result


@router.get("/drafts/{draft_id}")
async def read_draft(draft_id: UUID, session: SessionDep, user: CurrentUser):
    return draft_view(await owned_draft(session, user, draft_id))


@router.put("/drafts/{draft_id}")
async def update_draft(draft_id: UUID, payload: UpdateDraft, session: SessionDep, user: CurrentUser):
    row = await owned_draft(session, user, draft_id, locked=True)
    check_revision(row, payload.revision)
    definition = payload.definition
    await get_project(session, user, definition.projectId, manage=True)
    require_source_access(user, definition.source)
    row.project_id = definition.projectId
    row.semantic_id = definition.id
    row.draft_definition = dump(definition)
    row.revision += 1
    row.updated_at = datetime.now(timezone.utc)
    view = draft_view(row)
    await session.commit()
    return view


@router.delete("/drafts/{draft_id}", status_code=204)
async def discard_draft(
    draft_id: UUID, session: SessionDep, user: CurrentUser, revision: int = Query(ge=1),
):
    row = await owned_draft(session, user, draft_id, locked=True)
    check_revision(row, revision)
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


async def evaluate(session, user, row):
    definition = Definition.model_validate(row.draft_definition)
    # Authorization is checked before asking the warehouse anything about the table.
    await get_project(session, user, row.project_id, manage=True)
    require_source_access(user, definition.source)
    source = await warehouse_table(definition.source)
    eligible = await eligible_principals(
        session, row.project_id, definition_principals(definition.actions, definition.security),
    )
    result = assess_definition(definition, source, eligible=eligible)
    conflicting = await session.scalar(select(OntologyDefinitionRow.id).where(
        OntologyDefinitionRow.project_id == row.project_id,
        OntologyDefinitionRow.semantic_id == definition.id,
        OntologyDefinitionRow.published_at.is_not(None),
    ))
    if conflicting:
        result.errors.append(DefinitionIssue(path="id", message="This project already has that object type id"))
    return definition, source, result


@router.post("/drafts/{draft_id}/validate")
async def validate_draft(draft_id: UUID, payload: Revision, session: SessionDep, user: CurrentUser):
    row = await owned_draft(session, user, draft_id)
    check_revision(row, payload.revision)
    _, _, result = await evaluate(session, user, row)
    return result


@router.post("/drafts/{draft_id}/publish")
async def publish_draft(draft_id: UUID, payload: Revision, session: SessionDep, user: CurrentUser):
    row = await owned_draft(session, user, draft_id, locked=True)
    check_revision(row, payload.revision)
    definition, source, result = await evaluate(session, user, row)
    if result.errors:
        raise HTTPException(422, detail=dump(result))
    row.published_definition = dump(definition)
    row.published_at = row.updated_at = datetime.now(timezone.utc)
    if source:
        row.source_oid = source.oid
        row.source_signature = source_signature(definition, source)
    row.revision += 1
    view = {"id": row.id, "revision": row.revision, "objectType": row.published_definition}
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # Other simultaneous publications of the same project key must not overwrite this row.
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise HTTPException(409, "This project already has that object type id") from exc
        raise
    return view


@router.get("/object-types")
async def published_types(
    session: SessionDep, user: CurrentUser,
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0, le=100_000),
):
    accessible = projects_statement(user).with_only_columns(Team.id)
    result = await session.scalars(select(OntologyDefinitionRow).where(
        OntologyDefinitionRow.project_id.in_(accessible),
        OntologyDefinitionRow.published_at.is_not(None),
    ).order_by(OntologyDefinitionRow.published_at.desc(), OntologyDefinitionRow.id).limit(limit).offset(offset))
    rows = result.all()
    items = []
    for row in rows:
        view = await visible_definition(session, user, row)
        if view:
            items.append({"id": row.id, "revision": row.revision, "definition": view})
    # Do not expose the count of policy-hidden resources. The caller can paginate explicitly.
    return {"items": items}


@router.get("/object-types/{definition_id}")
async def published_type(definition_id: UUID, session: SessionDep, user: CurrentUser):
    row = await get_published(session, user, definition_id)
    view = await visible_definition(session, user, row)
    if not view:
        raise HTTPException(404, "Object type not found")
    return {"id": row.id, "revision": row.revision, "definition": view}
