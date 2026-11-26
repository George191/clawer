"""Workbench routing follows registered bindings on exact physical identities."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from app.web.services import etl_layers, etl_workspace, ontology_registry


RAW = "11111111-1111-1111-1111-111111111111"
CURATED = "22222222-2222-2222-2222-222222222222"
REVISION = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def bindings():
    return {"rds": {"id": RAW, "name": "Raw", "engine": "postgresql", "updated_at": REVISION},
            "ods": {"id": CURATED, "name": "Curated", "engine": "postgresql", "updated_at": REVISION}}


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
    stale = SimpleNamespace(connection_id=RAW, revision=REVISION, close=AsyncMock(), connect=AsyncMock())
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
    clients = {layer: SimpleNamespace(connection_id=info["id"], revision=REVISION, connect=AsyncMock()) for layer, info in bindings.items()}
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

        return SimpleNamespace(connection_id=identity, revision=REVISION, connect=AsyncMock(), session=session, sql=sql)

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


@pytest.mark.parametrize("initial_id,initial_revision", [
    (RAW, REVISION), (CURATED, REVISION.replace(year=2025)),
])
async def test_other_web_process_reopens_rebound_connection(bindings, initial_id, initial_revision):
    service = etl_layers.EtlLayers(metadata_pg=SimpleNamespace(fetch_all=AsyncMock(return_value=[{"layer": "ods"}])))
    client = SimpleNamespace(connection_id=initial_id, revision=initial_revision, close=AsyncMock())

    async def reconnect(*, max_retries):
        client.connection_id, client.revision = CURATED, REVISION

    client.connect = reconnect
    with (patch.object(etl_layers, "layer_connection_info", AsyncMock(return_value=bindings)),
          patch.object(etl_layers, "get_layer_pg_client", return_value=client)):
        assert (await service.scoped_postgres("analysis", "measurements"))[0] is client
    client.close.assert_awaited_once()


@pytest.mark.parametrize("operation", ["patch_row", "delete_row"])
async def test_row_write_rejects_predeployment_and_rebound_tokens_before_opening_warehouse(operation):
    workspace = object.__new__(etl_workspace.EtlWorkspace)
    metadata = Mock()
    result = Mock()
    result.scalars.return_value.all.return_value = []
    metadata.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def session(**kwargs):
        assert not kwargs.get("business"), "a stale token must never open the new warehouse"
        yield metadata

    workspace._session = session
    request = getattr(workspace, operation)
    payload = {"key": {"id": "1"}, "changes": {"value": "edited"}, "expectedVersion": "a" * 64}
    with pytest.raises(HTTPException) as old_format:
        await request("research", "measurements", payload)
    assert old_format.value.status_code == 428
    metadata.execute.assert_not_awaited()
    with pytest.raises(HTTPException, match="connection changed") as rebound:
        await request("research", "measurements", {**payload, "expectedVersion": RAW + ":" + "a" * 64})
    assert rebound.value.status_code == 409
    lookup, params = metadata.execute.await_args.args
    assert "FOR SHARE OF o, b, s" in str(lookup)
    assert params == {"schema": "research", "table": "measurements", "connection_id": RAW}


async def test_scoped_write_commits_business_session_before_unlocking_binding():
    workspace = object.__new__(etl_workspace.EtlWorkspace)
    sequence = []
    metadata, business = Mock(), Mock()
    locked = Mock()
    locked.scalars.return_value.all.return_value = [REVISION]
    metadata.execute = AsyncMock(return_value=locked)
    updated = Mock()
    updated.mappings.return_value = [{"row": {"id": "1", "value": "edited"}}]
    business.execute = AsyncMock(return_value=updated)
    workspace._layers = SimpleNamespace(columns=AsyncMock(return_value=[{"name":"id", "primary":True}, {"name":"value"}]))

    @asynccontextmanager
    async def session(**kwargs):
        if kwargs.get("business"):
            assert kwargs["connection_id"] == RAW
            assert "FOR SHARE" in str(metadata.execute.await_args.args[0])
            sequence.append("business open")
            yield business
            sequence.append("business commit")
        else:
            yield metadata
            sequence.append("binding unlocked")

    workspace._session = session
    version = RAW + ":" + "a" * 64
    result = await workspace.patch_row("research", "measurements", {
        "key": {"id": "1"}, "changes": {"value": "edited"}, "expectedVersion": version,
    })
    statement, params = business.execute.await_args.args
    assert etl_layers.row_version_sql(RAW, REVISION) in str(statement)
    assert etl_layers.row_version_sql(CURATED, REVISION) != etl_layers.row_version_sql(RAW, REVISION)
    assert etl_layers.row_version_sql(RAW, REVISION.replace(year=2025)) != etl_layers.row_version_sql(RAW, REVISION)
    assert result["rowCount"] == 1 and params["version"] == version
    assert sequence == ["business open", "business commit", "binding unlocked"]
