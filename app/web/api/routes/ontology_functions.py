"""Discover and invoke the caller's published business functions."""

from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import Field, JsonValue

from app.web.api.dependencies.common import CurrentUser
from app.web.api.dependencies.session import SessionDep
from app.web.services import ontology_functions as functions
from app.web.services import ontology_transactions as tx
from app.web.services.ontology_action_runtime import _available_actions, _definition_access

router = APIRouter(tags=["ontology functions"])


class Invocation(tx.RequestModel):
    requestId: UUID
    modelRevision: tx.Version
    recordId: UUID
    expectedRevision: tx.Version
    arguments: dict[str, JsonValue] = Field(max_length=48)


async def accessible(session, user, definition_id):
    row, definition, context, fields = await _definition_access(
        session, user, definition_id, lock_membership=True
    )
    allowed = functions.available_functions(
        definition, _available_actions(definition, context, fields)
    )
    return row, {prop.id: prop for prop in definition.properties}, allowed


@router.get("/object-types/{definition_id}/functions")
async def list_functions(definition_id: UUID, session: SessionDep, user: CurrentUser):
    row, properties, allowed = await accessible(session, user, definition_id)
    return {
        "revision": row.revision,
        "items": [functions.signature(f, properties) for f in allowed],
    }


@router.post(
    "/object-types/{definition_id}/functions/{key}/invoke", response_model=tx.TransactionReceipt
)
async def invoke(
    definition_id: UUID, key: str, body: Invocation, session: SessionDep, user: CurrentUser
):
    row, properties, allowed = await accessible(session, user, definition_id)
    function = next((f for f in allowed if f.key == key), None)
    if function is None:
        raise HTTPException(404, "Function not found")
    if body.modelRevision != row.revision:
        raise HTTPException(409, "The published function has changed. Reload its signature.")
    values = functions.evaluate(function, properties, body.arguments)
    step = tx.ExistingStep(
        kind="modify",
        definitionId=definition_id,
        recordId=body.recordId,
        expectedRevision=body.expectedRevision,
        values=values,
    )
    request = tx.TransactionRequest(requestId=body.requestId, name=function.name, steps=[step])
    identity = {"function": key, "revision": row.revision, "arguments": body.arguments}
    return await tx.commit_operation(
        session, tx.execute(session, user, row.project_id, request, invocation=identity)
    )
