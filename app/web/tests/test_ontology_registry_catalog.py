"""Run the published catalog through its route handlers on an isolated PostgreSQL database."""

import json
import os
from pathlib import Path
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from app.web.api.routes.ontology_registry import change_catalog_state, published_type, published_types, type_groups
from app.web.api.models.ontology_registry import CatalogChange, Definition, OntologyDefinitionRow
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
async def test_catalog_state_migration_preserves_existing_types(db):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    migration = ScriptDirectory.from_config(config).get_revision("86d5a031f9b2")
    assert migration.down_revision == "81a1b2c3d4e5"
    async with db.begin() as connection:
        await connection.execute(text("CREATE TEMP TABLE ontology_registry_definitions (id integer PRIMARY KEY)"))
        await connection.execute(text("INSERT INTO ontology_registry_definitions VALUES (11)"))
        def exercise(conn):
            with Operations.context(MigrationContext.configure(conn)):
                migration.module.upgrade()
                assert conn.execute(text("SELECT * FROM ontology_registry_definitions")).one() == (11, False, False)
                migration.module.downgrade()
                assert conn.execute(text("SELECT * FROM ontology_registry_definitions")).one() == (11,)
                migration.module.upgrade()
                assert conn.execute(text("SELECT * FROM ontology_registry_definitions")).one() == (11, False, False)
        await connection.run_sync(exercise)


@pytest.mark.asyncio
async def test_bound_record_page_orders_by_the_physical_key_when_an_alias_matches(db, monkeypatch):
    table = "aircraft_" + uuid4().hex
    definition = Definition.model_validate({
        "id": "aircraft", "name": "Aircraft", "pluralName": "Aircraft", "projectId": uuid4(),
        "source": {"mode": "existing", "schema": "ts_dim", "table": table},
        "properties": [
            {"id": "key", "name": "Key", "type": "integer", "sourceColumn": "id", "required": True},
            {"id": "id", "name": "Name", "type": "string", "sourceColumn": "name"},
        ], "primaryKey": ["key"], "titleKey": "id", "security": {"mode": "inherit"},
    })
    async with AsyncSession(db) as session:
        await session.execute(text("CREATE SCHEMA IF NOT EXISTS ts_dim"))
        await session.execute(text(f"CREATE TABLE ts_dim.{table} (id int PRIMARY KEY, name text)"))
        await session.execute(text(f"INSERT INTO ts_dim.{table} VALUES (1,'Zulu'), (2,'Alpha')"))
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
            "CREATE TYPE pg_temp.roletypes AS ENUM ('MEMBER','MODERATOR','ADMIN','OWNER')",
            "CREATE TEMP TABLE team_user_join (team_id uuid, user_id uuid, status statustypes, role roletypes DEFAULT 'MEMBER')",
            "CREATE TEMP TABLE ontology_markings (id uuid PRIMARY KEY, project_id uuid, readers jsonb)",
            "CREATE TEMP TABLE ontology_publications (id uuid PRIMARY KEY, definition_id uuid, revision integer)",
            "CREATE TEMP TABLE ontology_registry_definitions (id uuid PRIMARY KEY, project_id uuid,"
            " revision integer, published_at timestamptz, published_definition jsonb, hidden boolean DEFAULT false,"
            " deleted boolean DEFAULT false, updated_at timestamptz)",
            "CREATE TEMP TABLE ontology_runtime_records (id uuid PRIMARY KEY, definition_id uuid,"
            " deleted boolean DEFAULT false, revision integer DEFAULT 1)",
            "CREATE TEMP TABLE ontology_revision_snapshots (definition_id uuid, revision integer, snapshot jsonb,"
            " actor_id uuid, proposal_id uuid, created_at timestamptz, PRIMARY KEY (definition_id, revision))",
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
                "groups": ["Private"] if key == "hidden" else ["Aviation", "Tags%_1"],
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
        assert page["hasMore"] and page["total"] == 3
        last = await published_types(session, actor, 2, 2, "miXed%_")
        assert await type_groups(session, actor, "") == {"items": ["Aviation", "Tags%_1"]}
        assert await type_groups(session, actor, "%_") == {"items": ["Tags%_1"]}
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
        async def change(operation, revision):
            return await change_catalog_state(row_ids["one"], CatalogChange(operation=operation, revision=revision), session, actor)

        with pytest.raises(HTTPException) as denied:
            await change("delete", 1)
        assert denied.value.status_code == 404
        await session.execute(text("UPDATE team_user_join SET role='ADMIN'"))
        assert (await change("hide", 1))["hidden"]
        assert (await published_types(session, actor, 2, 0, "miXed%_"))["total"] == 2
        hidden_page = await published_types(session, actor, 2, 0, "miXed%_", includeHidden=True)
        assert hidden_page["total"] == 3 and hidden_page["items"][0]["hidden"]
        with pytest.raises(HTTPException) as conflict:
            await change("delete", 1)
        assert conflict.value.status_code == 409
        await session.execute(text("INSERT INTO ontology_runtime_records VALUES "
            "(:active,:definition,false,1),(:deleted,:definition,true,2),(:other,:other_definition,false,1)"),
            {"active": uuid4(), "deleted": uuid4(), "other": uuid4(),
             "definition": row_ids["one"], "other_definition": row_ids["two"]})
        assert (await change("delete", 2))["deleted"]
        records = text("SELECT deleted, revision FROM ontology_runtime_records WHERE definition_id=:id ORDER BY revision")
        assert (await session.execute(records, {"id": row_ids["one"]})).all() == [(True, 2), (True, 2)]
        assert (await session.execute(records, {"id": row_ids["two"]})).all() == [(False, 1)]
        with pytest.raises(HTTPException) as removed:
            await published_type(row_ids["one"], session, actor)
        assert removed.value.status_code == 404
        assert (await published_types(session, actor, 2, 0, "miXed%_", includeHidden=True, includeDeleted=True))["items"][0]["deleted"]
        await session.execute(text("UPDATE team_user_join SET role='MEMBER'"))
        assert (await published_types(session, actor, 2, 0, "miXed%_", includeHidden=True, includeDeleted=True))["total"] == 2
        with pytest.raises(HTTPException) as denied_restore:
            await change("restore", 3)
        assert denied_restore.value.status_code == 404
        await session.execute(text("UPDATE team_user_join SET role='OWNER'"))
        restored = await change("restore", 3)
        assert restored["hidden"] and not restored["deleted"]
        assert (await session.execute(records, {"id": row_ids["one"]})).all() == [(False, 3), (False, 3)]
        assert (await session.execute(records, {"id": row_ids["two"]})).all() == [(False, 1)]
        assert not (await change("unhide", 4))["hidden"]
        assert (await published_types(session, actor, 2, 999, "miXed%_")) == {"items": [], "hasMore": False, "total": 3}

        connection_id, other_connection = uuid4(), uuid4()
        for name, upstream, readers in (
            ("workspace", None, []),
            ("connection", connection_id, []),
            ("other-connection", other_connection, []),
            ("restricted-connection", connection_id, [{"kind": "user", "id": str(outsider)}]),
        ):
            definition = {
                "id": name, "name": name, "projectId": str(project),
                "source": {"mode": "existing", "schema": "ts_ods", "table": "example",
                           "upstreamConnectionId": str(upstream) if upstream else None},
                "security": {"mode": "explicit" if readers else "inherit", "objectReaders": readers},
            }
            await session.execute(text("INSERT INTO ontology_registry_definitions VALUES "
                "(:id,:p,1,now(),CAST(:data AS jsonb))"),
                {"id": uuid4(), "p": project, "data": json.dumps(definition)})
        assert (await published_types(session, actor, 2, 0, "", source=connection_id))["total"] == 0
        # Administrative access still requires an enabled project membership.
        actor.is_superuser = True
        for source_filter, expected in ((connection_id, "connection"), (other_connection, "other-connection"), ("warehouse", "workspace")):
            scoped = await published_types(session, actor, 2, 0, "example", projectId=project, source=source_filter)
            assert scoped["total"] == 1
            assert [item["definition"]["id"] for item in scoped["items"]] == [expected]
        assert (await published_types(session, actor, 2, 0, "", source="object-managed"))["total"] == 4
        assert await published_types(session, actor, 2, 999, "", source=connection_id) == {"items": [], "hasMore": False, "total": 1}
        await session.execute(text("UPDATE team_user_join SET status='DISABLE'"))
        for source_filter in (None, connection_id):
            assert await published_types(session, actor, 2, 0, "", source=source_filter) == {"items": [], "hasMore": False, "total": 0}
