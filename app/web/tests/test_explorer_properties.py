"""The property contract reads physical and semantic metadata; writes never change warehouse DDL."""

import asyncio
import importlib
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.schema.ontology import ONTOLOGY_PROPERTIES_DDL
from app.web.api.models import User
from app.web.api.models.ontology_markings import OntologyMarking
from app.web.api.models.ontology_registry import Definition, OntologyDefinitionRow
from app.web.api.routes import etl, explorer_properties
from app.web.services import ontology_registry
from app.web.tests import test_published_api_keys as key_tests

publication_app = key_tests.publication_app


@pytest_asyncio.fixture
async def props(publication_app, monkeypatch):
    app, _, engine, *_ = publication_app
    migration = importlib.import_module("app.web.alembic.versions.79a1b2c3d4e5_explorer_properties")

    def migrate(conn):
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.downgrade()
            migration.upgrade()

    async with engine.begin() as conn:
        await conn.execute(text(ONTOLOGY_PROPERTIES_DDL))
        await conn.run_sync(migrate)
        await conn.run_sync(
            User.metadata.create_all, tables=[OntologyDefinitionRow.__table__, OntologyMarking.__table__]
        )
    app.include_router(explorer_properties.router, prefix="/api/v1")
    warehouse = etl.get_etl_workspace()._layers
    monkeypatch.setattr(explorer_properties, "get_etl_layers", lambda: warehouse)
    monkeypatch.setattr(ontology_registry, "get_etl_layers", lambda: warehouse)
    return publication_app


def write(mapping):
    value = {key: mapping[key] for key in (
        "version", "sourceDefinitionId", "titleProperty", "displayName", "description", "icon", "color", "groups"
    )}
    value["properties"] = [
        {k: v for k, v in p.items() if k not in {"sqlType", "primaryKeyPosition"}}
        for p in mapping["properties"]
    ]
    return value


@pytest.mark.asyncio
async def test_title_mapping_edit_scope_staleness_and_column_changes(props):
    _, c, engine, schema, admin, member, *_ = props
    endpoint = f"/api/v1/etl/{schema}/items/property-mapping"
    assert (await c.get(endpoint)).status_code == 401
    async with engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO app.ontology_objects(layer,schema_name,table_name,data_type)
            VALUES ('ods',:schema,'items','patent')
        """), {"schema": schema})
    received = await c.get(endpoint, headers=member)
    assert received.status_code == 200, received.text
    origin = received.json()
    assert origin["titleProperty"] is None and origin["primaryKey"] == ["id"]
    assert origin["dataType"] == "patent"
    draft = write(origin)
    draft.update(
        titleProperty="category", displayName="Flights", icon="plane", color="#2585A0", groups=["Overview"]
    )
    for p in draft["properties"]:
        if p["id"] == "category":
            p.update(
                id="callsign", name="Flight callsign", summary=True, visibility="prominent", group="Overview"
            )
        if p["id"] == "source":
            p.update(visibility="hidden", description="Restricted styling")
    assert (await c.put(endpoint, headers=member, json=draft)).status_code == 403
    assert (await c.put(endpoint, headers=admin, json={**draft, "dataType": "contract"})).status_code == 422
    responses = await asyncio.gather(*(c.put(endpoint, headers=admin, json=draft) for _ in range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    updated = next(r.json() for r in responses if r.status_code == 200)
    assert updated["version"] != origin["version"]
    assert updated["dataType"] == "patent"
    reader = (await c.get(endpoint, headers=member)).json()
    assert reader["titleProperty"] == "category" and reader["displayName"] == "Flights"
    assert not any(p["id"] == "source" for p in reader["properties"])
    draft = write(updated)
    draft["properties"].append(
        {**draft["properties"][1], "id": "unmapped", "sourceColumn": None, "summary": False}
    )
    assert (await c.put(endpoint, headers=admin, json={**draft, "primaryKey": ["source"]})).status_code == 422
    duplicate = write(updated)
    duplicate["properties"][1]["sourceColumn"] = "source"
    assert (await c.put(endpoint, headers=admin, json=duplicate)).status_code == 422
    added = await c.put(endpoint, headers=admin, json=draft)
    assert added.status_code == 200, added.text
    invalid = write(added.json())
    invalid["titleProperty"] = "unmapped"
    assert (await c.put(endpoint, headers=admin, json=invalid)).status_code == 422
    async with engine.begin() as conn:
        await conn.execute(text(f"""COMMENT ON TABLE "{schema}".items IS 'Warehouse table documentation' """))
        await conn.execute(
            text(f"""COMMENT ON COLUMN "{schema}".items.id IS 'Warehouse key documentation' """)
        )
        await conn.execute(
            text(f'ALTER TABLE "{schema}".items DROP COLUMN category, ADD COLUMN amount numeric(12,2)')
        )
    rebased = (await c.get(endpoint, headers=admin)).json()
    assert rebased["titleProperty"] is None and rebased["primaryKey"] == ["id"]
    assert rebased["description"] == "Warehouse table documentation"
    assert (
        next(p for p in rebased["properties"] if p["id"] == "id")["description"]
        == "Warehouse key documentation"
    )
    assert next(p for p in rebased["properties"] if p["id"] == "callsign")["sourceColumn"] is None
    assert next(p for p in rebased["properties"] if p["id"] == "amount")["type"] == "number"
    assert (await c.put(endpoint, headers=admin, json=write(added.json()))).status_code == 409
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE app.ontology_objects SET visibility='hidden' WHERE schema_name=:s AND table_name='items'"
            ),
            {"s": schema},
        )
    try:
        assert (await c.get(endpoint, headers=member)).status_code == 404
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM app.ontology_objects WHERE schema_name=:s"), {"s": schema}
            )


@pytest.mark.asyncio
async def test_real_published_mapping_source_pin_and_authorization(props):
    _, client, engine, _, admin, member, _, team, _ = props
    table = "property_test_" + uuid4().hex[:12]
    definition = {
        "id": "flight",
        "name": "Reviewed flight",
        "projectId": team,
        "icon": "plane",
        "color": "#1188bb",
        "primaryKey": ["identity"],
        "titleKey": "callsign",
        "source": {"mode": "existing", "schema": "ts_ods", "table": table},
        "properties": [
            {"id": "identity", "name": "Identifier", "type": "integer", "sourceColumn": "id"},
            {"id": "callsign", "name": "Call sign", "type": "string", "sourceColumn": "title"},
        ],
        "security": {"mode": "inherit", "objectReaders": [], "fieldReaders": {}},
    }
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS ts_ods"))
        await conn.execute(text(f"CREATE TABLE ts_ods.{table}(id bigint primary key,title text)"))
    endpoint = f"/api/v1/etl/ts_ods/{table}/property-mapping"
    try:
        d = Definition.model_validate(definition)
        physical = await ontology_registry.warehouse_table(d.source)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            owner = await session.scalar(text("SELECT id FROM \"user\" WHERE email='admin@example.org'"))
            row = OntologyDefinitionRow(
                project_id=UUID(team),
                owner_id=owner,
                semantic_id="flight",
                draft_definition=definition,
                published_definition=definition,
                published_at=datetime.now(timezone.utc),
                source_oid=physical.oid,
                source_signature=ontology_registry.source_signature(d, physical),
            )
            session.add(row)
            await session.commit()
        get = await client.get(endpoint, headers=admin)
        assert get.status_code == 200, get.text
        physical_mapping = get.json()
        assert physical_mapping["sourceDefinitionId"] is None
        assert (physical_mapping["titleProperty"], physical_mapping["displayName"]) == (None, table)
        assert [(p["id"], p["name"], p["sourceColumn"]) for p in physical_mapping["properties"]] == [
            ("id", "id", "id"),
            ("title", "title", "title"),
        ]
        physical_body = write(physical_mapping)
        physical_body.update(displayName="Physical assets", titleProperty="title")
        physical_body["properties"][1]["name"] = "Physical title"
        physical_saved = await client.put(endpoint, headers=admin, json=physical_body)
        assert physical_saved.status_code == 200, physical_saved.text
        assert physical_saved.json()["sourceDefinitionId"] is None
        scoped = {"definitionId": str(row.id)}
        semantic_get = await client.get(endpoint, headers=admin, params=scoped)
        assert semantic_get.status_code == 200, semantic_get.text
        mapped = semantic_get.json()
        assert mapped["sourceDefinitionId"] == str(row.id)
        assert (mapped["titleProperty"], mapped["displayName"]) == ("title", "Reviewed flight")
        assert [(p["id"], p["sourceColumn"]) for p in mapped["properties"]] == [
            ("identity", "id"),
            ("callsign", "title"),
        ]
        unauthorized = (await client.get(endpoint, headers=member)).json()
        assert (
            unauthorized["sourceDefinitionId"] is None
            and unauthorized["titleProperty"] == "title"
            and unauthorized["displayName"] == "Physical assets"
            and "Reviewed flight" not in str(unauthorized)
        )
        assert (
            await client.get(endpoint, headers=member, params={"definitionId": str(row.id)})
        ).status_code == 404
        illegal = write(mapped)
        illegal["titleProperty"] = "id"
        assert (await client.put(endpoint, headers=admin, json=illegal)).status_code == 409
        body = write(mapped)
        body["properties"][1]["summary"] = True
        body["groups"] = ["Flight details"]
        body["properties"][1]["group"] = "Flight details"
        assert (await client.put(endpoint, headers=admin, json=body)).json()["properties"][1]["summary"]
        physical_read = (await client.get(endpoint, headers=admin)).json()
        assert physical_read["sourceDefinitionId"] is None
        assert (physical_read["displayName"], physical_read["titleProperty"]) == ("Physical assets", "title")
        assert physical_read["properties"][1]["name"] == "Physical title"
        assert physical_read["groups"] == [] and not physical_read["properties"][1]["summary"]
        async with AsyncSession(engine, expire_on_commit=False) as session:
            published = await session.get(OntologyDefinitionRow, row.id)
            published.published_definition = {
                **definition,
                "security": {**definition["security"], "fieldReaders": {"callsign": []}},
            }
            alternate = {**definition, "id": "alternate", "name": "Alternate type"}
            other = OntologyDefinitionRow(
                project_id=UUID(team),
                owner_id=owner,
                semantic_id="alternate",
                draft_definition=alternate,
                published_definition=alternate,
                published_at=datetime.now(timezone.utc),
                source_oid=physical.oid,
                source_signature=ontology_registry.source_signature(d, physical),
            )
            session.add(other)
            await session.commit()
        assert (await client.get(endpoint, headers=admin)).json()["sourceDefinitionId"] is None
        redacted = (await client.get(endpoint, headers=admin, params=scoped)).json()
        assert redacted["sourceDefinitionId"] == str(row.id) and redacted["titleProperty"] is None
        assert "Flight details" not in str(redacted) and "callsign" not in str(redacted)
        assert (await client.put(endpoint, headers=admin, json=write(redacted))).status_code == 403
        async with engine.begin() as conn:
            await conn.execute(text(f"ALTER TABLE ts_ods.{table} ALTER COLUMN title TYPE integer USING 1"))
        assert (await client.get(endpoint, headers=admin, params=scoped)).status_code == 409
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE ts_ods.{table}"))
