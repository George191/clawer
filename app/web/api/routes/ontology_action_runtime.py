"""Authenticated routes for ontology principals, policy preview, and object Actions."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.web.api.dependencies.common import CurrentUser
from app.web.api.dependencies.session import SessionDep
from app.web.api.models import User
from app.web.api.routes.ontology_markings import router as marking_router
from app.web.services.ontology_markings import cleared
from app.web.services import ontology_registry
from app.web.services.ontology_action_runtime import (
    ActionKind,
    ActionPayload,
    ActionReceipt,
    RecordHistoryPage,
    RuntimePage,
    RuntimeRecord,
    get_record,
    list_records,
    record_history,
    submit_action,
)
from app.web.services.ontology_registry_identity import (
    PrincipalOptions,
    eligible_principals,
    project_access_context,
    project_principals,
)
from app.web.services.ontology_registry_policy import (
    ObjectSecurity,
    definition_principals,
    visibility,
)

router = APIRouter(prefix="/ontology/v1", tags=["ontology object actions and security"])
router.include_router(marking_router)


class SecurityPreviewRequest(BaseModel):
    """Test a proposed policy for a project member without accessing record data."""

    model_config = ConfigDict(extra="forbid")
    projectId: UUID
    userId: UUID
    sourceMode: Literal["existing", "none"]
    properties: list[str] = Field(min_length=1, max_length=200)
    security: ObjectSecurity


class SecurityPreview(BaseModel):
    """Effective visibility decisions only, not record values or user credentials."""

    visible: bool
    visibleFields: list[str]


@router.get("/projects/{project_id}/principals", response_model=PrincipalOptions)
async def principals(
    session: SessionDep,
    current_user: CurrentUser,
    project_id: UUID,
    search: str = Query(default="", max_length=100),
    limit: int = Query(default=100, ge=1, le=200),
    user_id: list[UUID] | None = Query(default=None, max_length=100),
) -> PrincipalOptions:
    """List identities available to a verified project manager."""
    await ontology_registry.get_project(session, current_user, project_id, manage=True)
    items = await project_principals(session, project_id, search=search.strip(), limit=limit, user_ids=user_id)
    return PrincipalOptions(items=items)


@router.post("/security/preview", response_model=SecurityPreview)
async def preview_security(
    session: SessionDep,
    current_user: CurrentUser,
    payload: SecurityPreviewRequest,
) -> SecurityPreview:
    """Simulate the same read guard used by the runtime without changing any grants."""
    await ontology_registry.get_project(session, current_user, payload.projectId, manage=True)
    if payload.sourceMode == "existing" and not current_user.is_superuser:
        raise HTTPException(403, "Only administrators can manage sourced object security")
    required_mode = "inherit" if payload.sourceMode == "existing" else "explicit"
    if payload.security.mode != required_mode:
        raise HTTPException(422, "Security mode does not match the object source")
    if len(set(payload.properties)) != len(payload.properties) or not all(payload.properties):
        raise HTTPException(422, "Properties must be distinct identifiers")
    if set(payload.security.fieldReaders) - set(payload.properties):
        raise HTTPException(422, "Field security references an unknown property")
    requested = definition_principals([], payload.security)
    if await eligible_principals(session, payload.projectId, requested) != requested:
        raise HTTPException(422, "Policy references an unavailable project principal")
    subject = await session.get(User, payload.userId)
    if subject is None:
        return SecurityPreview(visible=False, visibleFields=[])
    context = await project_access_context(
        session,
        subject,
        payload.projectId,
        source_access=payload.sourceMode != "existing" or subject.is_superuser,
    )
    allowed, fields = visibility(payload.security, payload.properties, context)
    if allowed and not await cleared(session, payload.projectId, payload.security.markings, context):
        allowed, fields = False, []
    return SecurityPreview(visible=allowed, visibleFields=fields)


@router.get("/object-types/{definition_id}/records", response_model=RuntimePage)
async def records(
    session: SessionDep,
    current_user: CurrentUser,
    definition_id: UUID,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    deleted: bool = False,
) -> RuntimePage:
    """Return a bounded list of policy-filtered object instances."""
    return await list_records(session, current_user, definition_id, limit=limit, offset=offset, deleted=deleted)


@router.get("/object-types/{definition_id}/records/{record_id}/history", response_model=RecordHistoryPage)
async def history(
    session: SessionDep,
    current_user: CurrentUser,
    definition_id: UUID,
    record_id: UUID,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> RecordHistoryPage:
    return await record_history(session, current_user, definition_id, record_id, limit=limit, offset=offset)


@router.get("/object-types/{definition_id}/records/{record_id}", response_model=RuntimeRecord)
async def record(session: SessionDep, current_user: CurrentUser, definition_id: UUID, record_id: UUID) -> RuntimeRecord:
    return await get_record(session, current_user, definition_id, record_id)


@router.post("/object-types/{definition_id}/actions/{kind}", response_model=ActionReceipt)
async def apply_action(
    session: SessionDep,
    current_user: CurrentUser,
    definition_id: UUID,
    kind: ActionKind,
    payload: ActionPayload,
) -> ActionReceipt:
    """Apply a defined and authorized action to isolated business records."""
    return await submit_action(session, current_user, definition_id, kind, payload)
