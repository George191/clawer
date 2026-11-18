"""Atomic commit, idempotency and reversals against a real PostgreSQL runtime."""

import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.ontology_registry import OntologyDefinitionRow
from app.web.api.models.ontology_runtime import (
    OntologyActionEvent,
    OntologyRuntimeRecord,
    OntologyTransaction,
)
from app.web.services import ontology_transactions as tx
from app.web.services.ontology_action_runtime import ActionPayload, submit_action
from app.web.tests.test_ontology_action_runtime_integration import runtime


@pytest.mark.asyncio
async def test_cross_type_replay_and_reverse(runtime):
    engine, user, first, _ = runtime
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        row = await setup.get(OntologyDefinitionRow, first)
        project = row.project_id
        definition = deepcopy(row.published_definition)
        definition["id"] = "other"
        other = OntologyDefinitionRow(
            project_id=project,
            owner_id=user.id,
            semantic_id="other",
            draft_definition=definition,
            published_definition=definition,
            published_at=row.published_at,
        )
        setup.add(other)
        await setup.commit()
        second = other.id
    request = tx.TransactionRequest(
        requestId=uuid4(),
        name="Create both",
        steps=[
            {"kind": "create", "definitionId": first, "values": {"key": "A"}},
            {"kind": "create", "definitionId": second, "values": {"key": "B"}},
        ],
    )

    async def send():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await tx.commit_operation(session, tx.execute(session, user, project, request))

    receipts = await asyncio.gather(send(), send())
    assert receipts[0] == receipts[1] and receipts[0].stepCount == 2
    async with AsyncSession(engine, expire_on_commit=False) as session:
        assert await session.scalar(select(func.count()).select_from(OntologyActionEvent)) == 2
        altered = request.model_copy(update={"name": "Different"})
        with pytest.raises(HTTPException) as failure:
            await tx.commit_operation(session, tx.execute(session, user, project, altered))
        assert failure.value.status_code == 409
        restored = await tx.commit_operation(
            session, tx.undo(session, user, project, receipts[0].id)
        )
        assert restored.undoneAt is not None
        assert (
            await tx.commit_operation(session, tx.undo(session, user, project, restored.id))
        ) == restored
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OntologyRuntimeRecord)
                .where(OntologyRuntimeRecord.deleted.is_(False))
            )
            == 0
        )
        assert await session.scalar(select(func.count()).select_from(OntologyActionEvent)) == 4


@pytest.mark.asyncio
async def test_conflict_rolls_back_entire_batch_and_undo(runtime):
    engine, user, definition, _ = runtime
    async with AsyncSession(engine, expire_on_commit=False) as session:
        project = (await session.get(OntologyDefinitionRow, definition)).project_id
        a = await submit_action(
            session, user, definition, "create", ActionPayload(values={"key": "A"})
        )
        b = await submit_action(
            session, user, definition, "create", ActionPayload(values={"key": "B"})
        )
        steps = [
            {
                "kind": "modify",
                "definitionId": definition,
                "recordId": r.id,
                "expectedRevision": 1,
                "values": {"note": "new"},
            }
            for r in (a, b)
        ]
        bad = tx.TransactionRequest(
            requestId=uuid4(),
            name="Conflict",
            steps=[steps[0], {**steps[1], "expectedRevision": 2}],
        )
        with pytest.raises(HTTPException) as failure:
            await tx.commit_operation(session, tx.execute(session, user, project, bad))
        assert failure.value.status_code == 409
        assert (await session.get(OntologyRuntimeRecord, a.id)).revision == 1
        assert await session.scalar(select(func.count()).select_from(OntologyTransaction)) == 0
        good = tx.TransactionRequest(requestId=uuid4(), name="Both", steps=steps)
        saved = await tx.commit_operation(session, tx.execute(session, user, project, good))
        await submit_action(
            session,
            user,
            definition,
            "modify",
            ActionPayload(recordId=a.id, expectedRevision=2, values={"note": "later"}),
        )
        with pytest.raises(HTTPException) as failure:
            await tx.commit_operation(session, tx.undo(session, user, project, saved.id))
        assert failure.value.status_code == 409
        assert (await session.get(OntologyRuntimeRecord, b.id)).values["note"] == "new"
        assert (await session.get(OntologyTransaction, saved.id)).undone_at is None


@pytest.mark.asyncio
async def test_single_undo_preserves_absent_and_null_values_and_record_identity(runtime):
    engine, user, definition, _ = runtime
    async with AsyncSession(engine, expire_on_commit=False) as session:
        record = await submit_action(
            session, user, definition, "create", ActionPayload(values={"key": "A"})
        )
        changed = await submit_action(
            session,
            user,
            definition,
            "modify",
            ActionPayload(recordId=record.id, expectedRevision=1, values={"note": None}),
        )
        await tx.commit_operation(
            session, tx.undo_single(session, user, definition, record.id, changed.revision)
        )
        assert (await session.get(OntologyRuntimeRecord, record.id)).values == {"key": "A"}
        deleted = await submit_action(
            session,
            user,
            definition,
            "delete",
            ActionPayload(recordId=record.id, expectedRevision=3),
        )
        await tx.commit_operation(
            session, tx.undo_single(session, user, definition, record.id, deleted.revision)
        )
        current = await session.get(OntologyRuntimeRecord, record.id)
        assert current.revision == 5 and not current.deleted and current.values == {"key": "A"}
