"""Published media reads use semantic identities and the current field/record grants."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.web.api.dependencies.common import get_current_user
from app.web.api.dependencies.session import get_db
from app.web.api.routes.ontology_explorer_details import router
from app.web.services import explorer_files as files
from app.web.services import ontology_explorer_details as details
from app.web.tests.test_ontology_explorer import published
from app.web.tests.test_ontology_graph import graph_db


@pytest.mark.asyncio
async def test_published_file_signs_only_the_authorized_stored_semantic_value():
    identity = {"semantic_key": "007"}
    value = {"assets": [{"url": "news/source/report.pdf"}]}
    with patch.object(details, "_definition_access", AsyncMock(return_value=(None, None, None, {"assets"}))), \
         patch.object(details, "evaluate", AsyncMock(return_value={"count": 1, "items": [{"properties": value}], "actions": []})) as read, \
         patch.object(files, "signed_link", return_value="https://files.example/signed") as sign:
        result = await details.file(None, None, uuid4(), identity, "assets", download=False, path=[0, "url"])
    assert result == {"url": "https://files.example/signed"}
    assert read.call_args.args[3].keys == [identity]
    sign.assert_called_once_with("news/source/report.pdf", download=False)


@pytest.mark.asyncio
async def test_hidden_property_is_rejected_before_query_or_signing():
    with patch.object(details, "_definition_access", AsyncMock(return_value=(None, None, None, {"name"}))), \
         patch.object(details, "evaluate", AsyncMock()) as read, \
         patch.object(details, "record_file_link", AsyncMock()) as sign:
        with pytest.raises(HTTPException) as failure:
            await details.file(None, None, uuid4(), {"$recordId": str(uuid4())}, "secret", download=False, path=[])
    assert failure.value.status_code == 403
    read.assert_not_called()
    sign.assert_not_called()


@pytest.mark.asyncio
async def test_hidden_or_deleted_object_cannot_sign_a_resource():
    with patch.object(details, "_definition_access", AsyncMock(return_value=(None, None, None, {"assets"}))), \
         patch.object(details, "evaluate", AsyncMock(return_value={"count": 0, "items": []})), \
         patch.object(details, "record_file_link", AsyncMock()) as sign:
        with pytest.raises(HTTPException) as failure:
            await details.file(None, None, uuid4(), {"$recordId": str(uuid4())}, "assets", download=False, path=[])
    assert failure.value.status_code == 404
    sign.assert_not_called()


@pytest.mark.asyncio
async def test_semantic_html_receives_only_projected_authorized_fields():
    values = {"body": "<p>Authorized</p>"}
    with patch.object(details, "_definition_access", AsyncMock(return_value=(None, None, None, {"body"}))), \
         patch.object(details, "evaluate", AsyncMock(return_value={"count": 1, "items": [{"properties": values}], "actions": []})), \
         patch.object(details, "render_html", return_value={"html": "<p>Authorized</p>"}) as render:
        result = await details.html(None, None, uuid4(), {"$recordId": str(uuid4())}, "body")
    assert result["html"] == "<p>Authorized</p>"
    render.assert_called_once_with(values, "body")


@pytest.mark.asyncio
async def test_file_http_contract_rejects_caller_urls_wrong_identity_and_invalid_resource_paths():
    app = FastAPI()
    app.include_router(router, prefix="/types/{definition_id}/explorer")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())
    app.dependency_overrides[get_db] = lambda: None
    base = f"/types/{uuid4()}/explorer/record-file"
    payload = {"key": {"code": "007"}, "field": "asset", "download": False, "path": [0, "url"]}
    with patch.object(details, "file", AsyncMock(return_value={"url": "https://files.example/signed"})) as sign:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for body in ({**payload, "url": "https://outside.example"}, {**payload, "key": {"code": 7}}, {**payload, "key": {}}, {**payload, "path": [True]}, {**payload, "path": ["x"] * 13}):
                assert (await client.post(base, json=body)).status_code == 422
            sign.assert_not_called()
            result = await client.post(base, json=payload)
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "private, no-store"
    assert sign.call_args.args[3:] == ({"code": "007"}, "asset")
    assert sign.call_args.kwargs == {"download": False, "path": [0, "url"]}


@pytest.mark.asyncio
async def test_record_and_media_http_enforce_real_semantic_field_and_identity_grants(published):
    g, client, reader, _ = published
    base = f"/api/ontology/v1/object-types/{g.d[0]}/explorer"
    identity = {"$recordId": str(g.r[0][0])}
    with patch.object(files, "signed_link") as sign:
        result = await client.post(base + "/record", headers=reader, json={"key": identity})
        assert result.status_code == 200, result.text
        assert result.json()["identity"] == identity
        assert "secret" not in result.json()["properties"]
        assert "actions" in result.json()
        denied = await client.post(base + "/record-file", headers=reader, json={"key": identity, "field": "secret"})
        assert denied.status_code == 403
        wrong_type = await client.post(base + "/record-file", headers=reader, json={"key": {"$recordId": str(g.r[1][0])}, "field": "key"})
        assert wrong_type.status_code == 404
        invalid = await client.post(base + "/record", headers=reader, json={"key": {"key": "First"}})
        assert invalid.status_code == 422
        sign.assert_not_called()
