from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.web.api.models.ontology_registry import Definition, OntologyDefinitionRow
from app.web.services.ontology_registry import (
    Column,
    WarehouseTable,
    assess_definition,
    check_revision,
    ensure_source_identity,
    projects_statement,
    source_signature,
)


def bound_definition(**changes):
    data = {
        "id": "aircraft-model",
        "name": "Aircraft",
        "pluralName": "Aircraft",
        "description": "Bound models",
        "icon": "aircraft",
        "color": "#234abc",
        "groups": ["Flight"],
        "projectId": uuid4(),
        "source": {"mode": "existing", "schema": "ts_ods", "table": "aircraft"},
        "properties": [
            {"id": "aircraft-id", "name": "Aircraft ID", "type": "integer", "sourceColumn": "id", "required": True},
            {"id": "display-name", "name": "Display name", "type": "string", "sourceColumn": "display_name"},
        ],
        "primaryKey": ["aircraft-id"],
        "titleKey": "display-name",
        "security": {"mode": "inherit"},
    }
    return Definition.model_validate({**data, **changes})


def bound_catalog(oid=11, unique_keys=(("id",),)):
    return WarehouseTable(oid, {
        "id": Column("id", "integer", False, "int4"),
        "display_name": Column("display_name", "text", True, "text"),
    }, unique_keys)


def test_existing_source_requires_proven_unique_key():
    definition = bound_definition()
    assert not assess_definition(definition, bound_catalog()).errors
    assert any(
        issue.path == "primaryKey"
        for issue in assess_definition(definition, bound_catalog(unique_keys=())).errors
    )


def test_unsafe_mapping_and_nonempty_nosource():
    bad = bound_definition(source={"mode": "existing", "schema": "public", "table": "secret"})
    assert {issue.path for issue in assess_definition(bad, bound_catalog()).errors} == {"source"}
    unbacked = bound_definition(source={"mode": "none"})
    assert {"source", "security"} <= {issue.path for issue in assess_definition(unbacked).errors}


def test_title_not_primary_and_actual_source_enforced():
    definition = bound_definition(titleKey="deleted_field")
    assert "titleKey" in {issue.path for issue in assess_definition(definition, bound_catalog()).errors}
    unbounded = bound_definition()
    unbounded.properties[1].required = True
    unbounded.properties[1].type = "integer"
    assert {"properties.1.required", "properties.1.type"} <= {
        issue.path for issue in assess_definition(unbounded, bound_catalog()).errors
    }


def test_warehouse_table_replacement_or_new_key_cannot_pass_identity():
    definition = bound_definition()
    table = bound_catalog()
    row = OntologyDefinitionRow(
        project_id=definition.projectId, owner_id=uuid4(), semantic_id=definition.id,
        draft_definition=definition.model_dump(mode="json", by_alias=True),
        source_oid=table.oid, source_signature=source_signature(definition, table),
    )
    ensure_source_identity(row, definition, table)
    for changed in (bound_catalog(oid=12), bound_catalog(unique_keys=()), None):
        with pytest.raises(HTTPException) as failure:
            ensure_source_identity(row, definition, changed)
        assert failure.value.status_code == 409


def test_stale_revision_is_rejected():
    definition = bound_definition()
    row = OntologyDefinitionRow(
        revision=3, project_id=definition.projectId, owner_id=uuid4(),
        semantic_id=definition.id, draft_definition={},
    )
    check_revision(row, 3)
    with pytest.raises(HTTPException) as failure:
        check_revision(row, 2)
    assert failure.value.status_code == 409



def test_definition_rejects_unknown_settings():
    for changes in ({"ownerId": uuid4()}, {"source": {"mode": "existing", "sql": "SELECT 1"}}):
        with pytest.raises(ValidationError):
            bound_definition(**changes)


def test_project_management_requires_enabled_tenant_exact_membership_and_role():
    actor = SimpleNamespace(id=uuid4(), is_superuser=False)
    statement = projects_statement(actor, manage=True).compile(dialect=postgresql.dialect())
    sql = str(statement)
    assert "JOIN tenant ON tenant.id = team.tenant_id" in sql
    assert "JOIN team_user_join ON team_user_join.team_id = team.id" in sql
    assert "tenant.status" in sql and "team_user_join.status" in sql
    assert "team_user_join.user_id" in sql and "team_user_join.role" in sql
    assert actor.id in statement.params.values()
