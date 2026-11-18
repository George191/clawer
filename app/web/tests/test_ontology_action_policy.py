"""Policy and action payload invariants; no calls to a database or external services."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import HTTPException

from app.web.services.ontology_registry_policy import (
    AccessContext,
    ActionDefinition,
    ObjectSecurity,
    Principal,
    action_permission,
    definition_policy_errors,
    visibility,
)
from app.web.services.ontology_runtime_values import check_record_json, normalize_fields, record_key

GROUP = Principal(kind="group", id=UUID(int=1))
MEMBER = Principal(kind="user", id=UUID(int=2))
OUTSIDER = Principal(kind="user", id=UUID(int=3))


def policy_errors(actions, security, source="none", eligible=None):
    return definition_policy_errors(
        source_mode=source,
        actions=actions,
        security=security,
        property_ids={"id", "title", "notes"},
        primary_fields={"id"},
        required_fields={"id", "title"},
        eligible=eligible,
    )


def test_project_and_source_gates_cannot_be_overridden_by_allowlist():
    security = ObjectSecurity(mode="explicit", objectReaders=[GROUP, OUTSIDER])
    assert visibility(security, ["id"], AccessContext(frozenset({GROUP}), False, True)) == (
        False,
        [],
    )
    assert visibility(security, ["id"], AccessContext(frozenset({GROUP}), True, False)) == (
        False,
        [],
    )
    assert visibility(security, ["id"], AccessContext(frozenset({MEMBER}), True, True)) == (
        False,
        [],
    )


def test_field_policy_is_a_further_restriction_not_an_object_grant():
    security = ObjectSecurity(
        mode="explicit",
        objectReaders=[GROUP],
        fieldReaders={"title": [MEMBER], "notes": []},
    )
    assert visibility(
        security,
        ["id", "title", "notes"],
        AccessContext(frozenset({GROUP}), True, True),
    ) == (True, ["id"])
    assert visibility(
        security,
        ["id", "title", "notes"],
        AccessContext(frozenset({GROUP, MEMBER}), True, True),
    ) == (True, ["id", "title"])
    assert visibility(
        security,
        ["id", "title", "notes"],
        AccessContext(frozenset({MEMBER}), True, True),
    ) == (False, [])


def test_action_submitter_cannot_write_a_hidden_field():
    action = ActionDefinition(kind="modify", submitters=[GROUP], fields=["notes"])
    verified = AccessContext(frozenset({GROUP}), True, True)
    assert action_permission(action, verified, {"id", "notes"})
    assert not action_permission(action, verified, {"id"})
    assert not action_permission(action, AccessContext(frozenset({MEMBER}), True, True), {"notes"})


def test_incomplete_draft_is_parseable_but_blocks_publication():
    incomplete = ActionDefinition(kind="create")
    issues = policy_errors([incomplete], ObjectSecurity(mode="explicit"))
    assert any("reader" in issue for issue in issues)
    assert any("submitter" in issue for issue in issues)
    assert any("every required" in issue for issue in issues)
    assert not policy_errors(
        [ActionDefinition(kind="create", submitters=[GROUP], fields=["id", "title"])],
        ObjectSecurity(mode="explicit", objectReaders=[GROUP]),
        eligible={GROUP},
    )


def test_invalid_action_or_policy_is_not_publishable():
    actions = [
        ActionDefinition(kind="create", submitters=[GROUP], fields=["id", "title"]),
        ActionDefinition(kind="modify", submitters=[OUTSIDER], fields=["id", "title"]),
    ]
    explicit = ObjectSecurity(
        mode="explicit", objectReaders=[GROUP], fieldReaders={"id": [OUTSIDER]}
    )
    issues = policy_errors(actions, explicit, "existing", {GROUP})
    assert any("read-only" in issue for issue in issues)
    assert any("inherited" in issue for issue in issues)
    assert any("Primary key properties" in issue for issue in issues)
    assert any("cannot change primary key" in issue for issue in issues)
    assert any("unavailable project principal" in issue for issue in issues)


def sample_definition():
    return SimpleNamespace(
        primaryKey=["numeric", "time"],
        properties=[
            SimpleNamespace(id="numeric", type="number", required=True),
            SimpleNamespace(id="time", type="datetime", required=True),
            SimpleNamespace(id="note", type="json", required=False),
        ],
    )


def test_equivalent_numeric_and_utc_keys_have_identical_digests():
    definition = sample_definition()
    first = normalize_fields(definition, {"numeric": 7, "time": "2026-11-16T12:00:00Z"})
    second = normalize_fields(definition, {"numeric": 7.0, "time": "2026-11-16T20:00:00+08:00"})
    assert second["time"] == first["time"]
    assert record_key(definition, second) == record_key(definition, first)
    assert record_key(definition, {**second, "numeric": 8}) != record_key(definition, first)
    assert record_key(definition, {**second, "numeric": -0.0}) == record_key(
        definition,
        {**first, "numeric": 0},
    )


def test_timestamp_key_rejects_precision_loss_and_accepts_exact_trailing_zeros():
    definition = sample_definition()
    for fraction in (".1234561", ",1234560001"):
        with pytest.raises(HTTPException, match="microsecond precision"):
            normalize_fields(definition, {"time": "2026-11-16T12:00:00" + fraction + "Z"})
    assert normalize_fields(definition, {"time": "2026-11-16T12:00:00.123456000Z"}) == {
        "time": "2026-11-16T12:00:00.123456+00:00"
    }


@pytest.mark.parametrize(
    "values",
    [
        {"numeric": None},
        {"numeric": True},
        {"numeric": "7"},
        {"numeric": float("nan")},
        {"time": "2026-11-16T12:00:00"},
        {"note": {"value": "\x00"}},
        {"note": {"value": "\ud800"}},
        {"note": {"\x00": "value"}},
        {"unexpected": 3},
    ],
)
def test_action_validation_refuses_invalid_data_types_and_nulls(values):
    with pytest.raises(HTTPException) as error:
        normalize_fields(sample_definition(), values)
    assert error.value.status_code == 422


def test_combined_record_is_bounded_independent_of_action_payload_size():
    with pytest.raises(HTTPException):
        check_record_json({"first": "a" * 170_000, "second": "b" * 100_000})
