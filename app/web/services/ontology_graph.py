"""Authorized directed links between live runtime records."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased
from sqlmodel import select

from app.web.api.models import Team
from app.web.api.models.ontology_graph import (
    OntologyLink as Link,
)
from app.web.api.models.ontology_graph import (
    OntologyLinkType as LinkType,
)
from app.web.api.models.ontology_graph import (
    OntologyRemovedLink,
    OntologySavedSet,
)
from app.web.api.models.ontology_registry import OntologyDefinitionRow
from app.web.api.models.ontology_runtime import OntologyRuntimeRecord as Record
from app.web.services.ontology_action_runtime import _definition_access, _project_record
from app.web.services.ontology_record_policy import record_read_predicate
from app.web.services.ontology_registry import projects_statement, published_catalog_statement


async def access(session, user, definition_id):
    row, definition, context, fields = await _definition_access(
        session, user, definition_id, lock_membership=True
    )
    if definition.source.mode != "none":
        raise HTTPException(422, "This operation requires records with stable runtime IDs")
    return row, definition, context, fields


async def manage(session, user, project_id):
    if (
        await session.scalar(
            projects_statement(user, manage=True)
            .with_only_columns(Team.id)
            .where(Team.id == project_id)
            .with_for_update(read=True)
        )
        is None
    ):
        raise HTTPException(403, "Project management permission is required")


def link_view(row):
    return {
        "id": row.id,
        "key": row.key,
        "name": row.name,
        "sourceDefinitionId": row.source_definition_id,
        "targetDefinitionId": row.target_definition_id,
        "cardinality": row.cardinality,
    }


async def create_type(session, user, project_id, payload):
    await manage(session, user, project_id)
    for definition_id in sorted({payload.sourceDefinitionId, payload.targetDefinitionId}):
        row, *_ = await access(session, user, definition_id)
        if row.project_id != project_id:
            raise HTTPException(404, "Object type not found in project")
    item = await session.scalar(
        insert(LinkType)
        .values(
            project_id=project_id,
            key=payload.key,
            name=payload.name,
            source_definition_id=payload.sourceDefinitionId,
            target_definition_id=payload.targetDefinitionId,
            cardinality=payload.cardinality,
        )
        .on_conflict_do_nothing(constraint="uq_ontology_link_type_key")
        .returning(LinkType)
    )
    if item is None:
        raise HTTPException(409, "Relationship type key already exists in this project")
    result = link_view(item)
    await session.commit()
    return result


async def link_types(session, user, definition_id, limit, offset):
    row, *_ = await access(session, user, definition_id)
    visible = (
        published_catalog_statement(user).with_only_columns(OntologyDefinitionRow.id).order_by(None)
    )
    rows = (
        await session.scalars(
            select(LinkType)
            .where(
                LinkType.project_id == row.project_id,
                or_(
                    LinkType.source_definition_id == definition_id,
                    LinkType.target_definition_id == definition_id,
                ),
                LinkType.source_definition_id.in_(visible),
                LinkType.target_definition_id.in_(visible),
            )
            .order_by(LinkType.key, LinkType.id)
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    can_manage = await session.scalar(
        projects_statement(user, manage=True)
        .with_only_columns(Team.id)
        .where(Team.id == row.project_id)
    )
    return {
        "items": [link_view(x) for x in rows[:limit]],
        "hasMore": len(rows) > limit,
        "canManage": can_manage is not None,
    }


async def link_targets(session, user, project_id, search, limit, offset):
    row = OntologyDefinitionRow
    query = (
        published_catalog_statement(user, search)
        .with_only_columns(row.id, row.published_definition["name"].astext.label("name"))
        .where(
            row.project_id == project_id,
            row.published_definition["source"]["mode"].astext == "none",
        )
    )
    rows = (await session.execute(query.offset(offset).limit(limit + 1))).all()
    return {
        "items": [{"id": item.id, "name": item.name} for item in rows[:limit]],
        "hasMore": len(rows) > limit,
    }


async def endpoints(session, user, link_type_id, *, write=False):
    statement = select(LinkType).where(LinkType.id == link_type_id)
    typ = await session.scalar(statement.with_for_update() if write else statement)
    if typ is None:
        raise HTTPException(404, "Relationship type not found")
    definitions = {}
    for id_ in sorted({typ.source_definition_id, typ.target_definition_id}):
        result = await access(session, user, id_)
        if result[0].project_id != typ.project_id:
            raise HTTPException(404, "Relationship type not found")
        definitions[id_] = result
    if write:
        await manage(session, user, typ.project_id)
    return typ, definitions


def live_records(grant):
    row, definition, context, _ = grant
    return select(Record).where(
        Record.definition_id == row.id,
        Record.deleted.is_(False),
        record_read_predicate(definition, context, Record),
    )


async def mutate_edge(
    session,
    user,
    link_type_id,
    source_record_id,
    target_record_id,
    *,
    add: bool,
    expected_link_id: UUID | None = None,
):
    """Mutate inside the caller's transaction. A replacement link gets an independent identity."""
    typ, definitions = await endpoints(session, user, link_type_id, write=True)
    source = (Record.id == source_record_id) & (Record.definition_id == typ.source_definition_id)
    target = (Record.id == target_record_id) & (Record.definition_id == typ.target_definition_id)
    await session.execute(
        select(Record.id).where(or_(source, target)).order_by(Record.id).with_for_update()
    )
    for rid, did in (
        (source_record_id, typ.source_definition_id),
        (target_record_id, typ.target_definition_id),
    ):
        if (
            await session.scalar(
                live_records(definitions[did]).with_only_columns(Record.id).where(Record.id == rid)
            )
            is None
        ):
            raise HTTPException(404, "Record not found")
    base = select(Link).where(Link.link_type_id == typ.id)
    pair = await session.scalar(
        base.where(
            Link.source_record_id == source_record_id, Link.target_record_id == target_record_id
        )
    )
    identifiers = {
        "link_type_id": typ.id,
        "source_record_id": source_record_id,
        "target_record_id": target_record_id,
    }
    if not add:
        if pair is None or (expected_link_id is not None and pair.id != expected_link_id):
            raise HTTPException(409, "Relationship has already been removed or replaced")
        link_id = pair.id
        await session.delete(pair)
        session.add(OntologyRemovedLink(**identifiers, link_id=link_id))
    else:
        if pair is not None:
            raise HTTPException(409, "Relationship already exists")
        occupied = []
        if typ.cardinality in ("one_to_one", "many_to_one"):
            occupied.append(Link.source_record_id == source_record_id)
        if typ.cardinality in ("one_to_one", "one_to_many"):
            occupied.append(Link.target_record_id == target_record_id)
        if occupied:
            left, right = aliased(Record), aliased(Record)
            if (
                await session.scalar(
                    base.with_only_columns(Link.id)
                    .join(left, left.id == Link.source_record_id)
                    .join(right, right.id == Link.target_record_id)
                    .where(left.deleted.is_(False), right.deleted.is_(False), or_(*occupied))
                    .limit(1)
                )
                is not None
            ):
                raise HTTPException(409, "Relationship would exceed the declared cardinality")
        remove_marker = delete(OntologyRemovedLink).filter_by(**identifiers)
        if expected_link_id is not None:
            remove_marker = remove_marker.where(OntologyRemovedLink.link_id == expected_link_id)
        previous = await session.scalar(remove_marker.returning(OntologyRemovedLink.link_id))
        if expected_link_id is not None and previous is None:
            raise HTTPException(409, "Relationship removal has already been superseded")
        pair = Link(**identifiers)
        session.add(pair)
        link_id = pair.id
    await session.flush()
    return link_id


async def neighbors(session, user, link_type_id, record_id, direction, limit, offset):
    typ, definitions = await endpoints(session, user, link_type_id)
    if direction == "outgoing":
        anchor, target = typ.source_definition_id, typ.target_definition_id
        origin, other = Link.source_record_id, Link.target_record_id
    else:
        anchor, target = typ.target_definition_id, typ.source_definition_id
        origin, other = Link.target_record_id, Link.source_record_id
    anchor_visible = (
        live_records(definitions[anchor]).with_only_columns(Record.id).where(Record.id == record_id)
    )
    if await session.scalar(anchor_visible) is None:
        raise HTTPException(404, "Record not found")
    _, definition, _, fields = definitions[target]
    rows = (
        await session.execute(
            live_records(definitions[target])
            .add_columns(Link.id)
            .join(Link, other == Record.id)
            .where(
                Link.link_type_id == typ.id,
                origin == record_id,
                anchor_visible.correlate(None).exists(),
            )
            .order_by(Record.id)
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    can_manage = await session.scalar(
        projects_statement(user, manage=True)
        .with_only_columns(Team.id)
        .where(Team.id == typ.project_id)
    )
    return {
        "items": [
            {
                "linkId": lid,
                "canUnlink": can_manage is not None,
                "record": _project_record(definition, fields, r.values, r.id, r.revision),
            }
            for r, lid in rows[:limit]
        ],
        "hasMore": len(rows) > limit,
    }


async def graph_referenced_fields(session, definition_id):
    queries = await session.scalars(
        select(OntologySavedSet.query).where(OntologySavedSet.definition_id == definition_id)
    )
    fields = set()
    for query in queries:
        fields.update(f["field"] for f in query["filters"])
        fields.update(key for key in (query.get("groupBy"), query.get("sumField")) if key)
    return fields
