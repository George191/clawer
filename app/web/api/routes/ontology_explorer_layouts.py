"""Personal and project defaults keyed by Published definition, never by its source."""

from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.web.api.dependencies import CurrentUser, SessionDep
from app.web.api.models import Team
from app.web.api.models.ontology_explorer import OntologyExplorerResultLayout as LayoutRow
from app.web.api.routes.explorer_layouts import LayoutWrite, can_display, reconcile
from app.web.services.ontology_action_runtime import _definition_access
from app.web.services.ontology_registry import projects_statement

router = APIRouter()
SQL_TYPES = {"string": "text", "integer": "bigint", "number": "numeric", "boolean": "boolean", "date": "date", "datetime": "timestamp with time zone", "json": "jsonb"}


async def layout_access(session, user, definition_id):
    row, definition, _, visible = await _definition_access(session, user, definition_id)
    fields = {prop.id: SQL_TYPES[prop.type] for prop in definition.properties if prop.id in visible}
    manageable = await session.scalar(projects_statement(user, manage=True).with_only_columns(Team.id).where(Team.id == row.project_id))
    return fields, manageable is not None


@router.get("/result-layout")
async def read_layout(definition_id: UUID, session: SessionDep, user: CurrentUser, response: Response):
    fields, can_manage = await layout_access(session, user, definition_id)
    rows = await session.scalars(select(LayoutRow).where(LayoutRow.definition_id == definition_id, (LayoutRow.owner_id == user.id) | LayoutRow.owner_id.is_(None)))
    result = {scope: {"version": None, "layout": None} for scope in ("personal", "default")}
    for row in rows:
        layout = reconcile(row.layout, fields)
        if layout:
            layout["sorts"] = [sort for sort in layout["sorts"] if fields[sort["field"]] != "jsonb"]
        result["default" if row.owner_id is None else "personal"] = {"version": row.version, "layout": layout}
    response.headers["Cache-Control"] = "private, no-store"
    return {**result, "canManageDefault": can_manage}


@router.put("/result-layout/{scope}")
async def write_layout(definition_id: UUID, scope: Literal["personal", "default"], body: LayoutWrite, session: SessionDep, user: CurrentUser, response: Response):
    fields, can_manage = await layout_access(session, user, definition_id)
    if scope == "default" and not can_manage:
        raise HTTPException(403, "Only project managers can change the object type default")
    layout = body.layout
    if layout is not None:
        saved_fields = {col.field for col in layout.columns}
        if saved_fields != set(fields) or any(sort.field not in saved_fields for sort in layout.sorts):
            raise HTTPException(409, "Object type properties have changed. Reload the layout.")
        if any(not can_display(col.presentation, fields[col.field]) for col in layout.columns):
            raise HTTPException(422, "The selected presentation does not support this property type.")
        if any(fields[sort.field] == "jsonb" for sort in layout.sorts):
            raise HTTPException(422, "Sort by a scalar property")
    owner, version = None if scope == "default" else user.id, uuid4()
    values = {"version": version, "layout": layout.model_dump() if layout else None}
    if body.version is None:
        statement = insert(LayoutRow).values(id=uuid4(), definition_id=definition_id, owner_id=owner, **values).on_conflict_do_nothing(constraint="uq_ontology_explorer_layout_owner").returning(LayoutRow.id)
    else:
        statement = update(LayoutRow).where(LayoutRow.definition_id == definition_id, LayoutRow.owner_id.is_(owner) if owner is None else LayoutRow.owner_id == owner, LayoutRow.version == body.version).values(**values).returning(LayoutRow.id)
    if await session.scalar(statement) is None:
        raise HTTPException(409, "This layout has changed. Reload it before saving.")
    await session.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return values
