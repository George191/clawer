"""Property presentation reads business structure with system metadata parameters."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.web.api.routes import explorer_properties


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
