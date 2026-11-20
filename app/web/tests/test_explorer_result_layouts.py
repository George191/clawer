"""Physical result layouts with real PostgreSQL, signed user sessions and the production panel."""

import asyncio
import importlib
import json
import os
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.responses import FileResponse, Response
from sqlalchemy import text

from app.web.api.routes import etl, explorer_layouts
from app.web.tests import test_published_api_keys as key_tests

publication_app = key_tests.publication_app


@pytest_asyncio.fixture
async def layouts(publication_app, monkeypatch):
    app, _, engine, *_ = publication_app
    migration = importlib.import_module("app.web.alembic.versions.78a1b2c3d4e5_explorer_result_layouts")

    def migrate(conn):
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.downgrade()
            migration.upgrade()

    async with engine.begin() as conn:
        await conn.run_sync(migrate)
        definition = await conn.scalar(text("""
            SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema()
              AND indexname = 'uq_explorer_layout_asset_owner'
        """))
        assert "(schema_name, table_name, owner_id) NULLS NOT DISTINCT" in definition
    app.include_router(explorer_layouts.router, prefix="/api/v1")
    layers = etl.get_etl_workspace()._layers
    monkeypatch.setattr(etl, "get_etl_layers", lambda: layers)
    monkeypatch.setattr(explorer_layouts, "get_etl_layers", lambda: layers)
    yield publication_app


@pytest.mark.asyncio
async def test_layouts_validate_fields_visibility_scope_and_concurrency(layouts):
    _, c, engine, schema, admin, reader, other, *_ = layouts
    path = f"/api/v1/etl/{schema}/items/result-layout"
    layout = {
        "columns": [
            {
                "field": f,
                "width": 160,
                "presentation": "media" if f == "category" else "value",
                "visible": f != "source",
            }
            for f in ("category", "source", "id")
        ],
        "sorts": [{"field": "id", "direction": "desc"}],
        "wrap": True,
    }
    first = {"version": None, "layout": layout}
    assert (await c.get(path)).status_code == 401
    assert (await c.put(path + "/default", headers=reader, json=first)).status_code == 403
    raced = await asyncio.gather(*(c.put(path + "/default", headers=admin, json=first) for _ in range(2)))
    assert sorted(r.status_code for r in raced) == [200, 409]
    original = (await c.get(path, headers=reader)).json()
    other_schema = schema + "_peer"
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{other_schema}"'))
        await conn.execute(text(f'CREATE TABLE "{other_schema}".items (id integer PRIMARY KEY)'))
    try:
        peer = await c.get(path.replace(schema, other_schema), headers=reader)
        assert peer.status_code == 200 and peer.json()["default"]["version"] is None
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{other_schema}" CASCADE'))
    assert original["personal"]["layout"] is None and original["default"]["layout"] == layout
    assert (
        await c.put(path + "/personal", headers=reader, json={**first, "owner_id": "other"})
    ).status_code == 422
    assert (
        await c.put(
            path + "/personal", headers=reader, json={"version": None, "layout": {**layout, "row": {"id": 2}}}
        )
    ).status_code == 422
    invalid_type = {**layout, "columns": [{**col, "presentation": "timeSeries"} for col in layout["columns"]]}
    assert (
        await c.put(path + "/personal", headers=reader, json={**first, "layout": invalid_type})
    ).status_code == 422
    saved = (await c.put(path + "/personal", headers=reader, json=first)).json()
    assert saved["version"] and (await c.get(path, headers=other)).json()["personal"]["version"] is None
    async with engine.begin() as conn:
        await conn.execute(
            text(
                f'ALTER TABLE "{schema}".items DROP COLUMN source, ADD COLUMN score int, ALTER COLUMN category TYPE int USING 1'
            )
        )
        await conn.execute(text(f'CREATE TABLE "{schema}".other_items (id int PRIMARY KEY)'))
    assert (await c.get(path.replace("/items/", "/other_items/"), headers=reader)).json()["default"][
        "version"
    ] is None
    adjusted = (await c.get(path, headers=reader)).json()["personal"]["layout"]
    assert adjusted["columns"][0]["presentation"] == "value"
    assert [col["field"] for col in adjusted["columns"]] == ["category", "id", "score"]
    assert (
        await c.put(path + "/personal", headers=reader, json={**first, "version": saved["version"]})
    ).status_code == 409
    clear = await c.put(
        path + "/personal", headers=reader, json={"version": saved["version"], "layout": None}
    )
    assert clear.status_code == 200 and clear.json()["version"] != saved["version"]
    assert (
        await c.put(path + "/personal", headers=reader, json={"version": saved["version"], "layout": None})
    ).status_code == 409
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """INSERT INTO public.ontology_objects(layer,schema_name,table_name,visibility)
                                   VALUES ('ods',:s,'items','hidden')"""
            ),
            {"s": schema},
        )
    try:
        assert (await c.get(path, headers=reader)).status_code == 404
        assert (
            await c.put(path + "/personal", headers=reader, json={"version": None, "layout": None})
        ).status_code == 404
        assert (await c.get(path, headers=admin)).status_code == 200
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM public.ontology_objects WHERE schema_name=:s"), {"s": schema}
            )


@asynccontextmanager
async def serve_panel(app, dist):
    import uvicorn

    @app.get("/{path:path}")
    async def panel(path: str):
        if path.startswith("api/"):
            return Response(status_code=404)
        child = (dist / path).resolve()
        return FileResponse(child if child.is_relative_to(dist) and child.is_file() else dist / "index.html")

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="error", lifespan="off"))
    running = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        await running
        sock.close()


@pytest.mark.asyncio
async def test_chromium_result_layouts(layouts):
    from playwright.async_api import async_playwright, expect

    directory = os.environ.get("PUBLISHED_API_UI_DIST")
    if not directory:
        pytest.skip("Set PUBLISHED_API_UI_DIST to the production panel build")
    app, _, engine, schema, admin, reader, *_ = layouts
    async with engine.begin() as conn:
        await conn.execute(text(f'ALTER TABLE "{schema}".items ADD COLUMN history jsonb'))
        await conn.execute(
            text(f"""UPDATE "{schema}".items SET history = '[["2024-01-01",1],["2024-02-01",4]]'::jsonb""")
        )
    async with serve_panel(app, Path(directory).resolve(strict=True)) as base, async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=os.environ.get("PUBLISHED_API_BROWSER"))

        async def open_page(headers):
            page = await browser.new_page(viewport={"width": 1450, "height": 920})
            token = json.dumps(headers["Authorization"].split()[1])
            await page.add_init_script(
                "localStorage.setItem('auth_session',JSON.stringify({access_token:"
                + token
                + ",last_activity:Date.now(),locked:false}));"
            )
            await page.goto(f"{base}/ontology/explorer?schema={schema}&table=items")
            await expect(page.get_by_role("button", name="Column actions for id", exact=True)).to_be_visible(
                timeout=25000
            )
            await expect(page.get_by_role("button", name="Configure columns")).to_be_enabled()
            return page

        page = await open_page(admin)

        async def menu(field, item):
            await page.get_by_role("button", name="Column actions for " + field, exact=True).click()
            await page.get_by_text(item, exact=True).last.click()

        await menu("id", "Sort descending")
        await expect(page.get_by_role("button", name="Column actions for id", exact=True)).to_contain_text(
            "1"
        )
        await menu("category", "Sort ascending")
        await expect(page.get_by_role("button", name="Column actions for id", exact=True)).to_contain_text(
            "2"
        )
        await menu("source", "Hide this column")
        await expect(page.get_by_role("button", name="Column actions for source")).to_have_count(0)
        await page.get_by_role("button", name="Configure columns").click()
        modal = page.get_by_role("dialog")
        await modal.get_by_role("searchbox").fill("source")
        await modal.get_by_label("source", exact=True).check()
        await modal.get_by_role("button", name="Move source to top").click()
        await modal.get_by_role("searchbox").fill("")
        rows = modal.locator("li")
        assert (await rows.first.inner_text()).find("source") >= 0
        await rows.filter(has_text="source").drag_to(rows.filter(has_text="id"))
        await modal.get_by_role("combobox", name="Display history as").click()
        await page.get_by_role("option", name="Time series", exact=True).click()
        await modal.get_by_label("Wrap text in cells").check()
        await modal.get_by_role("button", name="Save as table default").click()
        await expect(modal.get_by_role("status")).to_have_text("Table default saved.")
        await page.keyboard.press("Escape")
        await expect(page.locator(".result-cell-wrap").first).to_be_visible()
        await expect(page.get_by_role("img", name="Time series trend").first).to_be_visible()
        handle = page.get_by_role("separator", name="Resize category")
        measured_before = await handle.evaluate("e => e.closest('th').getBoundingClientRect().width")
        rect = await handle.bounding_box()
        x, y = rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
        await page.mouse.move(x, y)
        await page.mouse.down()
        await page.mouse.move(x + 75, y, steps=6)
        await page.mouse.up()
        width = int(await handle.get_attribute("aria-valuenow"))
        assert width >= 235, width
        measured_after = await handle.evaluate("e => e.closest('th').getBoundingClientRect().width")
        assert measured_after >= measured_before + 40, (measured_before, measured_after)
        await page.locator(".result-column-header").filter(has_text="category").drag_to(
            page.locator(".result-column-header").filter(has_text="history")
        )
        await page.get_by_role("button", name="Configure columns").click()
        await modal.get_by_role("button", name="Hide all").click()
        await modal.get_by_role("button", name="Show all").click()
        await modal.get_by_label("source", exact=True).uncheck()
        await modal.get_by_role("button", name="Save personal layout").click()
        await expect(modal.get_by_role("status")).to_have_text("Personal layout saved.")
        await page.reload()
        await expect(page.get_by_role("separator", name="Resize category")).to_have_attribute(
            "aria-valuenow", str(width)
        )
        await expect(page.get_by_role("button", name="Column actions for source")).to_have_count(0)
        member_page = await open_page(reader)
        await expect(member_page.get_by_role("button", name="Column actions for source")).to_be_visible()
        await member_page.get_by_role("button", name="Configure columns").click()
        member_modal = member_page.get_by_role("dialog")
        await expect(member_modal.get_by_role("button", name="Save as table default")).to_have_count(0)
        await member_modal.get_by_label("id", exact=True).uncheck()
        await member_modal.get_by_role("button", name="Save personal layout").click()
        await expect(member_modal.get_by_role("status")).to_have_text("Personal layout saved.")
        await member_modal.get_by_role("button", name="Restore table default").click()
        await expect(
            member_page.get_by_role("button", name="Column actions for id", exact=True)
        ).to_be_visible()
        await menu("category", "Clear all sorts")
        await expect(
            page.get_by_role("button", name="Column actions for id", exact=True)
        ).not_to_contain_text("2")
        await browser.close()
