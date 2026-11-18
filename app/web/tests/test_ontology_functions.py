from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.ontology_registry import BusinessFunction, Definition, OntologyDefinitionRow
from app.web.api.models.ontology_runtime import OntologyRuntimeRecord
from app.web.api.routes.ontology_functions import Invocation, invoke, list_functions
from app.web.services.ontology_action_runtime import ActionPayload, submit_action
from app.web.services.ontology_functions import evaluate, function_errors, signature

from app.web.tests import test_ontology_action_runtime_integration as action_tests

runtime = action_tests.runtime


def calculation(field, operation, *operands):
    return {
        "key": "apply",
        "name": "Apply change",
        "assignments": [{"field": field, "operation": operation, "operands": operands}],
    }


@pytest.mark.asyncio
async def test_invoke_replay_and_stale_model(runtime):
    engine, user, type_id, _ = runtime
    async with AsyncSession(engine, expire_on_commit=False) as db:
        row = await db.get(OntologyDefinitionRow, type_id)
        payload = deepcopy(row.published_definition)
        payload["functions"] = [
            calculation("note", "concat", {"parameter": "name"}, {"value": " approved"})
        ]
        row.published_definition = payload
        await db.commit()
        record = await submit_action(
            db, user, type_id, "create", ActionPayload(values={"key": "A"})
        )
        catalog = await list_functions(type_id, db, user)
        assert catalog["items"][0]["parameters"] == [
            {"key": "name", "type": "string", "nullable": False}
        ]
        body = Invocation(
            requestId=uuid4(),
            modelRevision=catalog["revision"],
            recordId=record.id,
            expectedRevision=1,
            arguments={"name": "QA"},
        )
        receipt = await invoke(type_id, "apply", body, db, user)
        assert await invoke(type_id, "apply", body, db, user) == receipt
        changed = await db.get(OntologyRuntimeRecord, record.id)
        assert changed.revision == 2 and changed.values["note"] == "QA approved"
        with pytest.raises(HTTPException) as stale:
            await invoke(
                type_id,
                "apply",
                body.model_copy(update={"modelRevision": body.modelRevision + 1}),
                db,
                user,
            )
        assert stale.value.status_code == 409


def test_validate_formulas_and_parameter_domains():
    definition = Definition.model_validate(
        {
            "id": "qa",
            "name": "QA",
            "projectId": uuid4(),
            "source": {"mode": "none"},
            "security": {"mode": "explicit"},
            "properties": [
                {"id": k, "name": k, "type": t}
                for k, t in [("note", "string"), ("amount", "integer")]
            ],
            "actions": [{"kind": "modify", "fields": ["note", "amount"], "submitters": []}],
            "functions": [calculation("amount", "add", {"parameter": "units"}, {"value": 2})],
        }
    )
    props = {p.id: p for p in definition.properties}
    fn = definition.functions[0]
    assert not list(function_errors(definition))
    assert evaluate(fn, props, {"units": 4}) == {"amount": 6}
    for bad in (None, True, 2**63 - 1):
        with pytest.raises(HTTPException):
            evaluate(fn, props, {"units": bad})
    nullable = BusinessFunction.model_validate(calculation("note", "set", {"parameter": "name"}))
    assert signature(nullable, props)["parameters"][0]["nullable"]
    assert evaluate(nullable, props, {"name": None}) == {"note": None}
    for invalid in (
        calculation("note", "multiply", {"value": "a"}, {"value": "b"}),
        calculation("missing", "set", {"parameter": "x"}),
        calculation("amount", "add", {"value": 1}),
    ):
        definition.functions = [BusinessFunction.model_validate(invalid)]
        assert list(function_errors(definition))
