"""Exercise saved explorations and exact object lists through authenticated HTTP."""

import asyncio
import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.web.api.routes import etl, explorer_saved_items
from app.web.tests.test_explorer_result_layouts import layouts, publication_app


@pytest.mark.asyncio
async def test_saved_items_visibility_identity_and_sharing_race(layouts, monkeypatch):
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
