"""Property presentation reads business structure with system metadata parameters."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web.api.dependencies.common import get_current_user
from app.web.api.routes import etl, explorer_properties
from app.web.services.etl_layers import EtlLayers


@pytest.mark.parametrize("admin", [False, True])
async def test_property_mapping_uses_registered_connection_without_business_metadata_tables(admin):
    connection = "27f17556-bc61-417e-ba4d-b7dcb213308f"
    target = {"schemaName": "ts_ods", "registryId": "42", "connectionId": connection}
    columns = [{"name": "id", "type": "bigint", "comment": "", "primary": True, "primaryPosition": 1}]
    warehouse = SimpleNamespace(runtime_table=AsyncMock(return_value=target), columns=AsyncMock(return_value=columns))
    calls = []

    class Session:
        async def execute(self, statement, parameters):
            sql = str(statement)
            # The business database deliberately has no app metadata schema.
            assert "app.ontology_objects" not in sql
            assert "app.ontology_object_properties" not in sql
            assert "jsonb_to_recordset" in sql
            assert parameters["object_metadata"] == "[]"
            assert parameters["property_metadata"] == "[]"
            calls.append((sql, parameters))
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [
                {"description": "Patents", "data_type": "patent", "name": "id", "comment": "Record key"},
            ]))

    @asynccontextmanager
    async def session(**scope):
        assert scope == {"schema": "ts_ods", "table": "ods_patent", "connection_id": connection}
        yield Session()

    async def parameters(values, *, object_id):
        assert object_id == 42
        return {**values, "object_metadata": "[]", "property_metadata": "[]"}

    warehouse.session = session
    warehouse._catalog_parameters = parameters
    with patch.object(explorer_properties, "get_etl_layers", return_value=warehouse):
        result = await explorer_properties.warehouse_asset("ts_ods", "ods_patent", SimpleNamespace(is_superuser=admin), connection)

    warehouse.runtime_table.assert_awaited_once_with("ts_ods", "ods_patent", include_hidden=admin, connection_id=connection)
    warehouse.columns.assert_awaited_once_with("ts_ods", "ods_patent", connection_id=connection, include_hidden=admin)
    assert result[:3] == ("ts_ods", "Patents", "patent")
    assert result[3]["id"]["comment"] == "Record key"
    assert len(calls) == 1


def test_direct_table_route_does_not_enumerate_or_count_other_tables():
    connection = "27f17556-bc61-417e-ba4d-b7dcb213308f"
    warehouse = SimpleNamespace(runtime_table=AsyncMock(return_value={"name": "ods_patent", "schemaName": "ts_ods"}))
    app = FastAPI()
    app.include_router(etl.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(is_superuser=False)
    with patch.object(etl, "get_etl_layers", return_value=warehouse):
        response = TestClient(app).get(f"/api/v1/etl/ts_ods/ods_patent/runtime-table?connectionId={connection}")
    assert response.status_code == 200
    assert response.json()["rowCountAvailable"] is False
    warehouse.runtime_table.assert_awaited_once_with("ts_ods", "ods_patent", include_hidden=False, connection_id=connection)


async def test_direct_mongo_table_uses_registration_without_opening_postgres():
    warehouse = object.__new__(EtlLayers)
    warehouse.selected_connection_id = AsyncMock(return_value="mongo-connection")
    warehouse._external_object = AsyncMock(return_value={"id": 8, "layer": "rds", "table_role": "current",
        "data_type": "patent", "connectionId": "mongo-connection", "engine": "mongodb"})
    result = await warehouse.runtime_table("raw", "patents", include_hidden=False)
    assert result == {"schemaName": "raw", "name": "patents", "registryId": "8", "layer": "rds",
                      "tableRole": "current", "dataType": "patent", "connectionId": "mongo-connection", "engine": "mongodb"}
