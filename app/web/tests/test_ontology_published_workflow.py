"""Authenticated API acceptance against a migrated, disposable PostgreSQL database."""

import os
import unittest
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.web.api.dependencies.session import get_db
from app.web.api.models import InviteCodes, TeamUserJoin, User
from app.web.api.models.user import RoleTypes
from app.web.api.utils.models import StatusTypes
from app.web.core.security import get_password_hash

URL = os.environ.get("ONTOLOGY_WORKFLOW_TEST_DATABASE_URL")
BASE = "/api/ontology/v1"


@unittest.skipUnless(URL, "Requires the migrated ontology_workflow_regression database")
class PublishedWorkflow(unittest.IsolatedAsyncioTestCase):
    async def test_login_publish_edit_history_and_revoke(self):
        from app.web.main import app

        target = make_url(URL)
        if target.database != "ontology_workflow_regression" or target.host != "127.0.0.1":
            self.fail("Workflow tests require a loopback ontology_workflow_regression database")
        engine = create_async_engine(target)
        self.addAsyncCleanup(engine.dispose)
        password = uuid4().hex
        async with engine.connect() as connection:
            transaction = await connection.begin()
            sessions = async_sessionmaker(connection, join_transaction_mode="create_savepoint", expire_on_commit=False)

            async def test_session():
                async with sessions() as session:
                    yield session

            self.addCleanup(app.dependency_overrides.pop, get_db)
            app.dependency_overrides[get_db] = test_session
            async with sessions() as seed:
                owner = User(email="owner@example.org", phone="10000000001", hashed_password=get_password_hash(password),
                             is_superuser=True)
                reader = User(email="reader@example.org", phone="10000000002", hashed_password=owner.hashed_password)
                invite = InviteCodes(code="workflow-test")
                seed.add_all([owner, reader, invite])
                await seed.commit()

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                async def login(user):
                    response = await client.post("/api/v1/login/token", data={"username": user.email, "password": password})
                    self.assertEqual(response.status_code, 200)
                    return {"Authorization": "Bearer " + response.json()["access_token"]}

                manager, member = await login(owner), await login(reader)
                org = await client.post("/api/v1/tenant/", headers=manager, json={"name": "workflow-org"})
                self.assertEqual(org.status_code, 200, org.text)
                team = await client.post("/api/v1/team/", headers=manager, params={"tenant_id": org.json()["id"]},
                                         json={"name": "Research and Development"})
                self.assertEqual(team.status_code, 200, team.text)
                team_id = UUID(team.json()["id"])
                async with sessions() as session:
                    assignments = (await session.scalars(select(TeamUserJoin).where(
                        TeamUserJoin.team_id == team_id, TeamUserJoin.user_id == owner.id))).all()
                    self.assertEqual([(a.role, a.status) for a in assignments], [(RoleTypes.OWNER, StatusTypes.ENABLE)])
                    owner_invite = await session.get(InviteCodes, assignments[0].invite_code)
                    self.assertEqual(owner_invite.status, StatusTypes.DISABLE)
                    membership = TeamUserJoin(user_id=reader.id, team_id=team_id, role=RoleTypes.MEMBER,
                                              invite_by=owner.id, invite_code=invite.id)
                    session.add(membership)
                    await session.commit()
                    reader_membership = membership.id
                profile = {"name": "Workflow project", "teamId": str(team_id)}
                self.assertEqual((await client.post(BASE + "/projects", headers=member, json=profile)).status_code, 404)
                project = await client.post(BASE + "/projects", headers=manager, json=profile)
                self.assertEqual(project.status_code, 201, project.text)
                self.assertEqual(project.json()["ownerId"], str(owner.id))
                self.assertEqual((await client.post(BASE + "/projects", headers=manager, json=profile)).status_code, 409)
                projects = await client.get(BASE + "/projects", headers=manager)
                self.assertIn(project.json(), projects.json()["items"])
                group = {"kind": "group", "id": str(team_id)}
                editor = {"kind": "user", "id": str(owner.id)}
                definition = {
                    "id": "work-order", "name": "Work order", "pluralName": "Work orders",
                    "projectId": project.json()["id"], "source": {"mode": "none"},
                    "properties": [{"id": key, "name": key, "type": "string", "required": True}
                                   for key in ("id", "note")],
                    "primaryKey": ["id"], "titleKey": "id",
                    "security": {"mode": "explicit", "objectReaders": [group], "fieldReaders": {"note": [editor]}},
                    "actions": [{"kind": kind, "fields": fields, "submitters": [editor]} for kind, fields in
                                [("create", ["id", "note"]), ("modify", ["note"]), ("delete", [])]],
                }
                draft = await client.post(BASE + "/drafts", headers=manager, json=definition)
                self.assertEqual(draft.status_code, 201, draft.text)
                path = BASE + "/drafts/" + draft.json()["id"]
                version = {"revision": draft.json()["revision"]}
                validation = await client.post(path + "/validate", headers=manager, json=version)
                self.assertEqual(validation.json()["errors"], [])
                published = await client.post(path + "/publish", headers=manager, json=version)
                self.assertEqual(published.status_code, 200, published.text)
                objects = BASE + "/object-types/" + draft.json()["id"]
                payload = {"values": {"id": "order-1", "note": "restricted"}}
                denied = await client.post(objects + "/actions/create", headers=member, json=payload)
                self.assertEqual(denied.status_code, 403)
                created = await client.post(objects + "/actions/create", headers=manager, json=payload)
                self.assertEqual(created.status_code, 200, created.text)
                receipt = created.json()
                records = await client.get(objects + "/records", headers=member)
                self.assertEqual(records.json()["items"][0]["properties"], {"id": "order-1"})
                action = {"recordId": receipt["id"], "expectedRevision": receipt["revision"], "values": {"note": "edited"}}
                modified = await client.post(objects + "/actions/modify", headers=manager, json=action)
                self.assertEqual(modified.status_code, 200, modified.text)
                stale = await client.post(objects + "/actions/modify", headers=manager, json=action)
                self.assertEqual(stale.status_code, 409)
                history = objects + "/records/" + receipt["id"] + "/history"
                result = await client.get(history, headers=member)
                self.assertEqual([(item["kind"], item["revision"]) for item in result.json()["items"]],
                                 [("modify", 2), ("create", 1)])
                async with sessions() as session:
                    await session.delete(await session.get(TeamUserJoin, reader_membership))
                    await session.commit()
                self.assertEqual((await client.get(history, headers=member)).status_code, 404)
                self.assertEqual((await client.get(history)).status_code, 401)
            await transaction.rollback()
