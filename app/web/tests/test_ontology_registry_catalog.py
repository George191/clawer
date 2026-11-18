"""Run the published catalog through its route handlers on an isolated PostgreSQL database."""

import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.web.api.routes.ontology_registry import published_type, published_types
from app.web.api.models.ontology_registry import Definition, OntologyDefinitionRow
from app.web.services import ontology_registry as registry
from app.web.services.ontology_registry_policy import AccessContext, Principal


@pytest.fixture
async def db():
    url = os.environ.get("ONTOLOGY_REGISTRY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires an isolated ontology_registry_regression PostgreSQL database")
    assert make_url(url).database == "ontology_registry_regression"
    engine = create_async_engine(url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_bound_record_page_orders_by_the_physical_key_when_an_alias_matches(db, monkeypatch):
    table = "aircraft_" + uuid4().hex
    definition = Definition.model_validate({
        "id": "aircraft", "name": "Aircraft", "pluralName": "Aircraft", "projectId": uuid4(),
        "source": {"mode": "existing", "schema": "ts_ods", "table": table},
        "properties": [
            {"id": "key", "name": "Key", "type": "integer", "sourceColumn": "id", "required": True},
            {"id": "id", "name": "Name", "type": "string", "sourceColumn": "name"},
        ], "primaryKey": ["key"], "titleKey": "id", "security": {"mode": "inherit"},
    })
    async with AsyncSession(db) as session:
        await session.execute(text("CREATE SCHEMA IF NOT EXISTS ts_ods"))
        await session.execute(text(f"CREATE TABLE ts_ods.{table} (id int PRIMARY KEY, name text)"))
        await session.execute(text(f"INSERT INTO ts_ods.{table} VALUES (1,'Zulu'), (2,'Alpha')"))
        catalog = await registry._warehouse_table(session, definition.source)
        assert not registry.assess_definition(definition, catalog).errors
        row = OntologyDefinitionRow(published_definition=definition.model_dump(mode="json", by_alias=True),
                                    source_oid=catalog.oid, source_signature=registry.source_signature(definition, catalog))

        @asynccontextmanager
        async def warehouse_session():
            yield session

        monkeypatch.setattr(registry, "get_etl_layers", lambda: SimpleNamespace(session=warehouse_session))
        assert await registry.read_bound_records(row, ["id", "key"], 1, 0) == {
            "items": [{"key": 1, "id": "Zulu"}], "hasMore": True,
        }
        reader = Principal(kind="user", id=uuid4())
        row.published_definition["security"]["recordRules"] = [{
            "kind":"property", "field":"id", "match":"equals", "value":"Alpha",
            "readers":[reader.model_dump(mode="json")],
        }]
        allowed = AccessContext(frozenset({reader}), True, True)
        assert await registry.read_bound_records(row, ["key"], 1, 0, context=allowed) == {
            "items": [{"key": 2}], "hasMore": False,
        }
        assert await registry.read_bound_records(row, ["key"], 1, 0) == {"items": [], "hasMore": False}


@pytest.mark.asyncio
async def test_published_catalog_pages_visible_matches_and_projects_fields(db):
    actor = SimpleNamespace(id=uuid4(), is_superuser=False)
    project, tenant, outsider = uuid4(), uuid4(), uuid4()
    async with AsyncSession(db) as session:
        for ddl in (
            "CREATE TEMP TABLE team (id uuid PRIMARY KEY, tenant_id uuid)",
            "SET LOCAL search_path TO pg_temp",
            "CREATE TYPE pg_temp.statustypes AS ENUM ('ENABLE','DISABLE')",
            "CREATE TEMP TABLE tenant (id uuid PRIMARY KEY, status statustypes)",
            'CREATE TEMP TABLE "user" (id uuid PRIMARY KEY, status statustypes)',
            "CREATE TEMP TABLE team_user_join (team_id uuid, user_id uuid, status statustypes)",
            "CREATE TEMP TABLE ontology_registry_definitions (id uuid PRIMARY KEY, project_id uuid,"
            " revision integer, published_at timestamptz, published_definition jsonb)",
        ):
            await session.execute(text(ddl))
        ids = {"u": actor.id, "p": project, "t": tenant}
        for insert in (
            "INSERT INTO team VALUES (:p,:t)", "INSERT INTO tenant VALUES (:t,'ENABLE')",
            'INSERT INTO "user" VALUES (:u,\'ENABLE\')', "INSERT INTO team_user_join VALUES (:p,:u,'ENABLE')",
        ):
            await session.execute(text(insert), ids)
        fixtures = [
            ("hidden", "user", outsider), ("one", "user", actor.id), ("two", "group", project),
            ("three", "organization", tenant), ("unmatched", "user", actor.id),
        ]
        row_ids = {}
        for position, (key, kind, reader) in enumerate(fixtures):
            row_ids[key] = uuid4()
            definition = {
                "id": key, "name": key, "description": "Mixed%_Case" if key != "unmatched" else "MixedABCase",
                "projectId": str(project), "source": {"mode": "none"},
                "properties": [{"id": name, "name": name, "type": "string"} for name in ("key", "secret")],
                "primaryKey": ["key"], "titleKey": "secret",
                "security": {"mode": "explicit", "objectReaders": [{"kind": kind, "id": str(reader)}],
                             "fieldReaders": {"secret": [{"kind": "user", "id": str(outsider)}]},
                             "recordRules": [{"kind": "property", "field": "secret", "match": "equals",
                                              "value": "private filter", "readers": [{"kind": kind, "id": str(reader)}]}]},
            }
            await session.execute(text("INSERT INTO ontology_registry_definitions VALUES "
                "(:id,:p,1,now() - CAST(:position AS int) * interval '1 minute',CAST(:data AS jsonb))"),
                {"id": row_ids[key], "p": project, "position": position, "data": json.dumps(definition)})
        page = await published_types(session, actor, 2, 0, "miXed%_")
        assert [item["definition"]["id"] for item in page["items"]] == ["one", "two"]
        assert page["hasMore"]
        last = await published_types(session, actor, 2, 2, "miXed%_")
        assert [item["definition"]["id"] for item in last["items"]] == ["three"]
        assert not last["hasMore"]
        item = await published_type(row_ids["one"], session, actor)
        assert item == page["items"][0]
        assert [prop["id"] for prop in item["definition"]["properties"]] == ["key"]
        assert item["definition"]["titleKey"] == ""
        assert item["definition"]["security"] == {"mode": "explicit", "objectReaders": [], "fieldReaders": {}}
        with pytest.raises(HTTPException) as hidden:
            await published_type(row_ids["hidden"], session, actor)
        assert hidden.value.status_code == 404
        # Administrative access still requires an enabled project membership.
        actor.is_superuser = True
        await session.execute(text("UPDATE team_user_join SET status='DISABLE'"))
        assert await published_types(session, actor, 2, 0, "") == {"items": [], "hasMore": False}
