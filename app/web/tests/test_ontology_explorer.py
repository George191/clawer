"""Published handoff keeps live queries, fixed lists and semantic identities distinct."""

import asyncio
import importlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.web.api.dependencies.session import get_db
from app.web.api.models import User
from app.web.api.models.ontology_explorer import ExplorerQuery
from app.web.api.models.ontology_registry import Definition, OntologyDefinitionRow
from app.web.api.models.ontology_runtime import OntologyRuntimeRecord
from app.web.api.routes.ontology_explorer import router
from app.web.core.security import ALGORITHM, JWTTokenGenerator
from app.web.services import ontology_explorer as explorer
from app.web.services import ontology_registry as registry
from app.web.tests.test_ontology_graph import graph_db


@pytest.fixture
async def published(graph_db):
    g = graph_db
    async with g.sessions() as session:
        def migrate(conn):
            migration = importlib.import_module("app.web.alembic.versions.87d6b142fab3_published_explorer_saved_items")
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                migration.downgrade()
                migration.upgrade()
        connection = await session.connection()
        await connection.run_sync(migrate)
        await session.commit()

    async def application_session():
        async with g.sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = application_session
    headers = lambda user: {"Authorization": "Bearer " + JWTTokenGenerator({"sub": str(user.id)}, ALGORITHM).access_token}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield g, client, headers(g.reader), headers(g.owner)


@pytest.mark.asyncio
async def test_query_uses_server_filtering_identity_sort_and_empty_scope(published):
    g, client, reader, _ = published
    path = f"/api/ontology/v1/object-types/{g.d[0]}/explorer/query"
    async with g.sessions() as session:
        definition = await session.get(OntologyDefinitionRow, g.d[0])
        definition.published_definition = {**definition.published_definition, "actions": [{"kind": "modify", "fields": ["amount"], "submitters": [{"kind": "group", "id": str(g.p)}]}]}
        await session.commit()
    assert (await client.post(path, json={})).status_code == 401
    query = {"filters": [{"field": "amount", "op": "gt", "value": 5}], "groupBy": "region", "sumField": "amount", "sorts": [{"field": "amount", "direction": "desc"}]}
    first = await client.post(path, headers=reader, json=query, params={"limit": 1})
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["count"] == 2 and data["sum"] == "30" and data["hasMore"]
    assert data["items"][0]["title"] == "Second"
    assert data["items"][0]["identity"] == {"$recordId": str(g.r[0][1])}
    assert "secret" not in data["items"][0]["properties"]
    assert data["actions"] == [{"kind": "modify", "fields": ["amount"]}]
    second = await client.post(path, headers=reader, json=query, params={"limit": 1, "offset": 1})
    assert second.json()["items"][0]["title"] == "First" and not second.json()["hasMore"]
    empty = await client.post(path, headers=reader, json={"keys": []})
    assert empty.json()["items"] == [] and empty.json()["count"] == 0
    wrong = await client.post(path, headers=reader, json={"keys": [{"key": "First"}]})
    assert wrong.status_code == 422
    hidden = await client.post(path, headers=reader, json={"sorts": [{"field": "secret", "direction": "asc"}]})
    assert hidden.status_code == 403
    other_type = await client.post(path, headers=reader, json={"keys": [{"$recordId": str(g.r[1][0])}]})
    assert other_type.json()["count"] == 0


@pytest.mark.asyncio
async def test_saved_work_dynamic_exploration_fixed_list_sharing_and_scope(published):
    g, client, reader, owner = published
    base = f"/api/ontology/v1/object-types/{g.d[0]}/explorer"
    dynamic = {"filters": [{"field": "amount", "op": "gt", "value": 5}]}
    saved = await client.post(base + "/saved-items", headers=reader, json={"kind": "exploration", "name": "Active", "content": {"query": dynamic, "view": "explore"}})
    assert saved.status_code == 201, saved.text
    exploration = saved.json()
    identities = [{"$recordId": str(g.r[0][0])}]
    saved_list = await client.post(base + "/saved-items", headers=reader, json={"kind": "list", "name": "Review", "content": {"keys": identities}})
    assert saved_list.status_code == 201, saved_list.text
    fixed = saved_list.json()
    item_path = base + "/saved-items/" + fixed["id"]
    assert (await client.get(item_path, headers=owner)).status_code == 404
    # A new matching object belongs to the exploration, never to the saved list.
    async with g.sessions() as session:
        session.add(OntologyRuntimeRecord(definition_id=g.d[0], key_digest="new", values={"key": "New", "amount": 40, "region": "EW", "secret": "private"}))
        await session.commit()
    opened = (await client.get(base + "/saved-items/" + exploration["id"], headers=reader)).json()
    current = await client.post(base + "/query", headers=reader, json=opened["content"]["query"])
    assert current.json()["count"] == 3
    opened_list = (await client.get(item_path, headers=reader)).json()
    current_list = await client.post(base + "/query", headers=reader, json={"keys": opened_list["content"]["keys"]})
    assert current_list.json()["count"] == 1 and current_list.json()["items"][0]["title"] == "First"
    races = await asyncio.gather(*(client.put(item_path + "/sharing", headers=reader, json={"version": fixed["version"], "shared": True}) for _ in range(2)))
    assert sorted(response.status_code for response in races) == [200, 409]
    shared = next(response.json() for response in races if response.status_code == 200)
    assert not (await client.get(item_path, headers=owner)).json()["owned"]
    assert (await client.get(item_path.replace(str(g.d[0]), str(g.d[1])), headers=reader)).status_code == 404
    stopped = await client.put(item_path + "/sharing", headers=reader, json={"version": shared["version"], "shared": False})
    assert stopped.status_code == 200 and (await client.get(item_path, headers=owner)).status_code == 404
    assert (await client.delete(item_path, headers=reader, params={"version": fixed["version"]})).status_code == 409
    assert (await client.delete(item_path, headers=reader, params={"version": stopped.json()["version"]})).status_code == 200
    assert (await client.get(item_path, headers=reader)).status_code == 404


@pytest.mark.asyncio
async def test_mapped_source_semantic_keys_server_query_policy_and_source_identity(published, monkeypatch):
    g, client, _, owner = published
    table_name = "explorer_" + uuid4().hex
    @asynccontextmanager
    async def warehouse():
        async with g.sessions() as session:
            yield session
    monkeypatch.setattr(explorer, "get_etl_layers", lambda: SimpleNamespace(session=warehouse))
    async with g.sessions() as session:
        await session.execute(text("CREATE SCHEMA IF NOT EXISTS ts_ods"))
        await session.execute(text(f'CREATE TABLE ts_ods."{table_name}" (tenant text NOT NULL, id bigint NOT NULL, label text, score integer, region text, created_at timestamp NOT NULL, updated_at timestamptz, PRIMARY KEY(tenant,id,created_at))'))
        await session.execute(text(f"INSERT INTO ts_ods.{table_name} SELECT '  Tenant A  ', 9007199254740992+value, 'Object ' || value, value, CASE WHEN value=102 THEN 'hidden' ELSE 'visible' END, TIMESTAMP '2026-11-21 01:02:03.123', TIMESTAMPTZ '2026-11-21 09:02:03.123+08' FROM generate_series(1,102) value"))
        actor = await session.get(User, g.owner.id)
        actor.is_superuser = True
        definition = Definition.model_validate({
            "id": "mapped", "name": "Mapped objects", "projectId": g.p,
            "source": {"mode": "existing", "schema": "ts_ods", "table": table_name},
            "properties": [{"id": key, "name": key, "type": type_, "sourceColumn": source, "required": key in {"tenant-key", "object-id", "moment"}} for key, type_, source in [("tenant-key", "string", "tenant"), ("object-id", "integer", "id"), ("name", "string", "label"), ("amount", "integer", "score"), ("zone", "string", "region"), ("moment", "datetime", "created_at"), ("instant", "datetime", "updated_at")]],
            "primaryKey": ["tenant-key", "object-id", "moment"], "titleKey": "name",
            "security": {"mode": "inherit", "recordRules": [{"kind": "property", "field": "zone", "match": "equals", "value": "visible", "readers": [{"kind": "group", "id": g.p}]}]},
        })
        catalog = await registry._warehouse_table(session, definition.source)
        doc = definition.model_dump(mode="json", by_alias=True)
        item = OntologyDefinitionRow(project_id=g.p, owner_id=g.owner.id, semantic_id="mapped", draft_definition=doc, published_definition=doc, published_at=datetime.now(timezone.utc), source_oid=catalog.oid, source_signature=registry.source_signature(definition, catalog))
        session.add(item)
        await session.commit()
        definition_id = item.id
    try:
        base = f"/api/ontology/v1/object-types/{definition_id}/explorer"
        result = await client.post(base + "/query", headers=owner, json={"filters": [{"field": "amount", "op": "gt", "value": 100}], "sumField": "amount"})
        assert result.status_code == 200, result.text
        data = result.json()
        assert data["count"] == 1 and data["sum"] == "101"
        assert data["items"][0]["properties"]["name"] == "Object 101"
        identity = {"tenant-key": "  Tenant A  ", "object-id": "9007199254741093", "moment": "2026-11-21 01:02:03.123"}
        assert data["items"][0]["id"] is None and data["items"][0]["identity"] == identity
        assert data["actions"] == []
        properties = data["items"][0]["properties"]
        assert properties["moment"] == "2026-11-21T01:02:03.123000"
        assert properties["instant"] == "2026-11-21T01:02:03.123000+00:00"
        for field, value in [("moment", properties["moment"]), ("instant", properties["instant"]), ("instant", "2026-11-21T09:02:03.123+08:00")]:
            equal = await client.post(base + "/query", headers=owner, json={"keys": [identity], "filters": [{"field": field, "op": "eq", "value": value}]})
            assert equal.status_code == 200 and equal.json()["count"] == 1, equal.text
        for field, value in [("moment", properties["instant"]), ("instant", properties["moment"])]:
            mismatch = await client.post(base + "/query", headers=owner, json={"filters": [{"field": field, "op": "eq", "value": value}]})
            assert mismatch.status_code == 422
        selected = await client.post(base + "/query", headers=owner, json={"keys": [identity]})
        assert selected.json()["count"] == 1
        unavailable = await client.post(base + "/query", headers=owner, json={"keys": [{**identity, "object-id": "9007199254741094"}]})
        assert unavailable.json()["count"] == 0
        wrong = await client.post(base + "/query", headers=owner, json={"keys": [{"id": "101"}]})
        assert wrong.status_code == 422
        saved = await client.post(base + "/saved-items", headers=owner, json={"kind": "list", "name": "Mapped review", "content": {"keys": [identity]}})
        assert saved.status_code == 201, saved.text
        assert (await client.post(base + "/query", headers=owner, json={"keys": []})).json()["count"] == 0
        async with g.sessions() as session:
            await session.execute(text(f'ALTER TABLE ts_ods.{table_name} ALTER COLUMN score TYPE bigint'))
            await session.commit()
        stale = await client.post(base + "/query", headers=owner, json={})
        assert stale.status_code == 409
    finally:
        async with g.sessions() as session:
            await session.execute(text(f'DROP TABLE ts_ods.{table_name}'))
            await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked_field", ["amount", "region"])
async def test_sharing_rechecks_query_and_chart_but_can_revoke_invalid_saved_work(published, revoked_field):
    g, client, reader, _ = published
    base = f"/api/ontology/v1/object-types/{g.d[0]}/explorer"
    saved = await client.post(base + "/saved-items", headers=reader, json={"kind": "exploration", "name": "Amounts", "content": {"query": {"filters": [{"field": "amount", "op": "gt", "value": 0}]}, "view": "explore", "chart": "region"}})
    assert saved.status_code == 201, saved.text
    item = saved.json()
    path = base + "/saved-items/" + item["id"]
    enabled = await client.put(path + "/sharing", headers=reader, json={"version": item["version"], "shared": True})
    assert enabled.status_code == 200
    version = enabled.json()["version"]
    async with g.sessions() as session:
        definition = await session.get(OntologyDefinitionRow, g.d[0])
        doc = definition.published_definition
        definition.published_definition = {**doc, "security": {**doc["security"], "fieldReaders": {**doc["security"]["fieldReaders"], revoked_field: [{"kind": "user", "id": str(g.owner.id)}]}}}
        await session.commit()
    assert (await client.put(path + "/sharing", headers=reader, json={"version": version, "shared": True})).status_code == 403
    assert (await client.get(path, headers=reader)).status_code == 403
    revoked = await client.put(path + "/sharing", headers=reader, json={"version": version, "shared": False})
    assert revoked.status_code == 200 and not revoked.json()["shared"]


def test_identity_strings_preserve_business_whitespace():
    assert ExplorerQuery(keys=[{"identifier": "  Object A  "}]).keys == [{"identifier": "  Object A  "}]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["list", "exploration"])
async def test_shared_saved_scopes_never_disclose_unreadable_record_identities(published, kind):
    g, client, reader, owner = published
    async with g.sessions() as session:
        definition = await session.get(OntologyDefinitionRow, g.d[0])
        doc = definition.published_definition
        definition.published_definition = {**doc, "security": {**doc["security"], "recordRules": [{"kind": "property", "field": "region", "match": "equals", "value": region, "readers": [{"kind": "user", "id": str(actor.id)}]} for region, actor in [("EW", g.reader), ("West", g.owner)]]}}
        await session.commit()
    base = f"/api/ontology/v1/object-types/{g.d[0]}/explorer"
    keys = [{"$recordId": str(g.r[0][2])}]
    content = {"keys": keys} if kind == "list" else {"query": {"keys": keys}}
    denied_save = await client.post(base + "/saved-items", headers=reader, json={"kind": kind, "name": "Unavailable scope", "content": content})
    assert denied_save.status_code == (422 if kind == "list" else 403)
    saved = await client.post(base + "/saved-items", headers=owner, json={"kind": kind, "name": "West review", "content": content})
    assert saved.status_code == 201, saved.text
    meta = saved.json()
    path = base + "/saved-items/" + meta["id"]
    enabled = await client.put(path + "/sharing", headers=owner, json={"version": meta["version"], "shared": True})
    assert enabled.status_code == 200, enabled.text
    assert (await client.get(path, headers=owner)).status_code == 200
    denied = await client.get(path, headers=reader)
    assert denied.status_code == 403 and "content" not in denied.json() and str(g.r[0][2]) not in denied.text
    async with g.sessions() as session:
        record = await session.get(OntologyRuntimeRecord, g.r[0][2])
        record.values = {**record.values, "region": "EW"}
        await session.commit()
    assert (await client.get(path, headers=owner)).status_code == 403
    assert (await client.put(path + "/sharing", headers=owner, json={"version": enabled.json()["version"], "shared": True})).status_code == 403
    assert (await client.delete(path, headers=owner, params={"version": enabled.json()["version"]})).status_code == 200
