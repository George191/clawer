"""Versioned workflow for staging and publishing semantic object types."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import String, func, or_, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.web.api.dependencies import CurrentUser, SessionDep
from app.web.api.models import Team, TeamUserJoin
from app.web.api.models.ontology_publications import OntologyPublication
from app.web.api.models.ontology_runtime import OntologyRuntimeRecord
from app.web.api.models.ontology_registry import (
    Definition,
    CatalogChange,
    DefinitionIssue,
    OntologyDefinitionRow,
    OntologyProjectProfile,
    ProjectCreate,
    Revision,
    UpdateDraft,
)
from app.web.api.utils.models import StatusTypes
from app.web.services.ontology_markings import allowed_for, covers, require_clearance
from app.web.services.ontology_registry import (
    assess_definition,
    catalog_definition,
    check_revision,
    get_project,
    projects_statement,
    published_catalog_statement,
    require_source_access,
    source_signature,
    warehouse_table,
)
from app.web.services.ontology_registry_identity import eligible_principals
from app.web.services.ontology_registry_policy import definition_principals
from app.web.services.ontology_revisions import revision_snapshot

router = APIRouter(prefix="/api/ontology/v1", tags=["ontology-registry"])


def dump(model):
    return model.model_dump(mode="json", by_alias=True)


def draft_view(row: OntologyDefinitionRow):
    return {"id": row.id, "revision": row.revision, "definition": row.draft_definition}


async def lock_upstream_connection(session, source):
    if source.mode != "existing" or source.upstreamConnectionId is None:
        return True
    # Hold the connection until the definition commits so a concurrent disconnect
    # either sees this reference or completes first and rejects this new binding.
    return await session.scalar(text("""SELECT id FROM app.helio_data_connections
        WHERE id=:id FOR SHARE"""), {"id": source.upstreamConnectionId}) is not None


@router.get("/projects")
async def list_projects(session: SessionDep, user: CurrentUser):
    member = select(TeamUserJoin.id).where(
        TeamUserJoin.team_id == Team.id, TeamUserJoin.user_id == user.id,
        TeamUserJoin.status == StatusTypes.ENABLE,
    ).correlate(Team).exists()
    rows = await session.execute(
        projects_statement(user, manage=True).add_columns(OntologyProjectProfile, member)
        .outerjoin(OntologyProjectProfile, OntologyProjectProfile.team_id == Team.id)
        .order_by(Team.name, Team.id)
    )
    return {"items": [
        {"id": team.id, "name": profile.name if profile else team.name, "tenantId": team.tenant_id,
         "teamName": team.name, "ownerId": profile.owner_id if profile else None,
         "canName": profile is None and joined}
        for team, profile, joined in rows
    ]}


@router.post("/projects", status_code=201)
async def create_project(payload: ProjectCreate, session: SessionDep, user: CurrentUser):
    """Give the team's ontology project a display name and record its accountable owner."""
    scope = projects_statement(user, manage=True)
    if user.is_superuser:
        scope = scope.join(TeamUserJoin, TeamUserJoin.team_id == Team.id).where(
            TeamUserJoin.user_id == user.id, TeamUserJoin.status == StatusTypes.ENABLE,
        )
    team = await session.scalar(scope.where(Team.id == payload.teamId).with_for_update(read=True))
    if team is None:
        raise HTTPException(404, "Team not found or not accessible")
    project_id = await session.scalar(
        insert(OntologyProjectProfile).values(team_id=team.id, name=payload.name, owner_id=user.id)
        .on_conflict_do_nothing(index_elements=[OntologyProjectProfile.team_id])
        .returning(OntologyProjectProfile.team_id)
    )
    if project_id is None:
        raise HTTPException(409, "This team already has a named ontology project")
    view = {"id": team.id, "name": payload.name, "tenantId": team.tenant_id,
            "teamName": team.name, "ownerId": user.id, "canName": False}
    await session.commit()
    return view


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
    definition = Definition.model_validate(row.draft_definition)
    require_source_access(user, definition.source)
    await require_clearance(session, user, row.project_id, definition.security.markings)
    return row


@router.get("/drafts")
async def list_drafts(session: SessionDep, user: CurrentUser, limit: int = Query(100, ge=1, le=200),
                      offset: int = Query(0, ge=0), q: str = Query("", max_length=200)):
    accessible = projects_statement(user, manage=True).with_only_columns(Team.id)
    row = OntologyDefinitionRow
    statement = select(row).where(
        covers(row.draft_definition["security"]["markings"], allowed_for(user.id, row.project_id)),
        row.owner_id == user.id, row.project_id.in_(accessible), row.published_at.is_(None),
    )
    if not user.is_superuser:
        statement = statement.where(row.draft_definition["source"]["mode"].astext == "none")
    if query := q.strip():
        statement = statement.where(or_(row.draft_definition["name"].astext.icontains(query, autoescape=True),
                                        row.semantic_id.icontains(query, autoescape=True)))
    items = (await session.scalars(statement.order_by(row.updated_at.desc(), row.id).offset(offset).limit(limit + 1))).all()
    return {"items": [draft_view(item) for item in items[:limit]], "hasMore": len(items) > limit}


@router.post("/drafts", status_code=201)
async def new_draft(payload: Definition, session: SessionDep, user: CurrentUser):
    await get_project(session, user, payload.projectId, manage=True)
    await require_clearance(session, user, payload.projectId, payload.security.markings)
    require_source_access(user, payload.source)
    if not await lock_upstream_connection(session, payload.source):
        raise HTTPException(409, "The selected data connection no longer exists")
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
    await require_clearance(session, user, definition.projectId, definition.security.markings)
    require_source_access(user, definition.source)
    if not await lock_upstream_connection(session, definition.source):
        raise HTTPException(409, "The selected data connection no longer exists")
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
    if not await lock_upstream_connection(session, definition.source):
        result.errors.append(DefinitionIssue(path="source.upstreamConnectionId", message="The selected data connection no longer exists"))
    from app.web.services.ontology_record_policy import validate_record_policy_refs

    for message in await validate_record_policy_refs(session, row.id, definition):
        result.errors.append(DefinitionIssue(path="security.recordRules", message=message))
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


@router.get("/type-groups")
async def type_groups(session: SessionDep, user: CurrentUser, q: str = Query("", max_length=120)):
    """Suggest classification labels from published types visible to the caller."""
    labels = func.jsonb_array_elements_text(
        OntologyDefinitionRow.published_definition["groups"], type_=String
    ).label("name")
    visible = published_catalog_statement(user).order_by(None).with_only_columns(labels).subquery()
    names = select(visible.c.name).distinct()
    if q.strip():
        names = names.where(visible.c.name.icontains(q.strip(), autoescape=True))
    result = await session.scalars(names.order_by(visible.c.name).limit(50))
    return {"items": list(result)}


@router.get("/object-types")
async def published_types(
    session: SessionDep, user: CurrentUser,
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0, le=100_000),
    q: str = Query("", max_length=200),
    projectId: UUID | None = None, includeHidden: bool = False, includeDeleted: bool = False,
    source: Literal["warehouse", "object-managed"] | UUID | None = None,
):
    statement = published_catalog_statement(
        user, q.strip(), include_hidden=includeHidden, include_deleted=includeDeleted, source_filter=source,
    )
    if projectId is not None:
        statement = statement.where(OntologyDefinitionRow.project_id == projectId)
    result = await session.execute(statement.add_columns(func.count().over()).limit(limit).offset(offset))
    rows = result.all()
    total = rows[0][2] if rows else 0
    if offset and not rows:
        total = await session.scalar(statement.order_by(None).with_only_columns(func.count(OntologyDefinitionRow.id)))
    items = [{"id": row.id, "revision": row.revision, "hidden": row.hidden, "deleted": row.deleted,
              "definition": catalog_definition(user, row, tenant_id)} for row, tenant_id, _ in rows]
    return {"items": items, "hasMore": offset + len(items) < total, "total": total}


@router.patch("/object-types/{definition_id}/catalog-state")
async def change_catalog_state(definition_id: UUID, payload: CatalogChange, session: SessionDep, user: CurrentUser):
    model = OntologyDefinitionRow
    manageable = projects_statement(user, manage=True).with_only_columns(Team.id)
    statement = published_catalog_statement(user, include_hidden=True, include_deleted=True).where(
        model.id == definition_id, model.project_id.in_(manageable),
    ).with_for_update(of=model)
    item = (await session.execute(statement)).one_or_none()
    if item is None:
        raise HTTPException(404, "Object type not found or not manageable")
    row, _ = item
    check_revision(row, payload.revision)
    if row.deleted != (payload.operation == "restore"):
        raise HTTPException(409, "This operation does not apply to the current object type state.")
    if payload.operation in ("hide", "unhide"):
        row.hidden = payload.operation == "hide"
        # This catalog-only revision leaves previously reviewed API grants current.
        await session.execute(update(OntologyPublication).where(
            OntologyPublication.definition_id == row.id, OntologyPublication.revision == row.revision,
        ).values(revision=row.revision + 1))
    else:
        row.deleted = payload.operation == "delete"
        await session.execute(update(OntologyRuntimeRecord).where(
            OntologyRuntimeRecord.definition_id == row.id,
            OntologyRuntimeRecord.deleted != row.deleted,
        ).values(deleted=row.deleted, revision=OntologyRuntimeRecord.revision + 1))
    row.revision += 1
    row.updated_at = datetime.now(timezone.utc)
    session.add(revision_snapshot(row, user.id))
    result = {"id": row.id, "revision": row.revision, "hidden": row.hidden, "deleted": row.deleted}
    await session.commit()
    return result


@router.get("/object-types/{definition_id}")
async def published_type(definition_id: UUID, session: SessionDep, user: CurrentUser):
    result = await session.execute(published_catalog_statement(user, include_hidden=True).where(OntologyDefinitionRow.id == definition_id))
    item = result.one_or_none()
    if item is None:
        raise HTTPException(404, "Object type not found")
    row, tenant_id = item
    return {"id": row.id, "revision": row.revision, "hidden": row.hidden, "deleted": row.deleted,
            "definition": catalog_definition(user, row, tenant_id)}
