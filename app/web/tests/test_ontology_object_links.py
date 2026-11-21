"""Logical-edge contracts fail explicitly without changing permissions or creating storage."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.etl.schema.object_links import canonical_object_key, object_key_hash
from app.web.api.models.etl import ExplorerObjectLinkCreate, ExplorerObjectLinkRemove
from app.web.services import ontology_object_links as links
from app.web.services.explorer_rows import _safe_plan


def test_canonical_keys_keep_exact_string_values_and_ignore_dictionary_order():
    key = {"tenant": "中国", "id": "9007199254740993", "decimal": "1.23000000000000000001"}
    assert canonical_object_key(key) == '{"decimal":"1.23000000000000000001","id":"9007199254740993","tenant":"中国"}'
    assert object_key_hash(key) == object_key_hash(dict(reversed(list(key.items()))))
    assert object_key_hash({"id": "01"}) != object_key_hash({"id": "1"})
    for invalid in ({}, {"id": 9007199254740993}, {"id": None}, {"id": True}, {"id": {"nested": "x"}}, {1: "value"}):
        with pytest.raises(ValueError):
            canonical_object_key(invalid)


def test_mutation_contract_requires_complete_bounded_keys_direction_and_exact_versions():
    valid = {"key": {"id": "1"}, "targetKey": {"tenant": "a", "id": "9007199254740993"},
             "relationId": "1", "direction": "incoming", "expectedVersion": "2"}
    assert ExplorerObjectLinkRemove.model_validate(valid).expectedVersion == "2"
    for field, value in (("relationId", 1), ("relationId", str(2**63)), ("expectedVersion", 2),
                         ("expectedVersion", "0"), ("expectedVersion", str(2**63)), ("direction", "both"),
                         ("targetKey", {}), ("targetKey", {"id": "x" * 4097})):
        with pytest.raises(ValidationError):
            ExplorerObjectLinkRemove.model_validate({**valid, field: value})
    with pytest.raises(ValidationError):
        ExplorerObjectLinkCreate.model_validate({key: value for key, value in valid.items() if key not in {"expectedVersion", "direction"}})


def test_row_locks_do_not_weaken_large_heap_index_requirements():
    index = {"Node Type": "Index Scan", "Index Cond": "id = 1"}
    assert _safe_plan([{"Plan": {"Node Type": "Limit", "Plans": [{"Node Type": "LockRows", "Plans": [index]}]}}])
    for unsafe in ({"Node Type": "Seq Scan"}, {**index, "Filter": "visible"}, {"Node Type": "Bitmap Heap Scan"}):
        assert not _safe_plan([{"Plan": {"Node Type": "LockRows", "Plans": [unsafe]}}])


@pytest.mark.asyncio
async def test_missing_store_reports_uninitialized_without_attempting_ddl():
    session = SimpleNamespace(scalar=AsyncMock(return_value=False), execute=AsyncMock())
    with pytest.raises(HTTPException) as failure:
        await links.require_object_links(session)
    assert failure.value.status_code == 503
    assert "not initialized" in failure.value.detail
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_mutation_denies_readers_before_connecting_or_locking_any_rows():
    with patch("app.web.services.etl_workspace.get_etl_workspace") as workspace:
        with pytest.raises(HTTPException) as failure:
            await links.mutate_row_link("ts_ods", "asset", None, SimpleNamespace(is_superuser=False))
    assert failure.value.status_code == 403
    workspace.assert_not_called()


@pytest.mark.asyncio
async def test_missing_row_keeps_the_existing_physical_mutation_conflict_contract():
    with patch.object(links, "_prepare", AsyncMock()), \
         patch.object(links, "_asset", AsyncMock(return_value={"primary": ["id"]})), \
         patch.object(links, "_key", AsyncMock(side_effect=HTTPException(404, "Record not found"))):
        with pytest.raises(HTTPException) as failure:
            await links.protect_linked_row(None, "ts_ods", "asset", {"id": "1"})
    assert failure.value.status_code == 409
    assert failure.value.detail["code"] == "row_version_conflict"


@pytest.mark.asyncio
async def test_hidden_assets_keep_their_visibility_denial_in_the_row_guard():
    with patch.object(links, "_prepare", AsyncMock()), \
         patch.object(links, "_asset", AsyncMock(side_effect=HTTPException(404, "Table unavailable"))):
        with pytest.raises(HTTPException) as failure:
            await links.protect_linked_row(None, "ts_ods", "asset", {"id": "1"})
    assert failure.value.status_code == 404


@pytest.mark.asyncio
async def test_objects_without_primary_keys_cannot_be_normalized_for_edges():
    with pytest.raises(HTTPException) as failure:
        await links._key(None, {"primary": []}, {"id": "1"})
    assert failure.value.status_code == 409
    assert "no primary key" in failure.value.detail
