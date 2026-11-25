from collections import Counter
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.web.services import etl_layers


def connection(layer, engine="postgresql", *, database=None):
    return {"id": f"{layer}-connection", "name": f"private-{layer}", "engine": engine,
            "database": database or f"{layer}-database"}


def registered(layer, schema, data_type, *, role="current", active=True, hidden=False, without_ddl=False):
    return {"layer": layer, "schema_name": schema, "table_name": f"{layer}_{data_type}",
            "data_type": data_type, "table_role": role, "is_active": active,
            "visibility": "hidden" if hidden else "normal", "without_ddl": without_ddl}


def layer_service(monkeypatch, bindings, rows):
    async def execute(statement):
        if "to_regclass" in str(statement):
            return SimpleNamespace(scalar_one=lambda: True)
        assert "FROM app.ontology_objects WHERE is_active" in str(statement)
        assert "FROM active WHERE visibility <> 'hidden'" in str(statement)
        active = [row for row in rows if row["is_active"]]
        type_counts = Counter((row["layer"], row["table_role"], row["data_type"]) for row in active)
        mongo_orbital = {
            (row["layer"], row["schema_name"]) for row in active
            if row["table_role"] == "current" and row["data_type"] == "orbital" and row["without_ddl"]
        }
        visible = [
            dict(row, type_count=type_counts[(row["layer"], row["table_role"], row["data_type"])],
                 has_mongo_orbital=(row["layer"], row["schema_name"]) in mongo_orbital)
            for row in active if row["visibility"] != "hidden"
        ]
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: visible))

    @asynccontextmanager
    async def session():
        yield SimpleNamespace(execute=execute)

    metadata = SimpleNamespace(session=session)
    monkeypatch.setattr(etl_layers, "layer_connection_info", AsyncMock(return_value=bindings))
    return etl_layers.EtlLayers(metadata_pg=metadata)


def edges(graph):
    return {(edge.sourceLayer, edge.targetLayer) for edge in graph.dependencies}


def test_handler_data_types_match_worker_discovery():
    from app.etl.layer_dependencies import DWD_DATA_TYPES, TASK_DATA_TYPES
    from app.etl.ts_dwd import TsDwd
    from app.etl.ts_task import TsTask

    assert set(TsTask()._handlers) == TASK_DATA_TYPES
    assert set(TsDwd()._handlers) == DWD_DATA_TYPES


async def test_layer_lineage_http_uses_authorized_reader_and_private_cache(monkeypatch):
    from app.web.api.routes import etl

    service = layer_service(monkeypatch, {"ads": connection("ads")}, [])
    monkeypatch.setattr(etl, "get_etl_layers", lambda: service)
    app = FastAPI()
    app.include_router(etl.read, prefix="/etl")
    app.dependency_overrides[etl.get_current_user] = lambda: SimpleNamespace(is_superuser=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/etl/lineage/layers")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json() == {"layers": [], "dependencies": []}

        app.dependency_overrides[etl.get_current_user] = lambda: SimpleNamespace(is_superuser=True)
        response = await client.get("/etl/lineage/layers")
        assert response.status_code == 200
        assert response.json()["layers"][0]["sourceName"] == "private-ads"


async def test_layer_lineage_reports_cross_connection_dependencies_and_limits_metadata(monkeypatch):
    bindings = {name: connection(name) for name in ("rds", "ods", "task", "dwd", "dim", "ads")}
    rows = [registered(name, "same.name", "patent") for name in ("rds", "ods", "task", "dwd")]
    rows.extend([
        registered("dws", "summary", "sales"),
        registered("ods", "hidden_schema", "secret", hidden=True),
        registered("ods", "inactive_schema", "deleted", active=False),
        registered("ads", "private_schema", "secret", hidden=True),
    ])
    service = layer_service(monkeypatch, bindings, rows)

    ordinary = await service.layer_lineage(can_manage=False)
    by_layer = {layer.layer: layer for layer in ordinary.layers}
    assert set(by_layer) == {"rds", "ods", "task", "dwd", "dws"}
    assert by_layer["rds"].connectionId == "rds-connection"
    assert by_layer["ods"].connectionId == "ods-connection"
    assert by_layer["ods"].engine == "postgresql"
    assert by_layer["ods"].database == "ods-database"
    assert by_layer["ods"].sourceName is None
    assert by_layer["ods"].objectCount == 1
    assert [item.model_dump() for item in by_layer["ods"].schemas] == [{"schemaName": "same.name", "tables": 1}]
    assert by_layer["dws"].connectionId is None

    admin = await service.layer_lineage(can_manage=True)
    admin_by_layer = {layer.layer: layer for layer in admin.layers}
    assert admin_by_layer["ods"].sourceName == "private-ods"
    for name in ("dim", "ads"):
        assert admin_by_layer[name].objectCount == 0
        assert admin_by_layer[name].schemas == []
    assert edges(ordinary) == edges(admin) == {("rds", "ods"), ("ods", "task"), ("ods", "dwd"), ("task", "dwd")}


async def test_dependencies_require_supported_registered_input_and_output(monkeypatch):
    bindings = {name: connection(name) for name in ("rds", "ods", "task", "dwd")}
    rows = [
        registered("rds", "raw", "vulnerability"),
        registered("ods", "curated", "vulnerability"),
        registered("ods", "hidden_schema", "patent", hidden=True),
        registered("task", "task", "vulnerability", role="history"),
        registered("task", "task", "patent"),
        registered("dwd", "detail", "patent"),
    ]
    service = layer_service(monkeypatch, bindings, rows)
    assert edges(await service.layer_lineage(can_manage=True)) == {("rds", "ods")}


async def test_hidden_duplicate_type_disqualifies_postgres_dependency(monkeypatch):
    bindings = {name: connection(name) for name in ("rds", "ods", "task")}
    service = layer_service(monkeypatch, bindings, [
        registered("rds", "raw", "patent"),
        registered("ods", "curated", "patent"),
        registered("ods", "private_schema", "patent", hidden=True),
        registered("task", "task", "patent"),
    ])
    graph = await service.layer_lineage(can_manage=False)
    assert edges(graph) == set()
    assert next(layer for layer in graph.layers if layer.layer == "ods").objectCount == 1


async def test_mongodb_uses_collection_identity_and_rejects_hidden_orbital(monkeypatch):
    monkeypatch.setattr(etl_layers.etl_settings, "etl_output_mode", "debezium")
    bindings = {"rds": connection("rds", "mongodb", database="raw"), "ods": connection("ods")}
    rows = [
        registered("rds", "raw", "patent", without_ddl=True),
        dict(registered("rds", "raw", "patent", without_ddl=True), table_name="second_patent_collection"),
        registered("ods", "ods", "patent"),
    ]
    service = layer_service(monkeypatch, bindings, rows)
    assert edges(await service.layer_lineage(can_manage=False)) == {("rds", "ods")}
    rows.append(registered("rds", "raw", "orbital", hidden=True, without_ddl=True))
    assert edges(await service.layer_lineage(can_manage=False)) == set()


@pytest.mark.parametrize(("engine", "output_mode", "registered_schema", "without_ddl", "kind", "expected"), [
    ("mongodb", "debezium", "raw", True, "patent", True),
    ("mongodb", "direct", "raw", True, "patent", False),
    ("mongodb", "debezium", "elsewhere", True, "patent", False),
    ("mongodb", "debezium", "raw", False, "patent", False),
    ("mongodb", "debezium", "raw", True, "orbital", False),
    ("milvus", "debezium", "raw", True, "patent", False),
    (None, "debezium", "raw", False, "patent", False),
])
async def test_rds_source_binding_eligibility(
    monkeypatch, engine, output_mode, registered_schema, without_ddl, kind, expected,
):
    monkeypatch.setattr(etl_layers.etl_settings, "etl_output_mode", output_mode)
    bindings = {"ods": connection("ods")}
    if engine:
        bindings["rds"] = connection("rds", engine, database="raw")
    service = layer_service(monkeypatch, bindings, [
        registered("rds", registered_schema, kind, without_ddl=without_ddl),
        registered("ods", "ods", "patent"),
    ])
    result = await service.layer_lineage(can_manage=False)
    assert edges(result) == ({("rds", "ods")} if expected else set())
