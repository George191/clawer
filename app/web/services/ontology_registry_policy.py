"""Typed, fail-closed policy and submitter checks for semantic ontology types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Principal(BaseModel):
    """An existing user, project team, or organization, never a wildcard."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["user", "group", "organization"]
    id: UUID


class ActionDefinition(BaseModel):
    """A generated operation with declared writable fields and submitters."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["create", "modify", "delete"]
    submitters: list[Principal] = Field(default_factory=list, max_length=100)
    fields: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_shape(self) -> ActionDefinition:
        if len(set(self.submitters)) != len(self.submitters):
            raise ValueError("Duplicate action submitter")
        if len(set(self.fields)) != len(self.fields) or any(not f for f in self.fields):
            raise ValueError("Action fields must be distinct property identifiers")
        return self


class ObjectSecurity(BaseModel):
    """Object readers and optional additional per-field restrictions."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["inherit", "explicit"]
    objectReaders: list[Principal] = Field(default_factory=list, max_length=100)
    fieldReaders: dict[str, list[Principal]] = Field(default_factory=dict, max_length=200)

    @model_validator(mode="after")
    def validate_shape(self) -> ObjectSecurity:
        lists = [self.objectReaders, *self.fieldReaders.values()]
        if any(len(items) > 100 or len(set(items)) != len(items) for items in lists):
            raise ValueError("Reader lists must contain at most 100 distinct principals")
        return self


@dataclass(frozen=True)
class AccessContext:
    """Verified identity facts for a single project scope and operation."""

    principals: frozenset[Principal]
    project_access: bool
    source_access: bool


def visibility(
    security: ObjectSecurity, properties: list[str], context: AccessContext
) -> tuple[bool, list[str]]:
    """Return object/field visibility before fetching or serializing row values."""
    if not context.project_access or not context.source_access:
        return False, []
    if security.mode == "explicit" and not context.principals.intersection(security.objectReaders):
        return False, []
    return True, [
        name
        for name in properties
        if name not in security.fieldReaders
        or context.principals.intersection(security.fieldReaders[name])
    ]


def action_permission(
    action: ActionDefinition,
    context: AccessContext,
    readable_fields: set[str],
) -> bool:
    """Grant action submit only if its allowlist matches and all affected fields are readable."""
    return (
        context.project_access
        and context.source_access
        and set(action.fields).issubset(readable_fields)
        and bool(context.principals.intersection(action.submitters))
    )


def definition_principals(
    actions: list[ActionDefinition],
    security: ObjectSecurity,
) -> set[Principal]:
    """Collect every principal referenced by the requested policy configuration."""
    values = set(security.objectReaders)
    for readers in security.fieldReaders.values():
        values.update(readers)
    for action in actions:
        values.update(action.submitters)
    return values


def definition_policy_errors(
    *,
    source_mode: str,
    actions: list[ActionDefinition],
    security: ObjectSecurity,
    property_ids: set[str],
    primary_fields: set[str],
    required_fields: set[str],
    eligible: set[Principal] | None = None,
) -> list[str]:
    """Validate cross-field constraints and references before publishing a definition."""
    errors: list[str] = []
    if source_mode == "existing":
        if security.mode != "inherit":
            errors.append("Existing sources require inherited source access.")
        if actions:
            errors.append(
                "Actions are unavailable for existing sources; source tables are read-only."
            )
    elif security.mode != "explicit":
        errors.append(
            "A type without an existing governed source requires explicit object readers."
        )
    if security.mode == "explicit" and not security.objectReaders:
        errors.append("Explicit mode requires at least one object reader.")
    if security.mode == "inherit" and security.objectReaders:
        errors.append("Inherited mode does not take additional object readers.")
    if source_mode == "none" and not any(action.kind == "create" for action in actions):
        errors.append("A type without a backing source must have a Create action.")
    if len({a.kind for a in actions}) != len(actions):
        errors.append("Only one action of each kind is allowed.")
    if set(security.fieldReaders) - property_ids:
        errors.append("Field security references an unknown property.")
    if primary_fields.intersection(security.fieldReaders):
        errors.append("Primary key properties cannot have separate field policies.")
    principals = definition_principals(actions, security)
    for action in actions:
        fields = set(action.fields)
        if not action.submitters:
            errors.append(f"{action.kind.capitalize()} action requires a submitter.")
        if bool(fields) == (action.kind == "delete"):
            errors.append("Create and modify require fields; delete does not take fields.")
        if fields - property_ids:
            errors.append(f"{action.kind.capitalize()} action references an unknown property.")
        if action.kind == "create" and not (primary_fields | required_fields).issubset(fields):
            errors.append("Create action must accept every required property and primary key.")
        if action.kind == "modify" and fields.intersection(primary_fields):
            errors.append("Modify action cannot change primary key properties.")
    if eligible is not None and principals - eligible:
        errors.append("Policy or action references an unavailable project principal.")
    return errors
