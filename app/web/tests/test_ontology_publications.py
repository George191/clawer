"""Publication grants retain their saved scope and the publisher's live permissions."""
from datetime import datetime, timedelta, timezone
from importlib import import_module
from uuid import UUID

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
from sqlalchemy import select

from app.web.api.dependencies.common import get_current_user
from app.web.api.dependencies.session import get_db
from app.web.api.models import TeamUserJoin
from app.web.api.models.ontology_publications import OntologyPublication, PublicationInput
from app.web.api.models.ontology_registry import CatalogChange, OntologyDefinitionRow
from app.web.api.models.user import RoleTypes
from app.web.api.routes.ontology_publications import public_router, router
from app.web.api.routes.ontology_registry import change_catalog_state
from app.web.services import ontology_publications as service
from app.web.tests.test_ontology_graph import graph_db


@pytest.fixture
async def published_api(graph_db):
    g = graph_db
    async with g.sessions() as session:
        def migrate(connection):
            with Operations.context(MigrationContext.configure(connection)):
                import_module("app.web.alembic.versions.74a1b2c3d4e5_ontology_revisions").upgrade()
                import_module("app.web.alembic.versions.8e35b4e1c682_ontology_api_publications").upgrade()
        await (await session.connection()).run_sync(migrate)
        await session.commit()
    return g


def payload(**changes):
    now = datetime.now(timezone.utc)
    return PublicationInput.model_validate({
        "name": "Eastern records", "modelRevision": 1, "fields": ["key", "amount"],
        "query": {"filters": [{"field": "region", "op": "eq", "value": "EW"}]},
        "validFrom": now - timedelta(minutes=1), "validUntil": now + timedelta(days=1), **changes,
    })


async def issue(g, **changes):
    async with g.sessions() as session:
        publication = await service.save(session, g.owner, g.d[0], payload(**changes))
        key = await service.change_key(session, g.owner, g.d[0], publication["id"], False)
        return publication, key["key"]


@pytest.mark.asyncio
async def test_fixed_range_fields_pagination_and_hash_only_storage(published_api):
    g = published_api
    publication, key = await issue(g)
    async with g.sessions() as session:
        row = await session.get(OntologyPublication, publication["id"])
        assert key not in str(service.view(row)) and row.key_digest != key
        first = await service.read(session, row.id, key, 1, 0)
        assert first["hasMore"] and first["nextOffset"] == 1 and not first["truncated"]
        assert set(first["items"][0]) == {"key", "amount"}
        second = await service.read(session, row.id, key, 1, first["nextOffset"])
        assert second["nextOffset"] is None and not second["hasMore"] and not second["truncated"]
        assert {first["items"][0]["key"], second["items"][0]["key"]} == {"First", "Second"}


@pytest.mark.asyncio
async def test_boundary_page_reports_truncation(published_api, monkeypatch):
    g = published_api
    publication, key = await issue(g)
    monkeypatch.setattr(service, "MAX_PUBLICATION_OFFSET", 0)
    async with g.sessions() as session:
        bounded = await service.read(session, publication["id"], key, 1, 0)
        assert len(bounded["items"]) == 1 and bounded["truncated"]
        assert bounded["nextOffset"] is None and not bounded["hasMore"]


@pytest.mark.asyncio
async def test_hide_and_unhide_preserve_only_current_publications(published_api):
    g = published_api
    stale, stale_key = await issue(g)
    async with g.sessions() as session:
        model = await session.get(OntologyDefinitionRow, g.d[0])
        model.revision += 1
        await session.commit()
    current, key = await issue(g, modelRevision=2)
    async with g.sessions() as session:
        for revision, operation in enumerate(("hide", "unhide"), 2):
            await change_catalog_state(g.d[0], CatalogChange(revision=revision, operation=operation), session, g.owner)
            assert (await service.read(session, current["id"], key, 10, 0))["items"]
            with pytest.raises(HTTPException) as conflict:
                await service.read(session, stale["id"], stale_key, 10, 0)
            assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_rotation_revocation_expiry_and_wrong_keys(published_api):
    g = published_api
    publication, old = await issue(g)
    async with g.sessions() as session:
        fresh = await service.change_key(session, g.owner, g.d[0], publication["id"], False)
        for invalid in (old, "wrong", "sha256:fake"):
            with pytest.raises(HTTPException) as denied:
                await service.read(session, publication["id"], invalid, 50, 0)
            assert denied.value.status_code == 404
        await service.change_key(session, g.owner, g.d[0], publication["id"], True)
        with pytest.raises(HTTPException):
            await service.read(session, publication["id"], fresh["key"], 50, 0)
        row = await session.get(OntologyPublication, publication["id"])
        row.valid_from = datetime.now(timezone.utc) - timedelta(days=2)
        row.valid_until = datetime.now(timezone.utc) - timedelta(days=1)
        await session.commit()
        latest = await service.change_key(session, g.owner, g.d[0], row.id, False)
        with pytest.raises(HTTPException):
            await service.read(session, row.id, latest["key"], 50, 0)


@pytest.mark.asyncio
async def test_nonmanager_and_cross_definition_cannot_manage_keys(published_api):
    g = published_api
    publication, _ = await issue(g)
    async with g.sessions() as session:
        with pytest.raises(HTTPException) as denied:
            await service.save(session, g.reader, g.d[0], payload())
        assert denied.value.status_code == 403
        with pytest.raises(HTTPException) as absent:
            await service.change_key(session, g.owner, g.d[1], publication["id"], False)
        assert absent.value.status_code == 404


@pytest.mark.asyncio
async def test_model_changes_and_manager_demotion_invalidate_grants(published_api):
    g = published_api
    publication, key = await issue(g)
    async with g.sessions() as session:
        row = await session.get(OntologyDefinitionRow, g.d[0])
        row.revision += 1
        await session.commit()
        with pytest.raises(HTTPException) as changed:
            await service.read(session, publication["id"], key, 50, 0)
        assert changed.value.status_code == 409
        row.revision -= 1
        member = await session.scalar(select(TeamUserJoin).where(TeamUserJoin.team_id == g.p, TeamUserJoin.user_id == g.owner.id))
        member.role = RoleTypes.MEMBER
        await session.commit()
        with pytest.raises(HTTPException) as denied:
            await service.read(session, publication["id"], key, 50, 0)
        assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_scope_update_checks_versions_and_disabled_publisher_loses_access(published_api):
    g = published_api
    publication, key = await issue(g)
    async with g.sessions() as session:
        row = await session.get(OntologyPublication, publication["id"])
        version = row.version
        updated = await service.save(session, g.owner, g.d[0], payload(version=version,
            fields=["key"], query={"filters": [{"field": "key", "op": "eq", "value": "First"}]}), row.id)
        assert updated["version"] != version
        result = await service.read(session, row.id, key, 50, 0)
        assert result["items"] == [{"key": "First"}]
        with pytest.raises(HTTPException) as conflict:
            await service.save(session, g.owner, g.d[0], payload(version=version), row.id)
        assert conflict.value.status_code == 409
        with pytest.raises(HTTPException) as stale_model:
            await service.save(session, g.owner, g.d[0], payload(modelRevision=99), row.id)
        assert stale_model.value.status_code == 409
        from app.web.api.models import User
        from app.web.api.utils.models import StatusTypes
        owner = await session.get(User, g.owner.id)
        owner.status = StatusTypes.DISABLE
        await session.commit()
        with pytest.raises(HTTPException) as denied:
            await service.read(session, row.id, key, 50, 0)
        assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_public_route_needs_header_and_rejects_range_override(published_api):
    g = published_api
    publication, key = await issue(g)
    app = FastAPI()
    app.include_router(router)
    app.include_router(public_router)
    async def session():
        async with g.sessions() as value:
            yield value
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_user] = lambda: g.owner
    path = f"/ontology/v1/publications/{publication['id']}/data"
    assert publication["url"] == path
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
        assert (await client.get("/api" + path)).status_code == 404
        assert (await client.get(path)).status_code == 401
        for params in ({"fields": "secret"}, {"filters": "[]"}, {"search": "West"}, {"limit": 101}, {"offset": 100001}):
            assert (await client.get(path, params=params, headers={"X-API-Key": key})).status_code == 422
        result = await client.get(path, headers={"X-API-Key": key})
        assert result.status_code == 200 and result.headers["cache-control"] == "private, no-store"
        assert len(result.json()["items"]) == 2


def test_invalid_validity_duplicate_fields_and_aggregates_are_rejected():
    with pytest.raises(ValueError):
        payload(validUntil=datetime.now(timezone.utc) - timedelta(days=2))
    with pytest.raises(ValueError):
        payload(validFrom=datetime.now())
    with pytest.raises(ValueError):
        payload(fields=["key", "key"])
    with pytest.raises(ValueError):
        payload(query={"filters": [], "sumField": "amount"})
