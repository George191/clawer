"""Clearances apply to derived reads, historical events and receipt replays."""

from copy import deepcopy
from importlib import import_module
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models import Team, TeamUserJoin, User
from app.web.api.models.ontology_graph import LinkTypeInput, SetQuery
from app.web.api.models.ontology_markings import ChangeMarking, NewMarking
from app.web.api.models.ontology_registry import OntologyDefinitionRow
from app.web.api.models.ontology_runtime import OntologyTransaction
from app.web.api.models.user import RoleTypes
from app.web.api.routes.ontology_markings import change_marking, create_marking, marking_history
from app.web.api.routes.ontology_registry import published_types
from app.web.services import (
    ontology_graph as graph,
)
from app.web.services import (
    ontology_graph_sets as sets,
)
from app.web.services import (
    ontology_transactions as tx,
)
from app.web.services.ontology_action_runtime import (
    ActionPayload,
    get_record,
    record_history,
    submit_action,
)
from app.web.services.ontology_markings import require_clearance
from app.web.tests import test_ontology_action_runtime_integration as action_tests

runtime = action_tests.runtime


@pytest.mark.asyncio
async def test_clearance_propagates_across_types_receipts_and_revocation(runtime):
    engine, manager, definition_id, membership_id = runtime
    async with AsyncSession(engine, expire_on_commit=False) as session:
        membership = await session.get(TeamUserJoin, membership_id)
        project = membership.team_id
        membership.role = RoleTypes.OWNER
        reader = User(email="clearance@example.org", phone="456", hashed_password="unused")
        session.add(reader)
        await session.flush()
        session.add(
            TeamUserJoin(
                team_id=project,
                user_id=reader.id,
                invite_by=manager.id,
                invite_code=membership.invite_code,
            )
        )
        await session.commit()
        owner_grant = {"kind": "user", "id": str(manager.id)}
        reader_grant = {"kind": "user", "id": str(reader.id)}
        grants = [owner_grant, reader_grant]
        first = await create_marking(
            project, NewMarking(name="Engineering", readers=grants), session, manager
        )
        second = await create_marking(
            project, NewMarking(name="Restricted", readers=grants), session, manager
        )
        other_project = Team(
            name="Distinct clearance project",
            tenant_id=(await session.get(Team, project)).tenant_id,
        )
        session.add(other_project)
        await session.flush()
        session.add(
            TeamUserJoin(
                team_id=other_project.id,
                user_id=manager.id,
                invite_by=manager.id,
                role=RoleTypes.OWNER,
                invite_code=membership.invite_code,
            )
        )
        await session.commit()
        with pytest.raises(HTTPException) as outside:
            await require_clearance(session, manager, other_project.id, [first["id"]])
        assert outside.value.status_code == 404
        migration = import_module("app.web.alembic.versions.76a1b2c3d4e5_ontology_markings")

        def downgrade(conn):
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()

        with pytest.raises(RuntimeError, match="cannot be discarded"):
            await (await session.connection()).run_sync(downgrade)
        with pytest.raises(HTTPException) as forbidden:
            await change_marking(
                project, first["id"], ChangeMarking(readers=[], revision=1), session, reader
            )
        assert forbidden.value.status_code == 404
        row = await session.get(OntologyDefinitionRow, definition_id)
        old = deepcopy(row.published_definition)
        labels = [str(first["id"]), str(second["id"])]
        marked = deepcopy(old)
        marked["security"]["markings"] = labels
        row.published_definition = marked

        def sibling(name, mark_ids):
            doc = deepcopy(old)
            doc["id"] = name
            doc["security"]["markings"] = mark_ids
            return OntologyDefinitionRow(
                project_id=project,
                owner_id=manager.id,
                semantic_id=name,
                draft_definition=doc,
                published_definition=doc,
                published_at=row.published_at,
            )

        other = sibling("another", [labels[0]])
        public = sibling("public", [])
        session.add_all([other, public])
        await session.commit()

        def request(type_id, key):
            return tx.TransactionRequest(
                requestId=uuid4(),
                name=key,
                steps=[{"kind": "create", "definitionId": type_id, "values": {"key": key}}],
            )

        saved = request(definition_id, "marked-receipt")
        receipt = await tx.commit_operation(session, tx.execute(session, reader, project, saved))
        batch = await session.get(OntologyTransaction, receipt.id)
        assert set(batch.markings) == set(labels)
        rec = (await sets.evaluate(session, reader, definition_id, SetQuery(), 10, 0, 0))["items"][
            0
        ]
        public_record = await submit_action(
            session, manager, public.id, "create", ActionPayload(values={"key": "unmarked"})
        )
        link = await graph.create_type(
            session,
            manager,
            project,
            LinkTypeInput(
                key="marked", name="Marked", sourceDefinitionId=public.id, targetDefinitionId=row.id
            ),
        )
        await graph.mutate_edge(session, manager, link["id"], public_record.id, rec.id, add=True)
        await session.commit()
        assert (
            await graph.neighbors(session, reader, link["id"], public_record.id, "outgoing", 5, 0)
        )["items"]
        # A grant cannot be revoked while a record decision is in progress.
        await get_record(session, reader, row.id, rec.id)
        async with AsyncSession(engine) as revoker:
            await revoker.execute(text("SET LOCAL lock_timeout='300ms'"))
            with pytest.raises(DBAPIError) as waiting:
                await change_marking(
                    project,
                    first["id"],
                    ChangeMarking(readers=[owner_grant], revision=1),
                    revoker,
                    manager,
                )
            assert waiting.value.orig.sqlstate == "55P03"
        await session.commit()
        await change_marking(
            project, first["id"], ChangeMarking(readers=[owner_grant], revision=1), session, manager
        )
        for operation in (
            lambda: get_record(session, reader, row.id, rec.id),
            lambda: record_history(session, reader, row.id, rec.id, limit=10, offset=0),
            lambda: sets.evaluate(session, reader, row.id, SetQuery(), 10, 0, 0),
            lambda: graph.neighbors(
                session, reader, link["id"], public_record.id, "outgoing", 5, 0
            ),
            lambda: tx.execute(session, reader, project, saved),
            lambda: tx.undo(session, reader, project, receipt.id),
            lambda: submit_action(
                session, reader, other.id, "create", ActionPayload(values={"key": "denied"})
            ),
        ):
            with pytest.raises(HTTPException) as hidden:
                await operation()
            assert hidden.value.status_code == 404
        page = await published_types(session, reader, 2, 0, "", project)
        assert [i["id"] for i in page["items"]] == [public.id] and not page["hasMore"]
        assert not (
            await session.scalars(
                select(OntologyTransaction).where(tx.visible_receipt(reader, project))
            )
        ).all()
        await session.commit()
        # A reviewed declassification can expose the current row. Its earlier event and receipt keep their original labels.
        row.published_definition = old
        await session.commit()
        assert (await get_record(session, reader, row.id, rec.id)).id == rec.id
        assert not (await record_history(session, reader, row.id, rec.id, limit=10, offset=0)).items
        assert not (
            await session.scalars(
                select(OntologyTransaction).where(tx.visible_receipt(reader, project))
            )
        ).all()
        await session.commit()
        await change_marking(
            project, first["id"], ChangeMarking(readers=grants, revision=2), session, manager
        )
        assert (await tx.execute(session, reader, project, saved)).id == receipt.id
        assert (
            len((await record_history(session, reader, row.id, rec.id, limit=10, offset=0)).items)
            == 1
        )
        history = await marking_history(project, first["id"], session, manager, 5, 0)
        assert [item["revision"] for item in history["items"]] == [3, 2, 1]
