"""Evaluate personal object sets against the current field and record grants."""

from fastapi import HTTPException
from sqlalchemy import Numeric, String, cast, func, literal, select, type_coerce
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by, insert

from app.web.api.models.ontology_graph import OntologySavedSet as Saved
from app.web.api.models.ontology_runtime import OntologyRuntimeRecord as Record
from app.web.services.ontology_action_runtime import _project_record
from app.web.services.ontology_graph import access, live_records
from app.web.services.ontology_runtime_values import normalize_fields


def filters(grant, query, values=None, normalize=normalize_fields):
    _, definition, _, fields = grant
    props = {p.id: p for p in definition.properties if p.id in fields}

    def prop(key):
        if key not in props:
            raise HTTPException(403, "Object set references an unavailable property")
        return props[key]

    if query.groupBy and prop(query.groupBy).type == "json":
        raise HTTPException(422, "Group by a scalar property")
    if query.sumField and prop(query.sumField).type not in ("integer", "number"):
        raise HTTPException(422, "Sum requires a numeric property")
    conditions = []
    for f in query.filters:
        p = prop(f.field)
        value = normalize(definition, {f.field: f.value})[f.field]
        column = (Record.values if values is None else values)[f.field]
        if f.op == "eq":
            conditions.append(
                column.astext.is_(None) if value is None else column == type_coerce(value, JSONB)
            )
        elif f.op == "contains" and p.type == "string" and isinstance(value, str):
            conditions.append(column.astext.icontains(value, autoescape=True))
        elif f.op in ("gt", "lt") and p.type in ("integer", "number") and value is not None:
            number = cast(column.astext, Numeric)
            conditions.append(number > value if f.op == "gt" else number < value)
        else:
            raise HTTPException(422, "Filter operator is incompatible with this property")
    return conditions


async def create_set(session, user, definition_id, payload):
    grant = await access(session, user, definition_id)
    filters(grant, payload.query)
    id_ = await session.scalar(
        insert(Saved)
        .values(
            definition_id=definition_id,
            owner_id=user.id,
            name=payload.name,
            query=payload.query.model_dump(mode="json"),
        )
        .on_conflict_do_nothing(constraint="uq_ontology_saved_set_name")
        .returning(Saved.id)
    )
    if id_ is None:
        raise HTTPException(409, "An object set with this name already exists")
    await session.commit()
    return {"id": id_, "name": payload.name}


async def list_sets(session, user, definition_id, limit, offset):
    await access(session, user, definition_id)
    rows = (
        await session.execute(
            select(Saved.id, Saved.name)
            .where(Saved.definition_id == definition_id, Saved.owner_id == user.id)
            .order_by(Saved.name, Saved.id)
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    return {
        "items": [{"id": r.id, "name": r.name} for r in rows[:limit]],
        "hasMore": len(rows) > limit,
    }


async def saved(session, user, definition_id, set_id):
    row = await session.scalar(
        select(Saved).where(
            Saved.id == set_id, Saved.owner_id == user.id, Saved.definition_id == definition_id
        )
    )
    if row is None:
        raise HTTPException(404, "Object set not found")
    return row


async def evaluate(session, user, definition_id, query, limit, offset, group_offset):
    grant = await access(session, user, definition_id)
    _, definition, _, fields = grant
    scoped = (
        live_records(grant)
        .with_only_columns(Record.id, Record.revision, Record.values)
        .where(*filters(grant, query))
        .cte("matches")
    )
    values = scoped.c["values"]
    summed = (
        cast(func.sum(cast(values[query.sumField].astext, Numeric)), String)
        if query.sumField
        else literal(None)
    )
    total = (
        select(func.count().label("count"), summed.label("sum")).select_from(scoped).cte("total")
    )
    page = select(scoped).order_by(scoped.c.id).offset(offset).limit(limit + 1).subquery()
    rows = select(
        func.jsonb_agg(aggregate_order_by(func.to_jsonb(page.table_valued()), page.c.id))
    ).scalar_subquery()
    groups = literal(None)
    if query.groupBy:
        # A missing optional property and an explicitly null value share one bucket.
        key = func.nullif(values[query.groupBy], literal(None, type_=JSONB))
        grouped = (
            select(key.label("value"), func.count().label("count"), summed.label("sum"))
            .select_from(scoped)
            .group_by(key)
            .order_by(key)
            .offset(group_offset)
            .limit(51)
            .subquery()
        )
        groups = select(
            func.jsonb_agg(
                aggregate_order_by(func.to_jsonb(grouped.table_valued()), grouped.c.value)
            )
        ).scalar_subquery()
    # Rows, totals and groups share one PostgreSQL snapshot and the same authorized CTE.
    count, amount, records, buckets = (
        await session.execute(select(total.c["count"], total.c["sum"], rows, groups))
    ).one()
    records = records or []
    buckets = buckets or []
    return {
        "items": [
            _project_record(definition, fields, r["values"], r["id"], r["revision"])
            for r in records[:limit]
        ],
        "hasMore": len(records) > limit,
        "count": count,
        "sum": amount,
        "groups": buckets[:50],
        "groupsHasMore": len(buckets) > 50,
    }
