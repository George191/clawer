"""Exercise action commits and membership revocation in an isolated PostgreSQL schema."""

import os
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
from app.web.api.models.ontology_registry import OntologyDefinitionRow
from app.web.api.models.ontology_runtime import OntologyActionEvent, OntologyRuntimeRecord
from app.web.api.models.user import InviteCodes
from app.web.api.routes.team import router as team_router
from app.web.api.utils.models import StatusTypes
from app.web.core.security import ALGORITHM, JWTTokenGenerator
from app.web.services.ontology_action_runtime import ActionPayload, record_history, submit_action


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
                        "ontology_registry_definitions",
                        "ontology_runtime_records",
                        "ontology_action_events",
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
            text("CREATE UNIQUE INDEX inject_audit_failure ON ontology_action_events (actor_id)")
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
        assert all(set(event.model_dump()) == {"revision", "kind", "createdAt"} for event in events)
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
