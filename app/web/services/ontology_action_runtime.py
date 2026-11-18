"""Read protected ontology instances and execute versioned, audited no-source actions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models import User
from app.web.api.models.ontology_registry import Definition
from app.web.api.models.ontology_runtime import OntologyActionEvent, OntologyRuntimeRecord
from app.web.services import ontology_registry
from app.web.services.ontology_registry_identity import project_access_context
from app.web.services.ontology_registry_policy import AccessContext, action_permission, visibility
from app.web.services.ontology_runtime_values import check_record_json, normalize_fields, record_key

ActionKind = Literal["create", "modify", "delete"]


class ActionPayload(BaseModel):
    """Values with a record and exact revision for modify or delete."""

    model_config = ConfigDict(extra="forbid")
    values: dict[str, JsonValue] = Field(default_factory=dict, max_length=200)
    recordId: UUID | None = None
    expectedRevision: int | None = Field(default=None, ge=1, le=2_147_483_646, strict=True)


class RuntimeAction(BaseModel):
    """An action currently available to the authenticated user."""

    kind: ActionKind
    fields: list[str]


class RuntimeRecord(BaseModel):
    """A projected record; sourced rows are never action targets."""

    id: UUID | None = None
    revision: int | None = None
    properties: dict[str, Any]
    title: Any = None


class RuntimePage(BaseModel):
    """A read-authorized, bounded page of ontology instances."""

    items: list[RuntimeRecord]
    actions: list[RuntimeAction]
    hasMore: bool


class ActionReceipt(BaseModel):
    """An audit-friendly acknowledgement without protected row values."""

    id: UUID
    revision: int
    deleted: bool


class RecordHistoryEvent(BaseModel):
    kind: ActionKind
    revision: int
    createdAt: datetime


class RecordHistoryPage(BaseModel):
    items: list[RecordHistoryEvent]
    hasMore: bool


def _bad(message: str) -> None:
    raise HTTPException(422, message)


def _project_record(
    definition: Definition,
    fields: list[str],
    values: dict[str, Any],
    record_id: UUID | None = None,
    revision: int | None = None,
) -> RuntimeRecord:
    visible = {field: values[field] for field in fields if field in values}
    return RuntimeRecord(
        id=record_id,
        revision=revision,
        properties=visible,
        title=visible.get(definition.titleKey),
    )


async def _definition_access(
    session: AsyncSession,
    user: User,
    definition_id: UUID,
    *,
    lock_membership: bool = False,
) -> tuple[Any, Definition, AccessContext, list[str]]:
    row = await ontology_registry.get_published(session, user, definition_id)
    definition = Definition.model_validate(row.published_definition)
    context = await project_access_context(
        session,
        user,
        row.project_id,
        source_access=definition.source.mode != "existing" or user.is_superuser,
        lock_membership=lock_membership,
    )
    visible, fields = visibility(
        definition.security, [prop.id for prop in definition.properties], context
    )
    if not visible:
        raise HTTPException(404, "Object type not found")
    return row, definition, context, fields


def _available_actions(
    definition: Definition,
    context: AccessContext,
    fields: list[str],
) -> list[RuntimeAction]:
    if definition.source.mode != "none":
        return []
    visible = set(fields)
    all_fields = {prop.id for prop in definition.properties}
    return [
        RuntimeAction(kind=action.kind, fields=action.fields)
        for action in definition.actions
        if action_permission(action, context, visible)
        and (action.kind != "delete" or all_fields.issubset(visible))
    ]


async def list_records(
    session: AsyncSession,
    user: User,
    definition_id: UUID,
    *,
    limit: int,
    offset: int,
) -> RuntimePage:
    """Check access first, then read only a bounded page and mask all projections."""
    row, definition, context, fields = await _definition_access(session, user, definition_id)
    if not fields:
        return RuntimePage(items=[], actions=[], hasMore=False)
    if definition.source.mode == "existing":
        source = await ontology_registry.read_bound_records(row, fields, limit, offset)
        return RuntimePage(
            items=[_project_record(definition, fields, item) for item in source["items"]],
            actions=[],
            hasMore=source["hasMore"],
        )
    records = (
        await session.scalars(
            select(OntologyRuntimeRecord)
            .where(
                OntologyRuntimeRecord.definition_id == definition_id,
                OntologyRuntimeRecord.deleted.is_(False),
            )
            .order_by(OntologyRuntimeRecord.id)
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    return RuntimePage(
        items=[
            _project_record(definition, fields, record.values, record.id, record.revision)
            for record in records[:limit]
        ],
        actions=_available_actions(definition, context, fields),
        hasMore=len(records) > limit,
    )


async def record_history(
    session: AsyncSession, user: User, definition_id: UUID, record_id: UUID, *, limit: int, offset: int
) -> RecordHistoryPage:
    """Read committed event metadata under the current object's access policy."""
    _, definition, _, _ = await _definition_access(session, user, definition_id)
    if definition.source.mode != "none" or await session.scalar(
        select(OntologyRuntimeRecord.id).where(
            OntologyRuntimeRecord.definition_id == definition_id, OntologyRuntimeRecord.id == record_id
        )
    ) is None:
        raise HTTPException(404, "Record not found")
    events = (await session.execute(
        select(OntologyActionEvent.kind, OntologyActionEvent.revision, OntologyActionEvent.occurred_at)
        .where(OntologyActionEvent.record_id == record_id)
        .order_by(OntologyActionEvent.revision.desc())
        .offset(offset).limit(limit + 1)
    )).all()
    return RecordHistoryPage(
        items=[RecordHistoryEvent(kind=kind, revision=revision, createdAt=when)
               for kind, revision, when in events[:limit]],
        hasMore=len(events) > limit,
    )


async def submit_action(
    session: AsyncSession,
    user: User,
    definition_id: UUID,
    kind: ActionKind,
    payload: ActionPayload,
) -> ActionReceipt:
    """Atomically write and audit a no-source action, preserving immutable business keys."""
    _, definition, context, visible = await _definition_access(
        session, user, definition_id, lock_membership=True
    )
    action = next(
        (item for item in _available_actions(definition, context, visible) if item.kind == kind),
        None,
    )
    if not action:
        raise HTTPException(403, "Action is not available")
    if payload.values.keys() - set(action.fields):
        _bad("Action contains fields it is not allowed to write")
    values = normalize_fields(definition, payload.values)
    try:
        if kind == "create":
            if payload.recordId is not None or payload.expectedRevision is not None:
                _bad("Create does not accept an existing record or revision")
            if any(values.get(prop.id) is None for prop in definition.properties if prop.required):
                _bad("Supply all required properties")
            record = OntologyRuntimeRecord(
                definition_id=definition_id,
                values=values,
                key_digest=record_key(definition, values),
            )
        else:
            if payload.recordId is None or payload.expectedRevision is None:
                _bad("Record and expected revision are required")
            if kind == "modify" and not values:
                _bad("Supply at least one value to modify")
            record = await session.scalar(
                select(OntologyRuntimeRecord)
                .where(
                    OntologyRuntimeRecord.definition_id == definition_id,
                    OntologyRuntimeRecord.id == payload.recordId,
                    OntologyRuntimeRecord.deleted.is_(False),
                )
                .with_for_update()
            )
            if record is None:
                raise HTTPException(404, "Record not found")
            if record.revision != payload.expectedRevision:
                raise HTTPException(409, "Record changed. Refresh, review and try again.")
            if kind == "modify":
                merged = {**record.values, **values}
                check_record_json(merged)
                record.values = merged
            else:
                record.deleted = True
            record.revision += 1
        session.add(record)
        await session.flush()
        session.add(
            OntologyActionEvent(
                record_id=record.id,
                actor_id=user.id,
                kind=kind,
                revision=record.revision,
            )
        )
        result = ActionReceipt(id=record.id, revision=record.revision, deleted=record.deleted)
        await session.commit()
        return result
    except IntegrityError as exc:
        await session.rollback()
        original = exc.orig
        sqlstate = getattr(original, "sqlstate", None)
        if kind == "create" and sqlstate == "23505" and (
            getattr(original.__cause__, "constraint_name", None) == "uq_ontology_runtime_record_key"
        ):
            raise HTTPException(409, "Record identity already exists") from exc
        raise
