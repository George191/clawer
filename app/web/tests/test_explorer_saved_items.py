"""Exercise saved explorations and exact object lists through authenticated HTTP."""

import asyncio
import importlib

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.web.api.routes import etl, explorer_saved_items
from app.web.tests.test_explorer_result_layouts import layouts, publication_app


@pytest_asyncio.fixture
async def saved_items(layouts, monkeypatch):
    app, c, engine, schema, admin, reader, other, *_ = layouts
    app.include_router(explorer_saved_items.router, prefix="/api/v1")
    monkeypatch.setattr(
        explorer_saved_items, "get_etl_layers", lambda: etl.get_etl_workspace()._layers
    )
    migration = importlib.import_module(
        "app.web.alembic.versions.80a1b2c3d4e5_explorer_saved_items"
    )

    def migrate(conn):
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()

    async with engine.begin() as conn:
        await conn.run_sync(migrate)
    return layouts


@pytest.mark.asyncio
async def test_saved_items_visibility_identity_and_sharing_race(saved_items):
    app, c, engine, schema, admin, reader, other, *_ = saved_items
    path = f"/api/v1/etl/{schema}/items/saved-items"
    data = {
        "kind": "exploration",
        "name": "Category counts",
        "content": {
            "chart": "category",
            "view": "explore",
            "sorts": [{"field": "id", "direction": "desc"}],
        },
    }
    assert (await c.post(path, json=data)).status_code == 401
    create = await c.post(path, headers=reader, json=data)
    assert create.status_code == 201, create.text
    meta = create.json()
    item = path + "/" + meta["id"]
    assert (await c.get(path, headers=reader, params={"kind": "exploration"})).json()["items"] == [
        meta
    ]
    assert (await c.get(item, headers=other)).status_code == 404
    assert (await c.get(item, headers=reader)).json()["content"]["chart"] == "category"
    share = {"shared": True, "version": meta["version"]}
    attempts = await asyncio.gather(
        *(c.put(item + "/sharing", headers=reader, json=share) for _ in range(2))
    )
    assert sorted(r.status_code for r in attempts) == [200, 409]
    version = next(r.json()["version"] for r in attempts if r.status_code == 200)
    assert (await c.get(item, headers=other)).json()["owned"] is False
    assert (
        await c.put(item + "/sharing", headers=other, json={"shared": False, "version": version})
    ).status_code == 409
    assert (
        await c.delete(item, headers=reader, params={"version": meta["version"]})
    ).status_code == 409
    assert (await c.delete(item, headers=reader, params={"version": version})).status_code == 200
    assert (await c.get(item, headers=other)).status_code == 404
    for key in ({"wrong": "2"}, {"id": "2", "extra": "0"}):
        assert (
            await c.post(
                path,
                headers=admin,
                json={"kind": "list", "name": "Bad", "content": {"keys": [key]}},
            )
        ).status_code == 422
    body = {"kind": "list", "name": "IDs", "content": {"keys": [{"id": "9007199254740993"}]}}
    result = await c.post(path, headers=admin, json=body)
    assert result.status_code == 201, result.text
    assert (await c.get(path + "/" + result.json()["id"], headers=admin)).json()["content"] == body[
        "content"
    ]
    assert (
        await c.post(
            path, headers=admin, json={**data, "content": {"filters": [{"field": ["bad"]}]}}
        )
    ).status_code == 422


@pytest.mark.asyncio
async def test_saved_list_preview_rechecks_table_kind_and_sharing(saved_items):
    _, c, engine, schema, admin, reader, other, *_ = saved_items
    base = f"/api/v1/etl/{schema}/items"
    saved = await c.post(base + "/saved-items", headers=reader, json={
        "kind": "list", "name": "Exact objects", "content": {"keys": [{"id": "1"}]},
    })
    assert saved.status_code == 201, saved.text
    meta = saved.json()
    query = {"savedListId": meta["id"], "withIdentity": True}
    assert (await c.post(base + "/preview-rows", json=query)).status_code == 401
    assert (await c.post(base + "/preview-rows", headers=other, json=query)).status_code == 404
    assert (await c.post(base + "/data", headers=reader, json=query)).status_code == 422
    own = await c.post(base + "/preview-rows", headers=reader, json=query)
    assert own.status_code == 200, own.text
    assert [row["id"] for row in own.json()["rows"]] == [1]
    item = base + "/saved-items/" + meta["id"]
    sharing = await c.put(item + "/sharing", headers=reader, json={"shared": True, "version": meta["version"]})
    assert sharing.status_code == 200, sharing.text
    shared = await c.post(base + "/preview-rows", headers=other, json=query)
    assert shared.status_code == 200 and shared.json()["rowIdentities"] == [{"id": "1"}]
    stopped = await c.put(item + "/sharing", headers=reader, json={"shared": False, "version": sharing.json()["version"]})
    assert stopped.status_code == 200, stopped.text
    assert (await c.post(base + "/preview-rows", headers=other, json=query)).status_code == 404
    exploration = await c.post(base + "/saved-items", headers=reader, json={
        "kind": "exploration", "name": "View", "content": {},
    })
    assert exploration.status_code == 201, exploration.text
    assert (await c.post(base + "/preview-rows", headers=reader, json={"savedListId": exploration.json()["id"]})).status_code == 404
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE TABLE "{schema}".other_items (id integer PRIMARY KEY)'))
        await conn.execute(text(f'INSERT INTO "{schema}".other_items VALUES (1)'))
    assert (await c.post(base.replace("/items", "/other_items") + "/preview-rows", headers=reader, json=query)).status_code == 404


@pytest.mark.asyncio
async def test_saved_list_preview_filters_sorts_pages_and_keeps_metadata(saved_items):
    _, c, engine, schema, admin, reader, other, *_ = saved_items
    base = f"/api/v1/etl/{schema}/items"
    saved = await c.post(base + "/saved-items", headers=admin, json={
        "kind": "list", "name": "Subset", "content": {"keys": [{"id": str(i)} for i in (1, 2, 3)]},
    })
    assert saved.status_code == 201, saved.text
    query = {
        "savedListId": saved.json()["id"], "forEditing": True, "withIdentity": True,
        "limit": 1, "sorts": [{"field": "id", "direction": "desc"}],
        "filters": [{"field": "category", "operator": "=", "value": "a"}],
    }
    first = await c.post(base + "/preview-rows", headers=admin, json=query)
    assert first.status_code == 200, first.text
    page = first.json()
    assert page["rows"] == [{"id": 3, "category": "a", "source": "local"}]
    assert page["columns"] == ["id", "category", "source"]
    assert page["primaryKey"] == ["id"] and page["columnTypes"]["id"] == "integer"
    assert page["rowIdentities"] == [{"id": "3"}] and len(page["rowVersions"][0].split(":")[1]) == 64
    assert page["rowActions"]["edit"] and page["hasMore"] and page["stableOrder"]
    second = await c.post(base + "/preview-rows", headers=admin, json={**query, "offset": 1})
    assert second.status_code == 200, second.text
    assert [row["id"] for row in second.json()["rows"]] == [1]
    assert second.json()["rowIdentities"] == [{"id": "1"}] and not second.json()["hasMore"]
    empty = await c.post(base + "/preview-rows", headers=admin, json={**query, "search": "secret"})
    assert empty.status_code == 200 and empty.json()["rows"] == []
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE explorer_saved_items SET content=CAST(:content AS jsonb) WHERE id=CAST(:id AS uuid)"), {
            "content": '{"keys":[]}', "id": saved.json()["id"],
        })
    emptied = await c.post(base + "/preview-rows", headers=admin, json={"savedListId": saved.json()["id"], "withIdentity": True})
    assert emptied.status_code == 200, emptied.text
    assert emptied.json()["rows"] == [] and not emptied.json()["hasMore"]
    assert emptied.json()["columns"] == ["id", "category", "source"]


@pytest.mark.asyncio
async def test_saved_list_preview_uses_complete_native_composite_keys(saved_items):
    _, c, engine, schema, admin, reader, other, *_ = saved_items
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE TABLE "{schema}".composite_items (tenant varchar(3), id bigint, media jsonb, PRIMARY KEY (tenant,id))'))
        await conn.execute(text(f'''INSERT INTO "{schema}".composite_items VALUES
            ('a',9007199254740993,'{{"url":"s3://private/image.png"}}'),
            ('a',2,'null'),('b',9007199254740993,'null'),('b',2,'null'),('abc',3,'null')'''))
    base = f"/api/v1/etl/{schema}/composite_items"
    keys = [{"tenant": "a", "id": "09007199254740993"}, {"tenant": "b", "id": "2"}, {"tenant": "abcdef", "id": "3"}]
    saved = await c.post(base + "/saved-items", headers=admin, json={"kind": "list", "name": "Composite", "content": {"keys": keys}})
    assert saved.status_code == 201, saved.text
    result = await c.post(base + "/preview-rows", headers=admin, json={"savedListId": saved.json()["id"], "withIdentity": True})
    assert result.status_code == 200, result.text
    page = result.json()
    assert page["rowIdentities"] == [{"tenant": "a", "id": "9007199254740993"}, {"tenant": "b", "id": "2"}]
    assert page["rows"][0]["id"] == "9007199254740993"
    assert page["rows"][0]["media"] == {"url": "s3://private/image.png"}
    assert page["primaryKey"] == ["tenant", "id"] and page["columnTypes"]["tenant"] == "character varying(3)"
    assert page["rowCount"] == 2 and not page["hasMore"]
    async with engine.begin() as conn:
        await conn.execute(text("UPDATE explorer_saved_items SET content=CAST(:content AS jsonb) WHERE id=CAST(:id AS uuid)"), {
            "content": '{"keys":[{"tenant":"a"}]}', "id": saved.json()["id"],
        })
    invalid = await c.post(base + "/preview-rows", headers=admin, json={"savedListId": saved.json()["id"]})
    assert invalid.status_code == 409


@pytest.mark.asyncio
async def test_saved_items_search_and_sharing_are_scoped_and_literal(saved_items):
    _, c, engine, schema, admin, reader, other, *_ = saved_items
    path = f"/api/v1/etl/{schema}/items/saved-items"
    first = None
    for headers, name in ((reader, "Alpha%_ exact"), (reader, "AlphaXY wildcard"), (other, "Alpha%_ private")):
        saved = await c.post(path, headers=headers, json={"kind": "exploration", "name": name, "content": {}})
        assert saved.status_code == 201, saved.text
        if first is None:
            first = saved.json()
    result = await c.get(path, headers=reader, params={"kind": "exploration", "search": "aLPHA%_", "sharing": "private"})
    assert result.status_code == 200 and [item["id"] for item in result.json()["items"]] == [first["id"]]
    assert not result.json()["hasMore"]
    sharing = await c.put(path + "/" + first["id"] + "/sharing", headers=reader, json={"shared": True, "version": first["version"]})
    assert sharing.status_code == 200, sharing.text
    private = await c.get(path, headers=reader, params={"kind": "exploration", "sharing": "private"})
    assert [item["name"] for item in private.json()["items"]] == ["AlphaXY wildcard"]
    shared = await c.get(path, headers=reader, params={"kind": "exploration", "sharing": "shared"})
    assert [item["id"] for item in shared.json()["items"]] == [first["id"]]
    assert (await c.get(path, headers=reader, params={"kind": "exploration", "sharing": "everyone"})).status_code == 422
