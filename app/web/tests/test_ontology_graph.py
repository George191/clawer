"""PostgreSQL regressions for link direction, visibility, cardinality and dynamic sets."""

import asyncio
import importlib
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.web.api.models import Team, TeamUserJoin, Tenant, User
from app.web.api.models.ontology_graph import LinkTypeInput, SavedSetInput, SetQuery
from app.web.api.models.ontology_registry import OntologyDefinitionRow
from app.web.api.models.ontology_runtime import OntologyRuntimeRecord
from app.web.api.models.user import InviteCodes, RoleTypes
from app.web.services import ontology_graph as graph
from app.web.services import ontology_graph_sets as sets


@pytest.fixture
async def graph_db():
    url = os.environ.get("ONTOLOGY_GRAPH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires an isolated ontology_graph_regression PostgreSQL database")
    assert make_url(url).database == "ontology_graph_regression"
    schema = "graph_" + uuid4().hex
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as c:
            await c.execute(text("CREATE SCHEMA " + schema))
            tables = Team.metadata.tables
            await c.run_sync(
                Team.metadata.create_all,
                tables=[
                    tables[k]
                    for k in (
                        "dictionaries",
                        "tenant",
                        "user",
                        "team",
                        "invite_codes",
                        "team_user_join",
                        "ontology_registry_definitions",
                        "ontology_runtime_records",
                    )
                ],
            )

            def migrate(conn):
                migration = importlib.import_module(
                    "app.web.alembic.versions.73a1b2c3d4e5_ontology_graph"
                )
                with Operations.context(MigrationContext.configure(conn)):
                    migration.upgrade()
                    migration.downgrade()
                    migration.upgrade()

            await c.run_sync(migrate)
        async with sessions() as s:
            owner = User(email="manager@example.org", phone="123", hashed_password="unused")
            reader = User(email="reader@example.org", phone="456", hashed_password="unused")
            tenant = Tenant(name="test")
            invite = InviteCodes(code="graph")
            team = Team(tenant_id=tenant.id, name="project")
            s.add_all([owner, reader, tenant, invite])
            await s.flush()
            other = Team(tenant_id=tenant.id, name="other")
            s.add_all([team, other])
            await s.flush()
            for u, role in [(owner, RoleTypes.OWNER), (reader, RoleTypes.MEMBER)]:
                for t in (team, other):
                    s.add(
                        TeamUserJoin(
                            team_id=t.id,
                            user_id=u.id,
                            role=role,
                            invite_by=owner.id,
                            invite_code=invite.id,
                        )
                    )
            records = []
            defs = []
            for name, t in [("source", team), ("target", team), ("external", other)]:
                group = {"kind": "group", "id": str(t.id)}
                manager = {"kind": "user", "id": str(owner.id)}
                properties = [
                    {"id": "key", "name": "Key", "type": "string", "required": True},
                    {"id": "amount", "name": "Amount", "type": "integer"},
                    {"id": "region", "name": "Region", "type": "string"},
                    {"id": "secret", "name": "Secret", "type": "string"},
                ]
                doc = {
                    "id": name,
                    "name": name,
                    "projectId": str(t.id),
                    "source": {"mode": "none"},
                    "properties": properties,
                    "primaryKey": ["key"],
                    "titleKey": "key",
                    "security": {
                        "mode": "explicit",
                        "objectReaders": [group],
                        "fieldReaders": {"secret": [manager]},
                        "recordRules": [
                            {
                                "kind": "property",
                                "field": "region",
                                "match": "equals",
                                "value": v,
                                "readers": roles,
                            }
                            for v, roles in (("EW", [group]), ("West", [manager]))
                        ],
                    },
                }
                row = OntologyDefinitionRow(
                    project_id=t.id,
                    owner_id=owner.id,
                    semantic_id=name,
                    draft_definition=doc,
                    published_definition=doc,
                    published_at=datetime.now(timezone.utc),
                )
                s.add(row)
                await s.flush()
                defs.append(row.id)
                data = [
                    OntologyRuntimeRecord(
                        definition_id=row.id,
                        key_digest=str(i),
                        values={"key": k, "amount": a, "region": v, "secret": "s"},
                    )
                    for i, (k, a, v) in enumerate(
                        [("First", 10, "EW"), ("Second", 20, "EW"), ("Third", 90, "West")]
                    )
                ]
                s.add_all(data)
                records.append([r.id for r in data])
            await s.commit()
        yield SimpleNamespace(
            sessions=sessions, owner=owner, reader=reader, p=team.id, d=defs, r=records
        )
    finally:
        async with engine.begin() as c:
            await c.execute(text("DROP SCHEMA " + schema + " CASCADE"))
        await engine.dispose()


def type_input(g, card="many_to_many", target=1):
    return LinkTypeInput(
        key="routes",
        name="Routes",
        sourceDefinitionId=g.d[0],
        targetDefinitionId=g.d[target],
        cardinality=card,
    )


@pytest.mark.asyncio
async def test_bidirectional_relationships_project_permissions_record_policy_and_undo_identity(
    graph_db,
):
    g = graph_db
    async with g.sessions() as s:
        for who, payload, code in [
            (g.reader, type_input(g), 403),
            (g.owner, type_input(g, target=2), 404),
        ]:
            with pytest.raises(HTTPException) as denied:
                await graph.create_type(s, who, g.p, payload)
            assert denied.value.status_code == code
            await s.rollback()
        rel = (await graph.create_type(s, g.owner, g.p, type_input(g)))["id"]
        visible = await graph.link_types(s, g.reader, g.d[0], 10, 0)
        assert visible["items"][0]["id"] == rel and not visible["canManage"]
        choices = await graph.link_targets(s, g.reader, g.p, "target", 1, 0)
        assert choices == {"items": [{"id": g.d[1], "name": "target"}], "hasMore": False}
        assert not (await graph.link_targets(s, g.reader, g.p, "external", 10, 0))["items"]

        async def edge(target, add=True, expected=None, actor=None):
            return await graph.mutate_edge(
                s, actor or g.owner, rel, g.r[0][0], target, add=add, expected_link_id=expected
            )

        with pytest.raises(HTTPException) as denied:
            await edge(g.r[1][0], actor=g.reader)
        assert denied.value.status_code == 403
        await s.rollback()
        first = await edge(g.r[1][0])
        await edge(g.r[1][2])
        await s.commit()
        out = await graph.neighbors(s, g.reader, rel, g.r[0][0], "outgoing", 10, 0)
        assert [link["linkId"] for link in out["items"]] == [first]
        assert "secret" not in out["items"][0]["record"].properties
        inc = await graph.neighbors(s, g.reader, rel, g.r[1][0], "incoming", 10, 0)
        assert inc["items"][0]["record"].id == g.r[0][0]
        with pytest.raises(HTTPException) as denied:
            await graph.neighbors(s, g.reader, rel, g.r[1][2], "incoming", 10, 0)
        assert denied.value.status_code == 404
        await edge(g.r[1][0], False, first)
        fresh = await edge(g.r[1][0])
        assert fresh != first
        with pytest.raises(HTTPException) as stale:
            await edge(g.r[1][0], False, first)
        assert stale.value.status_code == 409
        await s.rollback()


@pytest.mark.asyncio
async def test_undo_unlink_checks_the_last_removed_edge(graph_db):
    g = graph_db
    async with g.sessions() as session:
        rel = (await graph.create_type(session, g.owner, g.p, type_input(g, "many_to_many")))["id"]
        endpoints = (rel, g.r[0][0], g.r[1][0])

        async def change(add, expected=None):
            link_id = await graph.mutate_edge(
                session, g.owner, *endpoints, add=add, expected_link_id=expected
            )
            await session.commit()
            return link_id

        first = await change(True)
        await change(False, first)
        replacement = await change(True)
        assert replacement != first
        await change(False, replacement)
        with pytest.raises(HTTPException) as stale:
            await change(True, first)
        assert stale.value.status_code == 409
        await session.rollback()
        assert not (await graph.neighbors(session, g.owner, rel, g.r[0][0], "outgoing", 5, 0))[
            "items"
        ]
        restored = await change(True, replacement)
        assert restored not in {first, replacement}
        assert (await graph.neighbors(session, g.owner, rel, g.r[0][0], "outgoing", 5, 0))["items"][
            0
        ]["linkId"] == restored


@pytest.mark.asyncio
async def test_concurrent_cardinality_and_deleted_endpoint(graph_db):
    g = graph_db
    async with g.sessions() as s:
        rel = (await graph.create_type(s, g.owner, g.p, type_input(g, "many_to_one")))["id"]

    async with g.sessions() as other_project, g.sessions() as writer:
        foreign_id = g.r[2][0]
        await other_project.execute(
            text("SELECT id FROM ontology_runtime_records WHERE id = :id FOR UPDATE"),
            {"id": foreign_id},
        )
        await writer.execute(text("SET LOCAL lock_timeout = '500ms'"))
        with pytest.raises(HTTPException) as outside:
            await graph.mutate_edge(writer, g.owner, rel, g.r[0][0], foreign_id, add=True)
        assert outside.value.status_code == 404

    async def add(rid):
        async with g.sessions() as s:
            try:
                await graph.mutate_edge(s, g.owner, rel, g.r[0][0], rid, add=True)
                await s.commit()
                return 201
            except HTTPException as e:
                return e.status_code

    assert sorted(await asyncio.gather(*(add(i) for i in g.r[1][:2]))) == [201, 409]
    async with g.sessions() as s:
        result = await graph.neighbors(s, g.owner, rel, g.r[0][0], "outgoing", 10, 0)
        old = result["items"][0]["record"].id
        record = await s.get(OntologyRuntimeRecord, old)
        record.deleted = True
        await s.commit()
        assert not (await graph.neighbors(s, g.owner, rel, g.r[0][0], "outgoing", 10, 0))["items"]
        await graph.mutate_edge(
            s, g.owner, rel, g.r[0][0], next(i for i in g.r[1][:2] if i != old), add=True
        )
        await s.commit()


@pytest.mark.asyncio
async def test_dynamic_set_filters_aggregation_pagination_recheck_and_field_protection(graph_db):
    g = graph_db
    async with g.sessions() as s:
        query = SetQuery.model_validate(
            {
                "filters": [{"field": "amount", "op": "gt", "value": 5}],
                "groupBy": "region",
                "sumField": "amount",
            }
        )
        item = await sets.create_set(s, g.reader, g.d[0], SavedSetInput(name="Active", query=query))
        assert await graph.graph_referenced_fields(s, g.d[0]) == {"amount", "region"}
        assert not (await sets.list_sets(s, g.owner, g.d[0], 10, 0))["items"]
        with pytest.raises(HTTPException) as unavailable:
            await sets.saved(s, g.owner, g.d[0], item["id"])
        assert unavailable.value.status_code == 404
        result = await sets.evaluate(s, g.reader, g.d[0], query, 1, 0, 0)
        assert result["hasMore"] and result["count"] == 2 and result["sum"] == "30"
        assert result["groups"] == [{"value": "EW", "count": 2, "sum": "30"}]
        assert "secret" not in result["items"][0].properties
        changed = await s.get(OntologyRuntimeRecord, g.r[0][0])
        changed.values = {**changed.values, "amount": 0}
        await s.commit()
        saved = await sets.saved(s, g.reader, g.d[0], item["id"])
        result = await sets.evaluate(
            s, g.reader, g.d[0], SetQuery.model_validate(saved.query), 1, 0, 0
        )
        assert result["count"] == 1 and result["sum"] == "20"
        for key in ["groupBy", "sumField"]:
            with pytest.raises(HTTPException) as hidden:
                await sets.evaluate(
                    s, g.reader, g.d[0], SetQuery.model_validate({key: "secret"}), 10, 0, 0
                )
            assert hidden.value.status_code == 403
        filtered = await sets.evaluate(
            s,
            g.reader,
            g.d[0],
            SetQuery.model_validate(
                {
                    "filters": [
                        {"field": "region", "op": "eq", "value": "EW"},
                        {"field": "key", "op": "contains", "value": "irst"},
                    ]
                }
            ),
            10,
            0,
            0,
        )
        assert filtered["count"] == 1 and filtered["items"][0].title == "First"


@pytest.mark.asyncio
async def test_related_record_grant_is_reevaluated_for_traversal_and_set_counts(graph_db):
    g = graph_db
    async with g.sessions() as session:
        link_type = (await graph.create_type(session, g.owner, g.p, type_input(g)))["id"]
        await graph.mutate_edge(session, g.owner, link_type, g.r[0][0], g.r[1][0], add=True)
        await session.commit()
        row = await session.get(OntologyDefinitionRow, g.d[0])
        policy = {
            **row.published_definition["security"],
            "recordRules": [
                {
                    "kind": "related",
                    "linkType": "routes",
                    "direction": "outgoing",
                    "relatedRecordId": str(g.r[1][0]),
                    "readers": [{"kind": "group", "id": str(g.p)}],
                },
            ],
        }
        row.published_definition = {**row.published_definition, "security": policy}
        await session.commit()
        first = await sets.evaluate(session, g.reader, g.d[0], SetQuery(), 10, 0, 0)
        assert first["count"] == 1 and first["items"][0].id == g.r[0][0]
        neighbors = await graph.neighbors(
            session, g.reader, link_type, g.r[1][0], "incoming", 10, 0
        )
        assert [item["record"].id for item in neighbors["items"]] == [g.r[0][0]]
        await graph.mutate_edge(session, g.owner, link_type, g.r[0][0], g.r[1][0], add=False)
        await session.commit()
        assert (await sets.evaluate(session, g.reader, g.d[0], SetQuery(), 10, 0, 0))["count"] == 0
        with pytest.raises(HTTPException) as absent:
            await graph.neighbors(session, g.reader, link_type, g.r[0][0], "outgoing", 10, 0)
        assert absent.value.status_code == 404
