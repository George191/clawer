"""Commit and undo a set of authorized object and link edits in one database transaction."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from sqlalchemy import Uuid, cast, column, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models import User
from app.web.api.models.ontology_graph import OntologyLinkType
from app.web.api.models.ontology_registry import OntologyDefinitionRow
from app.web.api.models.ontology_runtime import (
    OntologyActionEvent,
    OntologyRuntimeRecord,
    OntologyTransaction,
)
from app.web.services.ontology_action_runtime import (
    ActionPayload,
    _available_actions,
    _definition_access,
    apply_action_step,
    raise_action_integrity_error,
)
from app.web.services.ontology_graph import mutate_edge
from app.web.services.ontology_markings import allowed_for, cleared, covers, require_clearance
from app.web.services.ontology_record_policy import authorize_record_values
from app.web.services.ontology_registry_identity import project_access_context
from app.web.services.ontology_runtime_values import check_record_json, normalize_fields

Alias = Annotated[str, Field(pattern=r"^@[a-zA-Z][a-zA-Z0-9_]{0,31}$")]
Version = Annotated[int, Field(strict=True, ge=1, le=2_147_483_646)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateStep(RequestModel):
    kind: Literal["create"]
    definitionId: UUID
    alias: Alias | None = None
    values: dict[str, JsonValue] = Field(max_length=200)


class ExistingStep(RequestModel):
    kind: Literal["modify", "delete"]
    definitionId: UUID
    recordId: UUID
    expectedRevision: Version
    values: dict[str, JsonValue] = Field(default_factory=dict, max_length=200)

    @model_validator(mode="after")
    def check_edit(self):
        if bool(self.values) != (self.kind == "modify"):
            raise ValueError("Modify requires changed fields. Delete takes no values.")
        return self


class LinkStep(RequestModel):
    kind: Literal["link", "unlink"]
    linkTypeId: UUID
    source: UUID | Alias
    target: UUID | Alias


Step = Annotated[CreateStep | ExistingStep | LinkStep, Field(discriminator="kind")]


class TransactionRequest(RequestModel):
    requestId: UUID
    name: str = Field(min_length=1, max_length=120, pattern=r"\S")
    steps: list[Step] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def check_references(self):
        aliases = set()
        edited = set()
        for step in self.steps:
            if isinstance(step, CreateStep) and step.alias:
                if step.alias in aliases:
                    raise ValueError("Created record references must be unique")
                aliases.add(step.alias)
            if isinstance(step, ExistingStep):
                if step.recordId in edited:
                    raise ValueError("Combine changes to the same record into one step")
                edited.add(step.recordId)
            if isinstance(step, LinkStep):
                if any(
                    isinstance(ref, str) and ref not in aliases
                    for ref in (step.source, step.target)
                ):
                    raise ValueError(
                        "Link references must name an earlier Create step or a record UUID"
                    )
        return self


class TransactionReceipt(BaseModel):
    id: UUID
    name: str
    stepCount: int
    createdAt: datetime
    undoneAt: datetime | None


def receipt(batch: OntologyTransaction) -> TransactionReceipt:
    return TransactionReceipt(
        id=batch.id,
        name=batch.name,
        stepCount=len(batch.steps),
        createdAt=batch.created_at,
        undoneAt=batch.undone_at,
    )


async def require_member(session, user, project_id, *, lock=False):
    context = await project_access_context(
        session, user, project_id, source_access=True, lock_membership=lock
    )
    if not context.project_access:
        raise HTTPException(404, "Project not found")


async def _require_project(session, model, identifier, project_id):
    if not await session.scalar(
        select(model.id).where(model.id == identifier, model.project_id == project_id)
    ):
        raise HTTPException(404, "Project resource not found")


async def _event(session, record_id, revision):
    return await session.scalar(
        select(OntologyActionEvent).where(
            OntologyActionEvent.record_id == record_id, OntologyActionEvent.revision == revision
        )
    )


def touched_markings(steps, project_id):
    """Expand committed edit references, including both ends of each directed link."""
    row, link = OntologyDefinitionRow, OntologyLinkType
    edits = func.jsonb_array_elements(steps).table_valued(column("value", JSONB)).alias("tx_edit")
    value = edits.c.value
    return (select(row.published_definition["security"]["markings"]).select_from(edits)
            .outerjoin(link, link.id == cast(value["linkTypeId"].astext, Uuid))
            .join(row, or_(row.id == cast(value["definitionId"].astext, Uuid), row.id == link.source_definition_id, row.id == link.target_definition_id))
            .where(row.project_id == project_id).correlate(OntologyTransaction))


def visible_receipt(user, project_id):
    granted = allowed_for(user.id, project_id)
    current = OntologyDefinitionRow.published_definition["security"]["markings"]
    touched = touched_markings(OntologyTransaction.steps, project_id)
    return covers(OntologyTransaction.markings, granted) & ~touched.where(~covers(current, granted)).exists()


async def _execute_steps(session, user, project_id, batch_id, steps):
    aliases = {}
    edits = []
    edges = set()
    for step in steps:
        if isinstance(step, LinkStep):
            await _require_project(session, OntologyLinkType, step.linkTypeId, project_id)
            source = aliases[step.source] if isinstance(step.source, str) else step.source
            target = aliases[step.target] if isinstance(step.target, str) else step.target
            edge_key = (step.linkTypeId, source, target)
            if edge_key in edges:
                raise HTTPException(422, "A relationship can be edited once per transaction")
            edges.add(edge_key)
            edge_id = await mutate_edge(session, user, *edge_key, add=step.kind == "link")
            edits.append(
                dict(
                    kind=step.kind,
                    linkTypeId=str(step.linkTypeId),
                    source=str(source),
                    target=str(target),
                    edgeId=str(edge_id),
                )
            )
            continue
        await _require_project(session, OntologyDefinitionRow, step.definitionId, project_id)
        payload = (
            ActionPayload(values=step.values)
            if isinstance(step, CreateStep)
            else ActionPayload(
                recordId=step.recordId, expectedRevision=step.expectedRevision, values=step.values
            )
        )
        result = await apply_action_step(session, user, step.definitionId, step.kind, payload)
        event = await _event(session, result.id, result.revision)
        event.transaction_id = batch_id
        if isinstance(step, CreateStep) and step.alias:
            aliases[step.alias] = result.id
        edits.append(
            dict(
                kind=step.kind,
                definitionId=str(step.definitionId),
                recordId=str(result.id),
                revision=result.revision,
            )
        )
    return edits


async def execute(session: AsyncSession, user: User, project_id: UUID, request: TransactionRequest, *, invocation: dict | None = None):
    await require_member(session, user, project_id, lock=True)
    body = request.model_dump(mode="json", exclude={"requestId"})
    if invocation is not None:
        body["invocation"] = invocation
    check_record_json(body)
    signature = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    batch_id = await session.scalar(
        insert(OntologyTransaction)
        .values(
            id=uuid4(),
            project_id=project_id,
            actor_id=user.id,
            request_id=request.requestId,
            request_hash=signature,
            name=request.name.strip(),
            steps=[],
        )
        .on_conflict_do_nothing(constraint="uq_ontology_tx_request")
        .returning(OntologyTransaction.id)
    )
    if batch_id is None:
        previous = await session.scalar(
            select(OntologyTransaction).where(
                OntologyTransaction.project_id == project_id,
                OntologyTransaction.actor_id == user.id,
                OntologyTransaction.request_id == request.requestId,
                visible_receipt(user, project_id),
            )
        )
        if previous is None:
            raise HTTPException(404, "Transaction not found")
        if previous.request_hash != signature:
            raise HTTPException(409, "Request ID already belongs to a different transaction")
        return receipt(previous)
    batch = await session.get(OntologyTransaction, batch_id)
    batch.steps = await _execute_steps(session, user, project_id, batch.id, request.steps)
    labels = await session.scalars(touched_markings(literal(batch.steps, type_=JSONB), project_id))
    batch.markings = sorted({key for labels_for_type in labels for key in (labels_for_type or [])})
    return receipt(batch)


async def _undo_record(session, user, definition_id, record_id, revision, *, batch_id=None):
    _, definition, context, fields = await _definition_access(
        session, user, definition_id, lock_membership=True
    )
    record = await session.scalar(
        select(OntologyRuntimeRecord)
        .where(
            OntologyRuntimeRecord.id == record_id,
            OntologyRuntimeRecord.definition_id == definition_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if record is None or not await authorize_record_values(
        session, definition_id, definition, context, record_id, record.values
    ):
        raise HTTPException(404, "Record not found")
    event = await _event(session, record_id, revision)
    if event is None or event.actor_id != user.id or event.transaction_id != batch_id:
        raise HTTPException(404, "Reversible edit not found")
    if not await cleared(session, definition.projectId, event.markings, context):
        raise HTTPException(404, "Reversible edit not found")
    audit_markings = sorted(set(event.markings) | {str(key) for key in definition.security.markings})
    if event.changes is None or record.revision != revision or revision >= 2_147_483_647:
        raise HTTPException(
            409, "This edit has no reversible snapshot or is no longer the current revision"
        )
    if event.kind == "create":
        await apply_action_step(
            session,
            user,
            definition_id,
            "delete",
            ActionPayload(recordId=record_id, expectedRevision=revision),
        )
        inverse = await _event(session, record_id, revision + 1)
        inverse.transaction_id = batch_id
        inverse.markings = audit_markings
        return
    actions = {a.kind: set(a.fields) for a in _available_actions(definition, context, fields)}
    current = record.values
    if event.kind == "delete":
        if (
            not record.deleted
            or not {"create", "delete"} <= actions.keys()
            or current.keys() - (actions["create"] | actions.get("modify", set()))
        ):
            raise HTTPException(
                403,
                "Restoring this object requires current create and delete permissions for its values",
            )
        restored = dict(current)
        kind = "create"
    else:
        if record.deleted or "modify" not in actions or event.changes.keys() - actions["modify"]:
            raise HTTPException(403, "Current permissions do not allow restoring these fields")
        restored = dict(current)
        for field, delta in event.changes.items():
            if "before" in delta:
                restored[field] = delta["before"]
            else:
                restored.pop(field, None)
        kind = "modify"
    normalize_fields(definition, restored)
    if any(p.required and restored.get(p.id) is None for p in definition.properties):
        raise HTTPException(409, "Restored values do not satisfy the current required properties")
    if not await authorize_record_values(
        session, definition_id, definition, context, record_id, restored
    ):
        raise HTTPException(403, "Restored values are outside the current record policy")
    inverse_changes = {
        field: {("after" if key == "before" else "before"): value for key, value in delta.items()}
        for field, delta in event.changes.items()
    }
    record.values = restored
    record.deleted = False
    record.revision += 1
    session.add(
        OntologyActionEvent(
            record_id=record_id,
            actor_id=user.id,
            kind=kind,
            revision=record.revision,
            changes=inverse_changes,
            transaction_id=batch_id,
            restricted_history=event.restricted_history or bool(definition.security.recordRules),
            markings=audit_markings,
        )
    )
    await session.flush()


async def undo_single(session, user, definition_id, record_id, revision):
    await _undo_record(session, user, definition_id, record_id, revision)
    return {"id": record_id, "revision": revision + 1}


async def undo(session, user, project_id, batch_id):
    await require_member(session, user, project_id, lock=True)
    batch = await session.scalar(
        select(OntologyTransaction)
        .where(
            OntologyTransaction.id == batch_id,
            OntologyTransaction.project_id == project_id,
            OntologyTransaction.actor_id == user.id,
            visible_receipt(user, project_id),
        )
        .with_for_update()
    )
    if batch is None:
        raise HTTPException(404, "Transaction not found")
    await require_clearance(session, user, project_id, batch.markings)
    if batch.undone_at is None:
        for edit in reversed(batch.steps):
            if edit["kind"] in ("link", "unlink"):
                await mutate_edge(
                    session,
                    user,
                    UUID(edit["linkTypeId"]),
                    UUID(edit["source"]),
                    UUID(edit["target"]),
                    add=edit["kind"] == "unlink",
                    expected_link_id=UUID(edit["edgeId"]),
                )
            else:
                await _undo_record(
                    session,
                    user,
                    UUID(edit["definitionId"]),
                    UUID(edit["recordId"]),
                    edit["revision"],
                    batch_id=batch_id,
                )
        batch.undone_at = datetime.now(timezone.utc)
    return receipt(batch)


async def commit_operation(session, operation):
    try:
        result = await operation
        await session.commit()
        return result
    except IntegrityError as exc:
        await session.rollback()
        raise_action_integrity_error(exc)
    except DBAPIError as exc:
        await session.rollback()
        if getattr(exc.orig, "sqlstate", None) in ("40P01", "40001"):
            raise HTTPException(409, "Concurrent edit conflicted. Retry the same request.") from exc
        raise
    except Exception:
        await session.rollback()
        raise
