"""Scoped metadata edits which preserve storage-backed relationship instances."""

from fastapi import HTTPException
from sqlalchemy import text

from app.web.services.etl_layers import _object_schema, get_etl_layers
from app.web.services.etl_workspace import get_etl_workspace
from app.web.services.explorer_rows import _asset, _limits


_FIELDS = """id, name, description, source_schema, source_table, source_fields,
    target_schema, target_table, target_fields, cardinality, enabled, updated_at::text AS revision"""
_SCOPE = """((source_schema=:schema AND source_table=:table)
    OR (target_schema=:schema AND target_table=:table))"""


async def _prepare(session, schema, table):
    await _limits(session)
    await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    await session.execute(text("SET LOCAL DateStyle = 'ISO, YMD'"))
    if not await session.scalar(text("SELECT to_regclass('public.ontology_object_relations') IS NOT NULL")):
        raise HTTPException(503, "Logical relationship registry is not initialized")
    await _asset(session, schema, table)


def _item(row, schema, table):
    return {"id": str(row["id"]), "name": row["name"], "description": row["description"],
            "source": {"schema": row["source_schema"], "table": row["source_table"], "fields": row["source_fields"]},
            "target": {"schema": row["target_schema"], "table": row["target_table"], "fields": row["target_fields"]},
            "cardinality": row["cardinality"], "enabled": row["enabled"], "revision": row["revision"],
            "direction": "outgoing" if (row["source_schema"], row["source_table"]) == (schema, table) else "incoming"}


async def read_relationship_types(layer, table, *, can_manage=False):
    schema = _object_schema(layer)
    async with get_etl_layers().session(repeatable_read=True) as session:
        await _prepare(session, schema, table)
        rows = (await session.execute(text(f"""SELECT {_FIELDS} FROM public.ontology_object_relations
            WHERE {_SCOPE} ORDER BY name, id"""), {"schema": schema, "table": table})).mappings().all()
        return {"items": [_item(row, schema, table) for row in rows], "canManage": can_manage}


async def update_relationship_type(layer, table, relation_id, payload, user):
    if not user.is_superuser:
        raise HTTPException(403, "Physical object management permission is required")
    if int(relation_id) > 2**63 - 1:
        raise HTTPException(422, "Relationship identifier is too large")
    schema = _object_schema(layer)
    async with get_etl_workspace()._session() as session:
        await _prepare(session, schema, table)
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('public.ontology_object_relations'))"))
        params = {"id": int(relation_id), "schema": schema, "table": table}
        row = (await session.execute(text(f"""SELECT {_FIELDS} FROM public.ontology_object_relations
            WHERE id=:id AND {_SCOPE} FOR UPDATE"""), params)).mappings().first()
        if not row:
            raise HTTPException(404, "Relationship type not available for this object type")
        if row["revision"] != payload.expectedRevision:
            raise HTTPException(409, {"code": "relation_version_conflict", "message": "Relationship type changed. Reload it before saving."})
        if row["name"] == payload.name and row["description"] == payload.description:
            return _item(row, schema, table)
        if await session.scalar(text("""SELECT EXISTS(SELECT 1 FROM public.ontology_object_relations
            WHERE name=:name AND id<>:id)"""), {"id": params["id"], "name": payload.name}):
            raise HTTPException(409, {"code": "relation_name_conflict", "message": "A relationship type with this name already exists"})
        updated = (await session.execute(text(f"""UPDATE public.ontology_object_relations
            SET name=:name, description=:description, updated_at=clock_timestamp()
            WHERE id=:id RETURNING {_FIELDS}"""),
            {"id": params["id"], "name": payload.name, "description": payload.description})).mappings().one()
        return _item(updated, schema, table)
