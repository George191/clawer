"""Record-level restrictions shared by object reads, actions and graph operations."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import false, func, literal, or_, select, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.web.api.models.ontology_registry import Definition, OntologyDefinitionRow
from app.web.api.models.ontology_markings import OntologyMarking
from app.web.api.models.ontology_runtime import OntologyRuntimeRecord
from app.web.services.ontology_markings import allowed_ids, covers
from app.web.services.ontology_registry_policy import (
    AccessContext,
    PropertyRecordRule,
    RelatedRecordRule,
)
from app.web.services.ontology_runtime_values import normalize_fields


def record_policy_errors(definition: Definition) -> list[str]:
    types = {p.id: p.type for p in definition.properties}
    errors = []
    for rule in definition.security.recordRules:
        if not rule.readers:
            errors.append("Every record rule requires at least one reader.")
        if isinstance(rule, RelatedRecordRule):
            if definition.source.mode != "none":
                errors.append(
                    "Relationship rules require editable records with stable object identifiers."
                )
            continue
        if types.get(rule.field) not in {"string", "number", "integer", "boolean"}:
            errors.append(
                "Record comparisons require a declared string, integer, number or boolean property."
            )
        elif rule.match == "currentUser":
            if types[rule.field] != "string":
                errors.append(
                    "Current-user comparisons require a string property containing the user UUID."
                )
        else:
            if (
                type(rule.value) in (int, float)
                and abs(rule.value) > 2**53 - 1
                and int(rule.value) == rule.value
            ):
                errors.append(
                    "Integral record comparison values must be within the exact browser integer range."
                )
                continue
            try:
                normalize_fields(definition, {rule.field: rule.value})
            except HTTPException as exc:
                errors.append(f"Invalid record comparison: {exc.detail}")
    return errors


def _related_records(definition, definition_id, rule, record_id=None, context=None):
    from app.web.api.models.ontology_graph import OntologyLink, OntologyLinkType

    link_type = OntologyLinkType
    link = OntologyLink
    other = aliased(OntologyRuntimeRecord)
    if rule.direction == "outgoing":
        own_type, other_type = link_type.source_definition_id, link_type.target_definition_id
        own_id, other_id = link.source_record_id, link.target_record_id
    else:
        own_type, other_type = link_type.target_definition_id, link_type.source_definition_id
        own_id, other_id = link.target_record_id, link.source_record_id
    other_definition = aliased(OntologyDefinitionRow)
    query = (
        select(1)
        .select_from(link_type)
        .join(other, other.definition_id == other_type)
        .join(other_definition, other_definition.id == other.definition_id)
        .where(
            link_type.project_id == definition.projectId,
            link_type.key == rule.linkType,
            own_type == definition_id,
            other_definition.project_id == definition.projectId,
            other.id == rule.relatedRecordId,
            other.deleted.is_(False),
        )
    )
    if record_id is not None:
        query = query.join(link, link.link_type_id == link_type.id).where(
            own_id == record_id, other_id == other.id
        )
    if context is not None:
        query = query.where(covers(other_definition.published_definition["security"]["markings"],
                                   allowed_ids(definition.projectId, [(p.kind, p.id) for p in context.principals])))
    return query


async def validate_record_policy_refs(session, definition_id, definition) -> list[str]:
    for rule in definition.security.recordRules:
        if isinstance(rule, RelatedRecordRule) and not await session.scalar(
            _related_records(definition, definition_id, rule).exists().select()
        ):
            return [
                "Relationship rule must identify a matching link type and active record in this project."
            ]
    return []


async def lock_related_clearances(session, definition, context):
    """Keep related anchors' classifications and grants stable for the complete operation."""
    anchors = {r.relatedRecordId for r in definition.security.recordRules
               if isinstance(r, RelatedRecordRule) and context.principals.intersection(r.readers)}
    if not anchors:
        return
    row = OntologyDefinitionRow
    peer_types = select(OntologyRuntimeRecord.definition_id).where(OntologyRuntimeRecord.id.in_(anchors))
    labels = await session.scalars(select(row.published_definition["security"]["markings"])
                                  .where(row.project_id == definition.projectId, row.id.in_(peer_types))
                                  .order_by(row.id).with_for_update(read=True))
    ids = {UUID(value) for values in labels for value in values or []}
    if ids:
        await session.execute(select(OntologyMarking.id).where(OntologyMarking.project_id == definition.projectId, OntologyMarking.id.in_(ids))
                              .order_by(OntologyMarking.id).with_for_update(read=True))


def property_operand(rule: PropertyRecordRule, context: AccessContext):
    if rule.match == "equals":
        return rule.value
    return next((str(p.id) for p in context.principals if p.kind == "user"), None)


def _predicate(definition, context, definition_id, record_id, values):
    if not context.project_access or not context.source_access:
        return false()
    if not definition.security.recordRules:
        return true()
    conditions = []
    for rule in definition.security.recordRules:
        if not context.principals.intersection(rule.readers):
            continue
        if isinstance(rule, PropertyRecordRule):
            operand = property_operand(rule, context)
            if operand is not None:
                conditions.append(
                    func.jsonb_extract_path(values, literal(rule.field))
                    == literal(operand, type_=JSONB)
                )
        else:
            conditions.append(_related_records(definition, definition_id, rule, record_id, context).exists())
    return or_(false(), *conditions)


def record_read_predicate(definition, context, record=OntologyRuntimeRecord):
    """Apply alongside the caller's definition, lifecycle, and field guards before LIMIT or aggregation."""
    return _predicate(definition, context, record.definition_id, record.id, record.values)


async def authorize_record_values(
    session: AsyncSession,
    definition_id: UUID,
    definition: Definition,
    context: AccessContext,
    record_id: UUID,
    values: dict,
) -> bool:
    """Evaluate an uncommitted create or replacement with the same predicates used for stored records."""
    if not definition.security.recordRules:
        return context.project_access and context.source_access
    predicate = _predicate(
        definition,
        context,
        definition_id,
        literal(record_id),
        literal(values, type_=JSONB),
    )
    return bool(await session.scalar(select(predicate)))
