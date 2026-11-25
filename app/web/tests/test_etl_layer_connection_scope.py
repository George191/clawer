"""Workbench routing follows registered bindings on exact physical identities."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from app.web.services import etl_layers, etl_workspace, ontology_registry


RAW = "11111111-1111-1111-1111-111111111111"
CURATED = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def bindings():
    return {"rds": {"id": RAW, "name": "Raw", "engine": "postgresql"},
            "ods": {"id": CURATED, "name": "Curated", "engine": "postgresql"}}


async def test_scope_rejects_the_wrong_connection_and_requires_selection_for_shared_schemas(bindings):
    lookup = AsyncMock(side_effect=[[{"layer": "ods"}], [{"layer": "rds"}, {"layer": "ods"}],
                                   [{"layer": "rds"}, {"layer": "ods"}]])
    service = etl_layers.EtlLayers(metadata_pg=SimpleNamespace(fetch_all=lookup))
    with patch.object(etl_layers, "layer_connection_info", AsyncMock(return_value=bindings)):
        with pytest.raises(HTTPException, match="matching data connection") as wrong:
            await service.connection_scope("research", "measurements", connection_id=RAW)
        assert wrong.value.status_code == 409
        with pytest.raises(HTTPException, match="Select a data connection"):
            await service.connection_scope("research")
        assert await service.connection_scope("research", connection_id=CURATED) == (bindings["ods"], ("ods",))
    assert lookup.await_args_list[0].args[1] == {"schema": "research", "table": "measurements"}


async def test_scope_rejects_binding_change_after_client_connect(bindings):
    service = etl_layers.EtlLayers(metadata_pg=SimpleNamespace(fetch_all=AsyncMock(return_value=[{"layer": "ods"}])))
    stale = SimpleNamespace(connection_id=RAW, connect=AsyncMock())
    client = Mock(return_value=stale)
    with (patch.object(etl_layers, "layer_connection_info", AsyncMock(return_value=bindings)),
          patch.object(etl_layers, "get_layer_pg_client", client)):
        with pytest.raises(HTTPException, match="connection changed"):
            await service.scoped_postgres("analysis", "measurements")
    client.assert_called_once_with("ods")
    stale.connect.assert_awaited_once()


async def test_scope_rejects_conflicting_object_rows_on_one_connection(bindings):
    lookup = AsyncMock(return_value=[{"layer": "ods"}, {"layer": "ods"}])
    service = etl_layers.EtlLayers(metadata_pg=SimpleNamespace(fetch_all=lookup))
    with (patch.object(etl_layers, "layer_connection_info", AsyncMock(return_value=bindings)),
          pytest.raises(HTTPException, match="conflicting Ontology objects")):
        await service.connection_scope("analysis", "measurements")


async def test_same_qualified_table_selects_only_the_explicit_connection(bindings):
    lookup = AsyncMock(return_value=[{"layer": "rds"}, {"layer": "ods"}])
    service = etl_layers.EtlLayers(metadata_pg=SimpleNamespace(fetch_all=lookup))
    clients = {layer: SimpleNamespace(connection_id=info["id"], connect=AsyncMock()) for layer, info in bindings.items()}
    with (patch.object(etl_layers, "layer_connection_info", AsyncMock(return_value=bindings)),
          patch.object(etl_layers, "get_layer_pg_client", side_effect=clients.__getitem__)):
        with pytest.raises(HTTPException, match="Select a data connection"):
            await service.scoped_postgres("DataCenter", "Records")
        assert (await service.scoped_postgres("DataCenter", "Records", connection_id=RAW))[0] is clients["rds"]
        assert (await service.scoped_postgres("DataCenter", "Records", connection_id=CURATED))[0] is clients["ods"]


async def test_same_qualified_ddl_targets_each_selected_connection(bindings):
    def database(identity):
        sql = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 1))

        async def bound_connection(_statement, params):
            return bindings[params["layer"]]["id"]

        @asynccontextmanager
        async def session():
            yield SimpleNamespace(execute=sql, scalar=bound_connection)

        return SimpleNamespace(connection_id=identity, connect=AsyncMock(), session=session, sql=sql)

    metadata, raw, curated = database(None), database(RAW), database(CURATED)
    workspace = object.__new__(etl_workspace.EtlWorkspace)
    workspace._pg = metadata
    workspace._layers = etl_layers.EtlLayers(metadata_pg=metadata)
    pg_clients = {"rds": raw, "ods": curated}
    payload = {"name": "Records", "dataType": "measurement",
               "fields": [{"name": "RecordID", "type": "BIGINT", "primary": True, "nullable": False}]}
    synced = AsyncMock()
    with (patch.object(etl_layers, "layer_connection_info", AsyncMock(return_value=bindings)),
          patch.object(etl_layers, "get_layer_pg_client", side_effect=pg_clients.__getitem__),
          patch.object(etl_workspace, "sync_object_metadata", synced),
          patch.object(workspace._layers, "get_table", AsyncMock())):
        with pytest.raises(HTTPException, match="matching data connection"):
            await workspace.create_table("DataCenter", {**payload, "logicalLayer": "rds", "connectionId": CURATED})
        with pytest.raises(HTTPException) as protected:
            await workspace.create_table("app", {**payload, "logicalLayer": "rds", "connectionId": RAW})
        assert protected.value.status_code == 404
        raw.sql.assert_not_awaited()
        curated.sql.assert_not_awaited()
        await workspace.create_table("DataCenter", {**payload, "logicalLayer": "rds", "connectionId": RAW})
        await workspace.create_table("DataCenter", {**payload, "logicalLayer": "ods", "connectionId": CURATED})
    for target in (raw, curated):
        ddl = [str(call.args[0]) for call in target.sql.await_args_list if str(call.args[0]).startswith("CREATE TABLE")]
        assert len(ddl) == 1
        assert ddl[0].startswith('CREATE TABLE "DataCenter"."Records" ("RecordID" BIGINT')
    assert [call.kwargs["layer"] for call in synced.await_args_list] == ["rds", "ods"]
    assert [call.args[1:] for call in synced.await_args_list] == [("DataCenter", "Records"), ("DataCenter", "Records")]


async def test_layer_graph_keeps_colliding_schemas_on_separate_connections():
    service = etl_layers.EtlLayers(metadata_pg=object())
    assets = [{"schema_name": "shared", "table_name": "events", "layer": "rds",
               "connectionId": RAW, "engine": "postgresql", "database": "raw", "table_role": "current"},
              {"schema_name": "shared", "table_name": "curated", "layer": "ods",
               "connectionId": CURATED, "engine": "postgresql", "database": "analytics", "table_role": "current"}]
    relation = {"source_schema": "shared", "source_table": "events",
                "target_schema": "shared", "target_table": "curated"}
    with (patch.object(service, "_registered_catalog", AsyncMock(return_value=(assets, [relation]))),
          patch.object(service, "_schema_aliases", AsyncMock())):
        rows = await service.layers()
    assert [(row["connectionId"], row["schemaName"]) for row in rows] == [(RAW, "shared"), (CURATED, "shared")]
    assert rows[0]["upstreamSchemaRefs"] == []
    assert rows[1]["upstreamSchemaRefs"] == [{"connectionId": RAW, "schemaName": "shared"}]


@pytest.mark.parametrize("name", ["app", "information_schema", "pg_catalog", "ts_meta", "ts_cdc_sandbox"])
def test_protected_schemas_are_not_ordinary_objects(name):
    with pytest.raises(HTTPException) as failure:
        etl_layers.resolve_schema(name)
    assert failure.value.status_code == 404


def test_published_binding_uses_connection_even_when_physical_oid_and_schema_match():
    definition = SimpleNamespace(
        primaryKey=["id"], properties=[SimpleNamespace(id="id", sourceColumn="RecordID")],
    )
    columns = {"RecordID": ontology_registry.Column("RecordID", "bigint", False, "int8")}
    expected = ontology_registry.WarehouseTable(42, columns, (("RecordID",),), RAW)
    other = ontology_registry.WarehouseTable(42, columns, (("RecordID",),), CURATED)
    published = SimpleNamespace(source_oid=42, source_signature=ontology_registry.source_signature(definition, expected))
    ontology_registry.ensure_source_identity(published, definition, expected)
    with pytest.raises(HTTPException, match="bound warehouse source has changed") as wrong:
        ontology_registry.ensure_source_identity(published, definition, other)
    assert wrong.value.status_code == 409
