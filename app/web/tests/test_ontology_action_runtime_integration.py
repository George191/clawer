"""Exercise action commits and membership revocation in an isolated PostgreSQL schema."""

import os
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.web.api.dependencies.session import get_db
from app.web.api.models import Team, TeamUserJoin, Tenant, User
from app.web.api.models.ontology_graph import OntologyLink, OntologyLinkType, SetQuery
from app.web.api.models.ontology_registry import CatalogChange, Definition, OntologyDefinitionRow
from app.web.api.models.ontology_revisions import (
    OntologyRevisionGrant,
    OntologyRevisionProposal,
    OntologyRevisionSnapshot,
)
from app.web.api.models.ontology_runtime import OntologyActionEvent, OntologyRuntimeRecord
from app.web.api.models.user import InviteCodes, RoleTypes
from app.web.api.routes.ontology_registry import change_catalog_state
from app.web.api.routes.team import router as team_router
from app.web.api.utils.models import StatusTypes
from app.web.core.security import ALGORITHM, JWTTokenGenerator
from app.web.services.ontology_action_runtime import (
    ActionPayload,
    get_record,
    list_records,
    record_history,
    submit_action,
)
from app.web.services.ontology_graph_sets import evaluate
from app.web.services.ontology_record_policy import validate_record_policy_refs


@pytest_asyncio.fixture
async def runtime():
    url = os.environ.get("ONTOLOGY_ACTION_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires an isolated ontology_action_regression PostgreSQL database")
    if make_url(url).database != "ontology_action_regression":
        pytest.fail("Action tests require the ontology_action_regression database")
    schema = f"action_test_{uuid4().hex}"
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
            tables = Team.metadata.tables
            await connection.run_sync(
                Team.metadata.create_all,
                tables=[
                    tables[name]
                    for name in (
                        "dictionaries",
                        "tenant",
                        "user",
                        "team",
                        "invite_codes",
                        "team_user_join",
                        "helio_registry_definitions",
                        "helio_runtime_records",
                        "helio_transactions",
                        "helio_action_events",
                        "helio_link_types",
                        OntologyRevisionGrant.__tablename__,
                        OntologyRevisionProposal.__tablename__,
                        OntologyRevisionSnapshot.__tablename__,
                        "helio_markings",
                        "helio_marking_changes",
                        "helio_links",
                        "helio_removed_links",
                    )
                ],
            )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            tenant = Tenant(name="action-test")
            user = User(email="action@example.org", phone="123", hashed_password="unused")
            invite = InviteCodes(code="action")
            session.add_all([tenant, user, invite])
            await session.flush()
            team = Team(tenant_id=tenant.id, name="project")
            session.add(team)
            await session.flush()
            membership = TeamUserJoin(
                user_id=user.id, team_id=team.id, invite_by=user.id, invite_code=invite.id
            )
            group = {"kind": "group", "id": str(team.id)}
            definition = {
                "id": "object",
                "name": "Object",
                "projectId": str(team.id),
                "source": {"mode": "none"},
                "properties": [
                    {"id": "key", "name": "Key", "type": "string", "required": True},
                    {"id": "note", "name": "Notes", "type": "string"},
                ],
                "primaryKey": ["key"],
                "titleKey": "key",
                "security": {"mode": "explicit", "objectReaders": [group]},
                "actions": [
                    {"kind": "create", "submitters": [group], "fields": ["key"]},
                    {"kind": "modify", "submitters": [group], "fields": ["note"]},
                    {"kind": "delete", "submitters": [group]},
                ],
            }
            row = OntologyDefinitionRow(
                project_id=team.id,
                owner_id=user.id,
                semantic_id="object",
                draft_definition=definition,
                published_definition=definition,
                published_at=datetime.now(timezone.utc),
            )
            session.add_all([row, membership])
            await session.commit()
        yield engine, user, row.id, membership.id
    finally:
        try:
            async with engine.begin() as connection:
                await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_restoring_object_type_preserves_record_identity_and_data(runtime):
    engine, actor, definition_id, member_id = runtime
    async with AsyncSession(engine, expire_on_commit=False) as session:
        await session.execute(update(TeamUserJoin).where(TeamUserJoin.id == member_id).values(role=RoleTypes.ADMIN))
        original = await submit_action(session, actor, definition_id, "create", ActionPayload(values={"key": "retained"}))
        removed = await change_catalog_state(definition_id, CatalogChange(revision=1, operation="delete"), session, actor)
        with pytest.raises(HTTPException) as invisible:
            await list_records(session, actor, definition_id, limit=10, offset=0)
        assert invisible.value.status_code == 404
        with pytest.raises(HTTPException) as rejected:
            await submit_action(session, actor, definition_id, "create", ActionPayload(values={"key": "blocked"}))
        assert rejected.value.status_code == 404
        await change_catalog_state(definition_id, CatalogChange(revision=removed["revision"], operation="restore"), session, actor)
        records = await list_records(session, actor, definition_id, limit=10, offset=0)
        assert [(item.id, item.properties["key"]) for item in records.items] == [(original.id, "retained")]


@pytest.mark.asyncio
async def test_team_creation_rolls_back_when_the_creator_cannot_be_added(runtime):
    engine, user, _, _ = runtime
    async with AsyncSession(engine) as session:
        await session.execute(update(User).where(User.id == user.id).values(is_superuser=True))
        await session.execute(
            text("CREATE UNIQUE INDEX inject_membership_conflict ON team_user_join (user_id)")
        )
        tenant_id = await session.scalar(select(Tenant.id))
        await session.commit()

    async def sessions():
        async with AsyncSession(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(team_router)
    app.dependency_overrides[get_db] = sessions
    token = JWTTokenGenerator({"sub": str(user.id)}, ALGORITHM).access_token
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with pytest.raises(IntegrityError) as failure:
            await client.post(
                "/team/",
                params={"tenant_id": str(tenant_id)},
                headers={"Authorization": f"Bearer {token}"},
                json={"name": "Research and Development"},
            )
        assert failure.value.orig.__cause__.constraint_name == "inject_membership_conflict"
    async with AsyncSession(engine) as session:
        assert await session.scalar(select(func.count()).select_from(Team)) == 1
        assert await session.scalar(select(func.count()).select_from(InviteCodes)) == 1
        assert await session.scalar(select(func.count()).select_from(TeamUserJoin)) == 1


@pytest.mark.asyncio
async def test_create_classifies_business_duplicates_and_rolls_back_an_audit_failure(runtime):
    engine, actor, definition_id, _ = runtime
    async with AsyncSession(engine) as session:

        async def create(key):
            return await submit_action(
                session, actor, definition_id, "create", ActionPayload(values={"key": key})
            )

        assert (await create("first")).revision == 1
        with pytest.raises(HTTPException) as duplicate:
            await create("first")
        assert duplicate.value.status_code == 409
        await session.execute(
            text("CREATE UNIQUE INDEX inject_audit_failure ON helio_action_events (actor_id)")
        )
        await session.commit()
        with pytest.raises(IntegrityError):
            await create("second")
        assert await session.scalar(select(func.count()).select_from(OntologyRuntimeRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(OntologyActionEvent)) == 1


@pytest.mark.asyncio
async def test_create_waits_for_membership_revocation_then_denies_the_actor(runtime):
    engine, actor, definition_id, membership_id = runtime
    async with AsyncSession(engine) as revoker, AsyncSession(engine) as writer:
        await revoker.execute(
            update(TeamUserJoin)
            .where(TeamUserJoin.id == membership_id)
            .values(status=StatusTypes.DISABLE)
        )
        payload = ActionPayload(values={"key": "pending-revocation"})
        await writer.execute(text("SET LOCAL lock_timeout = '500ms'"))
        with pytest.raises(DBAPIError) as blocked:
            await submit_action(writer, actor, definition_id, "create", payload)
        assert blocked.value.orig.sqlstate == "55P03"
        await writer.rollback()
        await revoker.commit()
        with pytest.raises(HTTPException) as denied:
            await submit_action(writer, actor, definition_id, "create", payload)
        assert denied.value.status_code == 404
        assert await writer.scalar(select(func.count()).select_from(OntologyRuntimeRecord)) == 0
        assert await writer.scalar(select(func.count()).select_from(OntologyActionEvent)) == 0


@pytest.mark.asyncio
async def test_read_holds_policy_version_until_projection_finishes(runtime):
    engine, actor, definition_id, _ = runtime
    async with AsyncSession(engine) as reader, AsyncSession(engine) as editor:
        receipt = await submit_action(
            reader, actor, definition_id, "create", ActionPayload(values={"key": "old"})
        )
        assert (await get_record(reader, actor, definition_id, receipt.id)).id == receipt.id
        old = await editor.scalar(
            select(OntologyDefinitionRow.published_definition).where(
                OntologyDefinitionRow.id == definition_id
            )
        )
        changed = deepcopy(old)
        changed["security"]["recordRules"] = [
            {
                "kind": "property",
                "field": "key",
                "match": "equals",
                "value": "new",
                "readers": changed["security"]["objectReaders"],
            }
        ]
        revision = (
            update(OntologyDefinitionRow)
            .where(OntologyDefinitionRow.id == definition_id)
            .values(published_definition=changed)
        )
        await editor.execute(text("SET LOCAL lock_timeout = '500ms'"))
        with pytest.raises(DBAPIError) as blocked:
            await editor.execute(revision)
        assert blocked.value.orig.sqlstate == "55P03"
        await editor.rollback()
        await reader.commit()
        await editor.execute(revision)
        await editor.commit()
        with pytest.raises(HTTPException) as revoked:
            await get_record(reader, actor, definition_id, receipt.id)
        assert revoked.value.status_code == 404


@pytest.mark.asyncio
async def test_deleted_record_history_is_ordered_scoped_and_rechecks_access(runtime):
    engine, actor, definition_id, membership_id = runtime
    async with AsyncSession(engine) as session:
        create = await submit_action(
            session, actor, definition_id, "create", ActionPayload(values={"key": "private-key"})
        )
        edit = ActionPayload(
            recordId=create.id, expectedRevision=1, values={"note": "private-note"}
        )
        changed = await submit_action(session, actor, definition_id, "modify", edit)
        with pytest.raises(HTTPException) as stale:
            await submit_action(session, actor, definition_id, "modify", edit)
        assert stale.value.status_code == 409
        await session.rollback()
        await submit_action(
            session,
            actor,
            definition_id,
            "delete",
            ActionPayload(recordId=create.id, expectedRevision=changed.revision),
        )

        async def history(definition_id, record_id, offset=0):
            return await record_history(
                session, actor, definition_id, record_id, limit=2, offset=offset
            )

        first = await history(definition_id, create.id)
        second = await history(definition_id, create.id, offset=2)
        assert first.hasMore and not second.hasMore
        events = first.items + second.items
        assert [(event.kind, event.revision) for event in events] == [
            ("delete", 3),
            ("modify", 2),
            ("create", 1),
        ]
        assert all(
            set(event.model_dump()) == {"revision", "kind", "createdAt", "changes", "canUndo"}
            for event in events
        )
        assert events[0].canUndo and not events[1].canUndo
        assert events[1].changes == {"note": {"after": "private-note"}}
        tombstones = await list_records(
            session, actor, definition_id, limit=5, offset=0, deleted=True
        )
        assert [item.id for item in tombstones.items] == [create.id]
        assert tombstones.actions == []
        with pytest.raises(HTTPException) as missing:
            await history(definition_id, uuid4())
        assert missing.value.status_code == 404

        original = await session.get(OntologyDefinitionRow, definition_id)
        other = OntologyDefinitionRow(
            project_id=original.project_id,
            owner_id=actor.id,
            semantic_id="different",
            draft_definition=original.draft_definition,
            published_definition=original.published_definition,
            published_at=datetime.now(timezone.utc),
        )
        session.add(other)
        other_id = other.id
        await session.commit()
        with pytest.raises(HTTPException) as wrong_definition:
            await history(other_id, create.id)
        assert wrong_definition.value.status_code == 404

        await session.execute(
            update(TeamUserJoin)
            .where(TeamUserJoin.id == membership_id)
            .values(status=StatusTypes.DISABLE)
        )
        await session.commit()
        with pytest.raises(HTTPException) as revoked:
            await history(definition_id, create.id)
        assert revoked.value.status_code == 404


@pytest.mark.asyncio
async def test_record_rules_guard_pages_histories_and_both_sides_of_edits(runtime):
    engine, actor, definition_id, membership_id = runtime
    async with AsyncSession(engine, expire_on_commit=False) as session:

        async def create(key):
            return await submit_action(
                session, actor, definition_id, "create", ActionPayload(values={"key": key})
            )

        ids = {
            key: (await create(key)).id
            for key in ["hidden", "shown", str(actor.id), "anchor", "via-link"]
        }
        row = await session.get(OntologyDefinitionRow, definition_id)
        group = row.published_definition["security"]["objectReaders"]
        rules = [
            {
                "kind": "property",
                "field": "note",
                "match": "equals",
                "value": "shared",
                "readers": group,
            },
            {"kind": "property", "field": "key", "match": "currentUser", "readers": group},
            {
                "kind": "related",
                "direction": "outgoing",
                "linkType": "assigned",
                "relatedRecordId": str(ids["anchor"]),
                "readers": group,
            },
            {
                "kind": "property",
                "field": "key",
                "match": "equals",
                "value": "create-allowed",
                "readers": group,
            },
        ]
        await submit_action(
            session,
            actor,
            definition_id,
            "modify",
            ActionPayload(recordId=ids["shown"], expectedRevision=1, values={"note": "shared"}),
        )
        link_type = OntologyLinkType(
            project_id=row.project_id,
            key="assigned",
            name="Assigned",
            cardinality="many_to_many",
            source_definition_id=row.id,
            target_definition_id=row.id,
        )
        session.add(link_type)
        await session.flush()
        link = OntologyLink(
            link_type_id=link_type.id,
            source_record_id=ids["via-link"],
            target_record_id=ids["anchor"],
        )
        session.add(link)
        await session.commit()

        new_definition = deepcopy(row.published_definition)
        new_definition["security"]["recordRules"] = rules
        defn = Definition.model_validate(new_definition)
        assert not await validate_record_policy_refs(session, row.id, defn)
        wrong_scope = defn.model_copy(update={"projectId": uuid4()})
        assert await validate_record_policy_refs(session, row.id, wrong_scope)
        row.published_definition = new_definition
        await session.commit()
        visible = sorted([ids["shown"], ids["via-link"], ids[str(actor.id)]])
        for offset in range(3):
            page = await list_records(session, actor, definition_id, limit=1, offset=offset)
            assert [r.id for r in page.items] == visible[offset : offset + 1]
            assert page.hasMore == (offset < 2)
        search = SetQuery.model_validate(
            {"filters": [{"field": "key", "op": "contains", "value": "i"}], "groupBy": "key"}
        )
        # hidden and via-link both satisfy the literal filter; the hidden row must not affect totals or groups.
        filtered = await evaluate(session, actor, definition_id, search, 1, 0, 0)
        assert [item.id for item in filtered["items"]] == [ids["via-link"]]
        assert filtered["count"] == 1 and not filtered["hasMore"]
        assert [item["value"] for item in filtered["groups"]] == ["via-link"]
        for request in [
            get_record(session, actor, definition_id, ids["hidden"]),
            record_history(session, actor, definition_id, ids["hidden"], limit=5, offset=0),
            submit_action(
                session,
                actor,
                definition_id,
                "delete",
                ActionPayload(recordId=ids["hidden"], expectedRevision=99),
            ),
        ]:
            with pytest.raises(HTTPException) as denied:
                await request
            assert denied.value.status_code == 404
        with pytest.raises(HTTPException) as changed_scope:
            await submit_action(
                session,
                actor,
                definition_id,
                "modify",
                ActionPayload(
                    recordId=ids["shown"], expectedRevision=2, values={"note": "no longer shared"}
                ),
            )
        assert changed_scope.value.status_code == 403
        with pytest.raises(HTTPException) as denied_create:
            await create("unmatched")
        assert denied_create.value.status_code == 403
        allowed = await create("create-allowed")
        assert (await get_record(session, actor, definition_id, allowed.id)).id == allowed.id
        audit = await record_history(session, actor, definition_id, ids["shown"], limit=5, offset=0)
        assert all(event.changes is None for event in audit.items)
        with pytest.raises(HTTPException, match="Remove relationships") as linked_delete:
            await submit_action(
                session,
                actor,
                definition_id,
                "delete",
                ActionPayload(recordId=ids["via-link"], expectedRevision=1),
            )
        assert linked_delete.value.status_code == 409
        await session.delete(link)
        await session.commit()
        with pytest.raises(HTTPException) as revoked_link:
            await get_record(session, actor, definition_id, ids["via-link"])
        assert revoked_link.value.status_code == 404
        unrestricted = deepcopy(new_definition)
        unrestricted["security"]["recordRules"] = []
        row.published_definition = unrestricted
        await session.commit()
        assert (
            await record_history(session, actor, definition_id, allowed.id, limit=5, offset=0)
        ).items[0].changes is None
        await session.execute(
            update(TeamUserJoin)
            .where(TeamUserJoin.id == membership_id)
            .values(status=StatusTypes.DISABLE)
        )
        await session.commit()
        with pytest.raises(HTTPException) as revoked_member:
            await get_record(session, actor, definition_id, allowed.id)
        assert revoked_member.value.status_code == 404
