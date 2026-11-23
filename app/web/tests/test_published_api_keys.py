"""Validate real public reads and API-key management through their authenticated HTTP routes."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi_pagination import add_pagination
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.web.api.dependencies.session import get_db
from app.web.api.models import RoleTypes, Team, TeamUserJoin, Tenant, User
from app.web.api.models.user import InviteCodes
from app.web.api.routes import apikey, etl, user as users
from app.web.core.security import ALGORITHM, JWTTokenGenerator
from app.web.services.etl_workspace import EtlWorkspace
from app.web.services.etl_layers import EtlLayers
from app.etl.schema.ontology import ONTOLOGY_OBJECTS_DDL


@pytest_asyncio.fixture
async def publication_app(monkeypatch):
    url = os.environ.get("PUBLISHED_API_TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "Requires an isolated ontology_published_api_regression PostgreSQL database"
        )
    parsed = make_url(url)
    assert parsed.database == "ontology_published_api_regression" and parsed.host in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    schema = "ts_keytest_" + uuid4().hex[:12]
    engine = create_async_engine(
        url, connect_args={"server_settings": {"search_path": schema + ",public"}}
    )
    workspace = EtlWorkspace()

    async def application_session():
        async with AsyncSession(engine) as s:
            yield s

    @asynccontextmanager
    async def db_session(**kwargs):
        async with AsyncSession(engine) as s:
            async with s.begin():
                yield s

    workspace._session = db_session
    workspace._layers = EtlLayers()
    workspace._layers.session = db_session
    monkeypatch.setattr(etl, "get_etl_workspace", lambda: workspace)
    monkeypatch.setattr(apikey, "get_etl_workspace", lambda: workspace)
    app = FastAPI()
    for router in (etl.router, apikey.router, users.router):
        app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = application_session
    add_pagination(app)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE SCHEMA " + schema))
            await conn.execute(text(ONTOLOGY_OBJECTS_DDL))
            await conn.run_sync(
                Team.metadata.create_all,
                tables=[
                    Team.metadata.tables[n]
                    for n in (
                        "dictionaries",
                        "tenant",
                        "team",
                        "user",
                        "invite_codes",
                        "team_user_join",
                        "apikey",
                    )
                ],
            )
            await conn.execute(
                text(
                    f'CREATE TABLE "{schema}".items(id int PRIMARY KEY, category text, source text)'
                )
            )
            await conn.execute(
                text(
                    f"""INSERT INTO "{schema}".items VALUES (3,'a','local'),(1,'a','local'),
                                        (2,'b','secret'),(4,'a','local'),(5,'a','other')"""
                )
            )
        async with AsyncSession(engine, expire_on_commit=False) as s:
            org = Tenant(name="Key test")
            admin = User(
                email="admin@example.org",
                phone="11",
                hashed_password="unused",
                is_superuser=True,
            )
            member = User(
                email="member@example.org", phone="12", hashed_password="unused"
            )
            friend = User(
                email="friend@example.org", phone="13", hashed_password="unused"
            )
            invite = InviteCodes(code="test")
            s.add_all([org, admin, member, friend, invite])
            await s.flush()
            team = Team(name="First", tenant_id=org.id)
            other = Team(name="Other", tenant_id=org.id)
            s.add_all([team, other])
            await s.flush()
            for person in (admin, member, friend):
                for group in (team, other):
                    s.add(
                        TeamUserJoin(
                            team_id=group.id,
                            user_id=person.id,
                            invite_by=admin.id,
                            invite_code=invite.id,
                            role=(
                                RoleTypes.OWNER if person is admin else RoleTypes.MEMBER
                            ),
                        )
                    )
            await s.commit()

            def auth(person):
                return {
                    "Authorization": "Bearer "
                    + JWTTokenGenerator({"sub": str(person.id)}, ALGORITHM).access_token
                }

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://qa"
        ) as client:
            yield app, client, engine, schema, auth(admin), auth(member), auth(
                friend
            ), str(team.id), str(other.id)
    finally:
        async with engine.begin() as conn:
            if await conn.scalar(
                text("SELECT to_regclass('public.flow_publications')")
            ):
                await conn.execute(
                    text("DELETE FROM public.flow_publications WHERE schema_name=:s"),
                    {"s": schema},
                )
            await conn.execute(text("DROP SCHEMA IF EXISTS " + schema + " CASCADE"))
        await engine.dispose()


async def create_publication(client, schema, admin, **changes):
    now = datetime.now(timezone.utc)
    res = await client.post(
        f"/api/v1/etl/{schema}/items/apis",
        headers=admin,
        json={
            "validFrom": (now - timedelta(days=1)).isoformat(),
            "validUntil": (now + timedelta(days=1)).isoformat(),
            "filterSource": ["local"],
            "filters": [{"field": "category", "operator": "=", "value": "a"}],
            "sorts": [{"field": "category", "direction": "desc"}],
            **changes,
        },
    )
    assert res.status_code == 200, res.text
    publication = res.json()
    assert publication["keyStatus"] == "unissued" and "token" not in publication
    return publication


@pytest.mark.asyncio
async def test_storage_publication_names_and_rename(publication_app, monkeypatch):
    _, client, _, schema, admin, member, _, _, _ = publication_app
    catalog = f"/api/v1/etl/{schema}/items/apis"
    legacy = await create_publication(client, schema, admin)
    assert "name" not in legacy
    publication = await create_publication(client, schema, admin, name="  客户目录  ")
    assert publication["name"] == "客户目录"
    publish_id = publication["id"]
    manage = f"{catalog}/{publish_id}"
    assert (await client.patch(manage, headers=member, json={"name": "Blocked"})).status_code == 403
    assert (await client.patch(
        f"/api/v1/etl/{schema}/other/apis/{publish_id}", headers=admin, json={"name": "Wrong table"}
    )).status_code == 404
    invalid = ({}, {"name": None}, {"name": 42}, {"name": "   "}, {"name": "x" * 121},
               {"name": "Invalid\0value"}, {"name": "Name", "filters": []})
    for payload in invalid:
        assert (await client.patch(manage, headers=admin, json=payload)).status_code == 422

    issued = await client.post(f"/api/v1/apikey/publications/{publish_id}/rotate", headers=admin)
    assert issued.status_code == 200
    key = issued.json()["key"]
    layers = etl.get_etl_workspace()._layers
    data = layers.data

    async def rename_during_read(*args, **kwargs):
        assert "name" not in kwargs["scope"]
        result = await data(*args, **kwargs)
        renamed = await client.patch(manage, headers=admin, json={"name": "  Renamed catalog  "})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json() == {**publication, "name": "Renamed catalog", "keyStatus": "active"}
        assert key not in renamed.text
        return result

    with monkeypatch.context() as patch:
        patch.setattr(layers, "data", rename_during_read)
        read = await client.get(publication["url"], headers={"X-API-Key": key})
    assert read.status_code == 200, read.text
    assert [row["id"] for row in read.json()["rows"]] == [1, 3, 4]

    settings = {field: publication[field] for field in ("validFrom", "validUntil", "filterSource", "filters", "sorts")}
    updated = await client.put(manage, headers=admin, json={**settings, "search": "local"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Renamed catalog"
    assert updated.json()["search"] == "local"
    listed = {item["id"]: item for item in (await client.get(catalog, headers=admin)).json()}
    assert "name" not in listed[legacy["id"]]
    assert listed[publish_id] == updated.json()


@pytest.mark.asyncio
async def test_public_scope_pagination_rotation_and_expiry(publication_app):
    _, client, engine, schema, admin, member, _, _, _ = publication_app
    publication = await create_publication(client, schema, admin)
    api = publication["url"]
    center = "/api/v1/apikey/publications"
    key_path = center + "/" + publication["id"]
    assert (await client.get(api)).status_code == 401
    assert (await client.get(center, headers=member)).status_code == 403
    assert (await client.post(key_path + "/rotate", headers=member)).status_code == 403
    assert (await client.get(center, headers=admin, params={"q": schema})).json()[
        "items"
    ][0]["id"] == publication["id"]
    key = (await client.post(key_path + "/rotate", headers=admin)).json()["key"]
    bearer = {"X-API-Key": key}
    page = (await client.get(api, headers=bearer, params={"limit": 2})).json()
    assert (
        [r["id"] for r in page["rows"]] == [1, 3]
        and page["hasMore"]
        and page["nextOffset"] == 2
        and page["stableOrder"]
    )
    last = (
        await client.get(
            api, headers=bearer, params={"limit": 2, "offset": page["nextOffset"]}
        )
    ).json()
    assert (
        [r["id"] for r in last["rows"]] == [4]
        and last["nextOffset"] is None
        and not last["hasMore"]
    )
    filters = '[{"field":"id","value":2,"join":"or"},{"field":"id","value":4}]'
    sliced = await client.get(
        api, headers=bearer, params={"filters": filters, "search": "local"}
    )
    assert [r["id"] for r in sliced.json()["rows"]] == [
        4
    ] and "rowVersions" not in sliced.json()
    for bad in [
        {"filters": "x"},
        {"offset": 1000001},
        {"forEditing": "true"},
        {"partition": "items"},
    ]:
        assert (await client.get(api, headers=bearer, params=bad)).status_code == 422
    assert (
        await client.get(
            api, headers=bearer, params={"filters": '[{"field":"unknown","value":"a"}]'}
        )
    ).status_code == 400
    listed = await client.get(f"/api/v1/etl/{schema}/items/apis", headers=admin)
    assert (
        key not in listed.text
        and key not in (await client.get(center, headers=admin)).text
    )
    async with engine.begin() as conn:
        raw = await conn.scalar(
            text("SELECT token FROM public.flow_publications WHERE id=:id"),
            {"id": publication["id"]},
        )
        assert raw.startswith("sha256:") and key not in raw
    assert (await client.get(api, headers={"X-API-Key": raw})).status_code == 404
    assert (await client.get("/api/v1/etl/published/" + key)).status_code == 404
    fresh = (await client.post(key_path + "/rotate", headers=admin)).json()["key"]
    assert (await client.get(api, headers=bearer)).status_code == 404
    assert (await client.get(api, headers={"X-API-Key": fresh})).status_code == 200
    assert (await client.delete(key_path + "/key", headers=admin)).status_code == 200
    assert (await client.get(api, headers={"X-API-Key": fresh})).status_code == 404
    old = "pk_live_" + uuid4().hex
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE public.flow_publications SET token=:k WHERE id=:id"),
            {"id": publication["id"], "k": old},
        )
    assert (await client.get("/api/v1/etl/published/" + old)).status_code == 200
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE public.flow_publications SET valid_until=now()-interval '1 second' WHERE id=:id"
            ),
            {"id": publication["id"]},
        )
    assert (await client.get("/api/v1/etl/published/" + old)).status_code == 404


@pytest.mark.asyncio
async def test_team_key_lifecycle_and_scope(publication_app):
    _, c, _, _, admin, member, friend, team, other = publication_app
    route = "/api/v1/apikey/"
    made = await c.post(
        route,
        headers=member,
        params={"team_id": team},
        json={"description": "Private integration"},
    )
    assert made.status_code == 200, made.text
    key = made.json()
    for actor, target, expected in [
        (member, team, 1),
        (friend, team, 0),
        (admin, other, 0),
    ]:
        got = await c.get(route + target, headers=actor)
        assert got.status_code == 200, got.text
        assert len(got.json()["items"]) == expected and key["key"] not in got.text
    assert (
        await c.post(
            route + key["id"] + "/rotate", headers=friend, params={"team_id": team}
        )
    ).status_code == 404
    assert (
        await c.delete(route + key["id"], headers=admin, params={"team_id": other})
    ).status_code == 404
    changed = await c.post(
        route + key["id"] + "/rotate", headers=member, params={"team_id": team}
    )
    assert changed.json()["key"] != key["key"]
    assert (
        await c.delete(route + key["id"], headers=member, params={"team_id": team})
    ).status_code == 200


@pytest.mark.asyncio
async def test_published_api_management_in_chromium(publication_app):
    import asyncio
    import json
    import socket
    from pathlib import Path
    from fastapi.responses import FileResponse
    import uvicorn
    from playwright.async_api import async_playwright, expect

    dist_dir = os.environ.get("PUBLISHED_API_UI_DIST")
    if not dist_dir:
        pytest.skip(
            "Set PUBLISHED_API_UI_DIST to the built panel to exercise the browser"
        )
    dist = Path(dist_dir).resolve(strict=True)
    app, client, _, schema, admin, member, _, _, _ = publication_app
    publication = await create_publication(client, schema, admin)

    @app.get("/{path:path}")
    async def static_site(path: str):
        file = (dist / path).resolve()
        if path.startswith("api/"):
            from fastapi.responses import Response

            return Response(status_code=404)
        return FileResponse(
            file
            if file.is_relative_to(dist) and file.is_file()
            else dist / "index.html"
        )

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(
        uvicorn.Config(app, access_log=False, log_level="error", lifespan="off")
    )
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                executable_path=os.environ.get("PUBLISHED_API_BROWSER"), headless=True
            )
            page = await browser.new_page(viewport={"width": 1360, "height": 900})
            page.on("dialog", lambda dialog: dialog.accept())

            async def login(headers):
                token = json.dumps(headers["Authorization"].split()[1])
                await page.add_init_script(
                    "localStorage.setItem('auth_session', JSON.stringify({access_token:"
                    + token
                    + ",last_activity:Date.now(),locked:false}));"
                )
                await page.goto(
                    base
                    + "/account/settings?tab=security&publication="
                    + publication["id"]
                )

            await login(admin)
            card = page.get_by_role("region", name="API key management")
            published = card.get_by_role("region", name="Published API keys")
            team = card.get_by_role("region", name="Team API keys")
            try:
                await expect(published.get_by_text(schema + ".items")).to_be_visible(timeout=15000)
            except AssertionError:
                # No key has been issued at this point.
                print("Initial security page:", await page.title(), (await page.locator("body").inner_text())[-1400:])
                raise

            async def consume(key):
                response = await page.request.get(
                    base + publication["url"],
                    headers={"X-API-Key": key},
                    params={"limit": 2},
                )
                if response.status == 200:
                    assert [row["id"] for row in (await response.json())["rows"]] == [
                        1,
                        3,
                    ]
                return response.status

            await published.get_by_role("button", name="Issue key").click()
            secret = card.get_by_label("New API key")
            first = await secret.input_value()
            assert await consume(first) == 200
            await card.get_by_role("button", name="Dismiss key").click()
            await expect(secret).to_have_count(0)
            await published.get_by_role("button", name="Rotate key").click()
            second = await secret.input_value()
            assert await consume(first) == 404 and await consume(second) == 200
            await published.get_by_role("button", name="Revoke key").click()
            await expect(published.get_by_text("revoked", exact=True)).to_be_visible()
            assert await consume(second) == 404
            await expect(secret).to_have_count(0)
            search = published.get_by_label("Find a publication")
            await search.fill("no-such-publication")
            await published.get_by_role("button", name="Search publications").click()
            await expect(
                published.get_by_text("No publications on this page.")
            ).to_be_visible()
            await page.route(
                "**/apikey/publications?*",
                lambda route: route.fulfill(status=503, json={"detail": "Maintenance"}),
            )
            await search.fill(schema)
            await published.get_by_role("button", name="Search publications").click()
            await expect(published.get_by_role("alert")).to_contain_text("Maintenance")
            await page.unroute("**/apikey/publications?*")
            await published.get_by_role("button", name="Retry").click()
            await expect(published.get_by_text(schema + ".items")).to_be_visible()
            await team.get_by_label("Key description").fill("Browser integration")
            await team.get_by_role("button", name="Create team key").click()
            await expect(
                team.get_by_text("Browser integration", exact=True)
            ).to_be_visible()
            await team.get_by_role("button", name="Rotate key").click()
            await team.get_by_role("button", name="Revoke key").click()
            await expect(team.get_by_text("No team keys on this page.")).to_be_visible()
            await page.set_viewport_size({"width": 390, "height": 844})
            await card.scroll_into_view_if_needed()
            metrics = await card.evaluate(
                "(el) => [el.scrollWidth, el.clientWidth, el.getBoundingClientRect().width]"
            )
            assert metrics[0] <= metrics[1] + 1 and metrics[2] <= 390, metrics
            await page.close()
            page = await browser.new_page()
            await login(member)
            await expect(
                page.get_by_role("region", name="Team API keys")
            ).to_be_visible()
            await expect(
                page.get_by_role("region", name="Published API keys")
            ).to_have_count(0)
            await browser.close()
    finally:
        server.should_exit = True
        await serving
        sock.close()
