"""Query Published objects through their semantic fields and current read policy."""

from contextlib import asynccontextmanager
import json
import re
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Integer, Numeric, String, cast, column, false, func, literal, or_, select, table, text, type_coerce
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID, aggregate_order_by
from sqlalchemy.exc import DBAPIError

from app.web.api.models.ontology_runtime import OntologyRuntimeRecord as Record
from app.web.services import ontology_registry as registry
from app.web.services.etl_layers import get_etl_layers, quote_identifier
from app.web.services.ontology_action_runtime import _available_actions, _definition_access, _project_record
from app.web.services.ontology_graph import live_records
from app.web.services.ontology_graph_sets import filters
from app.web.services.ontology_record_policy import property_operand
from app.web.services.ontology_registry_policy import PropertyRecordRule
from app.web.services.ontology_runtime_values import check_record_json, normalize_fields


def normalize_query_fields(definition, values):
    """Warehouse timestamps retain their declared timezone semantics."""
    props = {prop.id: prop for prop in definition.properties}
    timestamps = {key: value for key, value in values.items() if key in props and props[key].type == "datetime" and definition.source.mode == "existing" and value is not None}
    result = normalize_fields(definition, {key: value for key, value in values.items() if key not in timestamps})
    for key, value in timestamps.items():
        try:
            if not isinstance(value, str) or "T" not in value or re.search(r"[.,][0-9]{6}[0-9]*[1-9]", value):
                raise ValueError
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc)
            result[key] = parsed.isoformat(timespec="microseconds")
        except (ValueError, OverflowError):
            raise HTTPException(422, f"{key} must be an ISO timestamp representable at microsecond precision") from None
    check_record_json(result)
    return result


def validate_query(grant, query):
    _, definition, _, fields = grant
    filters(grant, query, normalize=normalize_query_fields)
    props = {prop.id: prop for prop in definition.properties if prop.id in fields}
    if any(sort.field not in props for sort in query.sorts):
        raise HTTPException(403, "Sort references an unavailable property")
    if any(props[sort.field].type == "json" for sort in query.sorts):
        raise HTTPException(422, "Sort by a scalar property")
    if query.keys is None:
        return
    expected = {"$recordId"} if definition.source.mode == "none" else set(definition.primaryKey)
    if definition.source.mode == "existing" and not expected.issubset(fields):
        raise HTTPException(403, "Object identities are unavailable")
    for key in query.keys:
        if set(key) != expected:
            raise HTTPException(422, "Object identity does not match this Published object type")
        check_record_json(key)
        if definition.source.mode == "none":
            try:
                if str(UUID(key["$recordId"])) != key["$recordId"]:
                    raise ValueError
            except ValueError:
                raise HTTPException(422, "Invalid runtime object identity") from None
    if len({json.dumps(key, sort_keys=True) for key in query.keys}) != len(query.keys):
        raise HTTPException(422, "Object identities must be distinct")


@asynccontextmanager
async def object_source(session, grant, query):
    row, definition, context, fields = grant
    if definition.source.mode == "none":
        source = live_records(grant).with_only_columns(
            Record.id,
            Record.revision,
            Record.values,
            func.jsonb_build_object("$recordId", cast(Record.id, String)).label("identity"),
            cast(Record.id, String).label("order_key"),
        ).cte("object_source")
        if not fields:
            source = select(source).where(false()).cte("visible_source")
        yield session, source
        return
    source = definition.source
    if source.schema_name not in registry.WAREHOUSE_SCHEMAS or not registry.COLUMN.fullmatch(source.table or ""):
        raise HTTPException(409, "Invalid warehouse binding")
    props = [prop for prop in definition.properties if prop.id in fields]
    if any(not registry.IDENTIFIER.fullmatch(prop.id) or not registry.COLUMN.fullmatch(prop.sourceColumn or "") for prop in props):
        raise HTTPException(409, "Invalid published property mapping")
    async with get_etl_layers().session() as warehouse:
        relation = f"{quote_identifier(source.schema_name)}.{quote_identifier(source.table)}"
        try:
            await warehouse.execute(text(f"LOCK TABLE {relation} IN ACCESS SHARE MODE"))
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) in {"42P01", "3F000", "42501"}:
                raise HTTPException(409, "The bound warehouse source has changed or is unavailable") from exc
            raise
        catalog = await registry._warehouse_table(warehouse, source)
        registry.ensure_source_identity(row, definition, catalog)
        bound = table(source.table, *(column(name) for name in catalog.columns), schema=source.schema_name).alias("bound")
        mappings = {prop.id: prop.sourceColumn for prop in definition.properties}
        prop_types = {prop.id: prop.type for prop in props}
        for rule in query.filters:
            if prop_types.get(rule.field) == "datetime" and rule.value is not None:
                aware = datetime.fromisoformat(rule.value.replace("Z", "+00:00")).tzinfo is not None
                if aware != (catalog.columns[mappings[rule.field]].kind == "timestamptz"):
                    raise HTTPException(422, "Timestamp timezone must match the mapped property")
        policy = []
        for rule in definition.security.recordRules:
            if not isinstance(rule, PropertyRecordRule):
                raise HTTPException(409, "Invalid record policy for warehouse source")
            if not context.source_access or not context.project_access or not context.principals.intersection(rule.readers):
                continue
            operand = property_operand(rule, context)
            if operand is not None:
                policy.append(func.to_jsonb(bound.c[mappings[rule.field]]) == type_coerce(operand, JSONB))
        def projected_value(prop):
            value = bound.c[prop.sourceColumn]
            if prop.type == "datetime":
                aware = catalog.columns[prop.sourceColumn].kind == "timestamptz"
                if aware:
                    value = func.timezone("UTC", value)
                value = cast(func.to_char(value, 'YYYY-MM-DD"T"HH24:MI:SS.US'), String)
                if aware:
                    value = value + literal("+00:00")
            return value
        values = func.jsonb_build_object(*(part for prop in props for part in (prop.id, projected_value(prop)))) if props else literal({}, JSONB)
        identity = literal(None, JSONB)
        if set(definition.primaryKey).issubset(fields):
            identity = func.jsonb_build_object(*(part for key in definition.primaryKey for part in (key, cast(bound.c[mappings[key]], String))))
        order_key = func.jsonb_build_array(*(bound.c[mappings[key]] for key in definition.primaryKey))
        statement = select(literal(None, PGUUID).label("id"), literal(None, Integer).label("revision"), type_coerce(values, JSONB).label("values"), type_coerce(identity, JSONB).label("identity"), order_key.label("order_key")).select_from(bound)
        if definition.security.recordRules:
            statement = statement.where(or_(*policy) if policy else false())
        if not fields:
            statement = statement.where(false())
        yield warehouse, statement.cte("object_source")


async def evaluate(session, user, definition_id, query, limit, offset, group_offset=0):
    grant = await _definition_access(session, user, definition_id)
    validate_query(grant, query)
    _, definition, context, fields = grant
    async with object_source(session, grant, query) as (reader, source):
        conditions = filters(grant, query, source.c["values"], normalize_query_fields)
        if query.keys is not None:
            conditions.append(or_(*(source.c.identity == type_coerce(key, JSONB) for key in query.keys)) if query.keys else false())
        scoped = select(source).where(*conditions).cte("matches")
        values = scoped.c["values"]
        summed = cast(func.sum(cast(values[query.sumField].astext, Numeric)), String) if query.sumField else literal(None)
        total = select(func.count().label("count"), summed.label("sum")).select_from(scoped).cte("total")
        ordering = []
        for sort in query.sorts:
            value = func.nullif(values[sort.field], literal(None, JSONB))
            ordering.append((value.asc() if sort.direction == "asc" else value.desc()).nulls_last())
        ordering.append(scoped.c.order_key)
        page = select(scoped, func.row_number().over(order_by=ordering).label("ordinal")).order_by(*ordering).offset(offset).limit(limit + 1).subquery()
        rows = select(func.jsonb_agg(aggregate_order_by(func.to_jsonb(page.table_valued()), page.c.ordinal))).scalar_subquery()
        groups = literal(None)
        if query.groupBy:
            key = func.nullif(values[query.groupBy], literal(None, JSONB))
            grouped = select(key.label("value"), func.count().label("count"), summed.label("sum")).select_from(scoped).group_by(key).order_by(key).offset(group_offset).limit(51).subquery()
            groups = select(func.jsonb_agg(aggregate_order_by(func.to_jsonb(grouped.table_valued()), grouped.c.value))).scalar_subquery()
        count, amount, records, buckets = (await reader.execute(select(total.c["count"], total.c["sum"], rows, groups))).one()
    records, buckets = records or [], buckets or []
    return {
        "items": [{**_project_record(definition, fields, record["values"], record["id"], record["revision"]).model_dump(mode="json"), "identity": record["identity"]} for record in records[:limit]],
        "hasMore": len(records) > limit,
        "count": count,
        "sum": amount,
        "groups": buckets[:50],
        "groupsHasMore": len(buckets) > 50,
        "actions": [action.model_dump(mode="json") for action in _available_actions(definition, context, fields)] if fields else [],
    }
