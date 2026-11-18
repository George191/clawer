"""Revision routes and compatibility checks against isolated PostgreSQL."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from importlib import import_module
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, Header, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import select

from app.web.api.dependencies.common import get_current_user
from app.web.api.dependencies.session import get_db
from app.web.api.models import Team, TeamUserJoin, Tenant, User
from app.web.api.models.ontology_graph import OntologySavedSet
from app.web.api.models.ontology_registry import OntologyDefinitionRow
from app.web.api.models.ontology_runtime import OntologyRuntimeRecord
from app.web.api.models.user import InviteCodes, RoleTypes
from app.web.api.routes.ontology_registry import router as registry_router
from app.web.api.models.ontology_markings import OntologyMarking
from app.web.api.routes.ontology_revisions import router as revision_router
from app.web.services.ontology_revisions import definition_diff
from app.web.services import ontology_registry

URL = os.getenv("ONTOLOGY_REVISION_TEST_DATABASE_URL")
BASE = "/api/ontology/v1"


class DefinitionDiffTests(unittest.TestCase):
    def test_property_change_shows_before_after_and_full_reorder(self):
        old = {"properties": [{"id": "x", "name": "X"}, {"id": "y", "name": "Y"}], "name": "Old"}
        new = {"properties": [{"id": "y", "name": "Y"}, {"id": "x", "name": "Renamed"}], "name": "New"}
        self.assertEqual(definition_diff(old, new), [
            {"path": "properties.x", "before": {"id": "x", "name": "X"}, "after": {"id": "x", "name": "Renamed"}},
            {"path": "properties.order", "before": ["x", "y"], "after": ["y", "x"]},
            {"path": "name", "before": "Old", "after": "New"},
        ])


@unittest.skipUnless(URL, "Requires isolated ontology_revision_regression PostgreSQL")
class RevisionIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        target = make_url(URL)
        self.assertEqual(target.database, "ontology_revision_regression")
        self.assertIn(target.host, {"127.0.0.1", "::1"})
        schema = "revision_test_" + uuid4().hex
        self.engine = create_async_engine(target, connect_args={"server_settings": {"search_path": schema}})
        self.addAsyncCleanup(self.engine.dispose)

        async def drop():
            async with self.engine.begin() as connection:
                await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))

        self.addAsyncCleanup(drop)
        async with self.engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
            migration = import_module("app.web.alembic.versions.74a1b2c3d4e5_ontology_revisions")

            def setup(sync):
                tables = Team.metadata.tables
                Team.metadata.create_all(sync, tables=[tables[name] for name in (
                    "dictionaries", "tenant", "user", "team", "invite_codes", "team_user_join",
                    "ontology_registry_definitions", "ontology_runtime_records", "ontology_saved_sets",
                    "ontology_link_types", "ontology_links", "ontology_markings",
                )])
                with Operations.context(MigrationContext.configure(sync)):
                    migration.upgrade()

            await connection.run_sync(setup)

        async with AsyncSession(self.engine, expire_on_commit=False) as session:
            self.actors = [User(email=f"{index}@example.org", phone=f"200{index}", hashed_password="unused")
                           for index in range(3)]
            tenant = Tenant(name="revision tenant")
            invite = InviteCodes(code="revision test")
            session.add_all([tenant, invite, *self.actors])
            await session.flush()
            team = Team(name="revision team", tenant_id=tenant.id)
            session.add(team)
            await session.flush()
            self.team_id = str(team.id)
            session.add_all([
                TeamUserJoin(user_id=self.actors[index].id, team_id=team.id, invite_by=self.actors[0].id, invite_code=invite.id,
                             role=RoleTypes.OWNER if index == 0 else RoleTypes.MEMBER)
                for index in (0, 1)
            ])
            await session.commit()

        app = FastAPI()
        app.include_router(registry_router)
        app.include_router(revision_router)

        async def get_test_session():
            async with AsyncSession(self.engine, expire_on_commit=False) as session:
                yield session

        async def test_actor(x_test_user: UUID = Header()):
            for actor in self.actors:
                if actor.id == x_test_user:
                    return actor
            raise HTTPException(401)

        app.dependency_overrides[get_db] = get_test_session
        app.dependency_overrides[get_current_user] = test_actor
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://fixture")
        self.addAsyncCleanup(self.client.aclose)

        self.definition = {
            "projectId": self.team_id, "id": "invoice", "name": "Invoice", "pluralName": "Invoices",
            "description": "Published fixture", "source": {"mode": "none"},
            "properties": [{"id": "id", "name": "ID", "type": "string", "required": True},
                           {"id": "note", "name": "Note", "type": "string"}],
            "primaryKey": ["id"], "titleKey": "id",
            "actions": [{"kind": "create", "fields": ["id"], "submitters": [{"kind": "group", "id": self.team_id}]}],
            "security": {"mode": "explicit", "objectReaders": [{"kind": "group", "id": self.team_id}]},
        }
        draft = await self.call("POST", BASE + "/drafts", 201, self.definition)
        initial = await self.call("POST", BASE + "/drafts/" + draft["id"] + "/publish", 200,
                                  {"revision": draft["revision"]})
        self.definition_id = UUID(draft["id"])
        self.head = initial["revision"]
        self.route = BASE + "/object-types/" + draft["id"]
        self.proposals = self.route + "/revision-proposals"
        self.roles = self.route + "/revision-roles/" + str(self.actors[1].id)

    async def call(self, method, path, status, body=None, *, actor=0):
        if not path.startswith("/"):
            path = self.route + path
        response = await self.client.request(method, path, json=body, headers={"X-Test-User": str(self.actors[actor].id)})
        self.assertEqual(response.status_code, status, response.text)
        return response.json() if response.content else None

    async def grant(self, review=True):
        await self.call("PUT", self.roles, 204, {"canEdit": False, "canReview": review})

    async def test_downgrading_markings_requires_manager_and_still_cleared_reviewer(self):
        group = [{"kind": "group", "id": self.team_id}]
        async with AsyncSession(self.engine, expire_on_commit=False) as session:
            label = OntologyMarking(project_id=UUID(self.team_id), name="Restricted", readers=group)
            session.add(label)
            await session.commit()
        await self.call("PUT", self.roles, 204, {"canEdit": True, "canReview": True})
        marked = {**self.definition, "security": {**self.definition["security"], "markings": [str(label.id)]}}
        change = await self.propose(definition=marked)
        await self.vote(change)
        await self.merge(change)
        revision = self.head + 1
        body = {"definition": self.definition, "revision": revision, "summary": "Remove clearance"}
        await self.call("POST", self.proposals, 403, body, actor=1)
        restore = await self.propose(definition=self.definition, head=revision)
        await self.vote(restore)
        async with AsyncSession(self.engine) as session:
            row = await session.get(OntologyMarking, label.id)
            row.readers = [{"kind": "user", "id": str(self.actors[0].id)}]
            await session.commit()
        await self.merge(restore, 404, head=revision)
        await self.call("GET", self.route + "/revision-workspace", 404, actor=1)
        async with AsyncSession(self.engine) as session:
            row = await session.get(OntologyMarking, label.id)
            row.readers = group
            await session.commit()
        await self.merge(restore, head=revision)

    async def propose(self, *, head=None, name="New invoice", expected=201, definition=None):
        return await self.call("POST", self.proposals, expected, {
            "revision": head or self.head, "definition": definition or {**self.definition, "name": name},
            "summary": name,
        })

    async def vote(self, proposal, *, expected=200, actor=1, approve=True):
        return await self.call("POST", f"{self.proposals}/{proposal['id']}/review", expected,
                               {"approve": approve, "note": "Independent review"}, actor=actor)

    async def merge(self, proposal, expected=200, *, head=None, actor=0):
        return await self.call("POST", f"{self.proposals}/{proposal['id']}/merge", expected, {"revision": head or self.head},
                               actor=actor)

    async def test_independent_review_diff_history_restore_and_stale_peer(self):
        await self.call("GET", self.route + "/revision-workspace", 404, actor=1)
        await self.call("GET", self.route + "/revision-workspace", 404, actor=2)
        await self.grant()
        workspace = await self.call("GET", self.route + "/revision-workspace", 200, actor=1)
        self.assertEqual((workspace["canEdit"], workspace["canReview"], workspace["canManageRoles"]), (False, True, False))
        proposal = await self.propose()
        peer = await self.propose(name="Parallel peer")
        await self.vote(proposal, actor=0, expected=403)
        await self.merge(proposal, expected=409)
        details = await self.call("GET", f"{self.proposals}/{proposal['id']}", 200, actor=1)
        self.assertIn({"path": "name", "before": "Invoice", "after": "New invoice"}, details["changes"])
        await self.vote(proposal)
        await self.vote(peer)
        await self.merge(proposal, expected=403, actor=1)
        merged = await self.merge(proposal)
        self.assertEqual(merged["mergedRevision"], self.head + 1)
        await self.merge(peer, expected=409, head=self.head + 1)
        published = await self.call("GET", self.route, 200)
        self.assertEqual(published["definition"]["name"], "New invoice")
        restore = await self.call("POST", f"{self.route}/revisions/{self.head}/restore-proposals", 201, {
            "summary": "Restore invoice metadata", "revision": self.head + 1,
        })
        await self.vote(restore)
        await self.merge(restore, head=self.head + 1)
        restored = await self.call("GET", self.route, 200)
        self.assertEqual(restored["revision"], self.head + 2)
        self.assertEqual(restored["definition"]["name"], "Invoice")
        history = (await self.call("GET", self.route + "/revisions", 200))["items"]
        self.assertEqual([row["revision"] for row in history], [self.head + 2, self.head + 1, self.head])
        self.assertEqual(history[0]["restoreRevision"], self.head)
        self.assertIsNone(history[-1]["proposalId"])

    async def test_revocation_and_rejection_never_change_publication(self):
        await self.grant()
        proposed = await self.propose()
        await self.vote(proposed)
        await self.grant(review=False)
        await self.merge(proposed, expected=409)
        await self.call("GET", self.proposals, 404, actor=1)
        later = await self.propose(name="Rejected invoice")
        await self.grant()
        await self.vote(later, approve=False)
        await self.merge(later, expected=409)
        self.assertEqual((await self.call("GET", self.route, 200))["revision"], self.head)

    async def test_migration_backfills_existing_type_and_captures_an_old_web_publication(self):
        migration = import_module("app.web.alembic.versions.74a1b2c3d4e5_ontology_revisions")
        async with self.engine.begin() as connection:
            def replay(sync):
                with Operations.context(MigrationContext.configure(sync)):
                    migration.downgrade()
                    migration.upgrade()
            await connection.run_sync(replay)
        history = (await self.call("GET", self.route + "/revisions", 200))["items"]
        self.assertEqual([row["revision"] for row in history], [self.head])
        draft = await self.call("POST", BASE + "/drafts", 201, {**self.definition, "id": "old-web"})
        async with AsyncSession(self.engine) as session:
            row = await session.get(OntologyDefinitionRow, UUID(draft["id"]))
            row.published_definition = row.draft_definition
            row.published_at = row.updated_at = datetime.now(timezone.utc)
            row.revision += 1
            await session.commit()
        history = (await self.call("GET", BASE + "/object-types/" + draft["id"] + "/revisions", 200))["items"]
        self.assertEqual([row["revision"] for row in history], [draft["revision"] + 1])

    async def test_data_and_saved_set_dependencies_block_incompatible_definitions(self):
        async with AsyncSession(self.engine) as session:
            session.add(OntologySavedSet(definition_id=self.definition_id, owner_id=self.actors[1].id, name="notes", query={
                "filters": [{"field": "note", "op": "eq", "value": "visible"}],
            }))
            session.add(OntologyRuntimeRecord(definition_id=self.definition_id, key_digest="id-a", values={"id": "A"}))
            await session.commit()
        missing = deepcopy(self.definition)
        missing["properties"].pop()
        blocked = await self.propose(expected=422, definition=missing)
        self.assertIn("saved object sets", str(blocked))
        required = deepcopy(self.definition)
        required["properties"][1]["required"] = True
        required["actions"][0]["fields"].append("note")
        blocked = await self.propose(expected=422, definition=required)
        self.assertIn("lack a newly required value", str(blocked))
        changed = deepcopy(self.definition)
        changed["properties"][1]["type"] = "integer"
        blocked = await self.propose(expected=422, definition=changed)
        self.assertIn("immutable", str(blocked))
        self.assertEqual((await self.call("GET", self.route, 200))["revision"], self.head)

    async def test_merge_revalidates_after_serialized_record_write(self):
        await self.grant()
        removed = deepcopy(self.definition)
        removed["properties"].pop()
        proposed = await self.propose(definition=removed)
        await self.vote(proposed)
        async with AsyncSession(self.engine) as writer:
            await writer.scalar(select(OntologyDefinitionRow).where(
                OntologyDefinitionRow.id == self.definition_id,
            ).with_for_update(read=True))
            concurrent_merge = asyncio.create_task(self.merge(proposed, expected=422))
            try:
                await asyncio.sleep(0.1)
                self.assertFalse(concurrent_merge.done())
                writer.add(OntologyRuntimeRecord(
                    definition_id=self.definition_id, key_digest="id-a", values={"id": "A", "note": "kept"},
                ))
                await writer.commit()
                blocked = await asyncio.wait_for(concurrent_merge, 5)
                self.assertIn("contain removed fields", str(blocked))
            finally:
                if not concurrent_merge.done():
                    concurrent_merge.cancel()
        self.assertEqual((await self.call("GET", self.route, 200))["revision"], self.head)

    async def test_restore_refuses_to_remove_new_property_values(self):
        await self.grant()
        extended = deepcopy(self.definition)
        extended["properties"].append({"id": "tracking", "name": "Tracking", "type": "string"})
        extended["actions"][0]["fields"].append("tracking")
        proposed = await self.propose(definition=extended)
        await self.vote(proposed)
        merged = await self.merge(proposed)
        async with AsyncSession(self.engine) as session:
            session.add(OntologyRuntimeRecord(definition_id=self.definition_id, key_digest="fixture", values={
                "id": "a", "tracking": "recorded after publication",
            }))
            await session.commit()
        response = await self.call("POST", f"{self.route}/revisions/{self.head}/restore-proposals", 422, {
            "summary": "Restore original properties", "revision": merged["mergedRevision"],
        })
        self.assertIn("contain removed fields", str(response))

    async def test_source_revision_rechecks_the_bound_table_identity_at_merge(self):
        table = "revision_" + uuid4().hex
        for actor in self.actors[:2]:
            actor.is_superuser = True
        async with AsyncSession(self.engine, expire_on_commit=False) as session:
            for actor in self.actors[:2]:
                session.add(actor)
            await session.execute(text("CREATE SCHEMA IF NOT EXISTS ts_ods"))
            await session.execute(text(f"CREATE TABLE ts_ods.{table} (id text PRIMARY KEY, note text)"))
            await session.commit()

        async def drop_source():
            async with self.engine.begin() as connection:
                await connection.execute(text(f"DROP TABLE IF EXISTS ts_ods.{table}"))

        self.addAsyncCleanup(drop_source)

        @asynccontextmanager
        async def warehouse_session():
            async with AsyncSession(self.engine) as session:
                yield session

        source_definition = {**self.definition, "id": "bound", "source": {"mode": "existing", "schema": "ts_ods", "table": table},
                             "actions": [], "security": {"mode": "inherit"},
                             "properties": [{**field, "sourceColumn": field["id"]} for field in self.definition["properties"]]}
        with patch.object(ontology_registry, "get_etl_layers", return_value=SimpleNamespace(session=warehouse_session)):
            draft = await self.call("POST", BASE + "/drafts", 201, source_definition)
            published = await self.call("POST", BASE + "/drafts/" + draft["id"] + "/publish", 200,
                                        {"revision": draft["revision"]})
            route = BASE + "/object-types/" + draft["id"]
            proposal = await self.call("POST", route + "/revision-proposals", 201, {
                "revision": published["revision"], "definition": {**source_definition, "name": "Source metadata change"},
                "summary": "Change source metadata",
            })
            proposal_route = route + "/revision-proposals/" + proposal["id"]
            await self.call("POST", proposal_route + "/review", 200, {"approve": True}, actor=1)
            async with self.engine.begin() as connection:
                await connection.execute(text(f"DROP TABLE ts_ods.{table}"))
                await connection.execute(text(f"CREATE TABLE ts_ods.{table} (id text PRIMARY KEY, note text)"))
            blocked = await self.call("POST", proposal_route + "/merge", 409, {"revision": published["revision"]})
            self.assertIn("bound warehouse source has changed", str(blocked))
            self.assertEqual((await self.call("GET", route + "/revision-workspace", 200))["revision"], published["revision"])
