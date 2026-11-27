"""Pass system metadata as query parameters when inspecting a business database."""

import json
from contextvars import ContextVar

from sqlalchemy import text

from app.storage.postgres_client import get_pg_client

catalog_logical_layers: ContextVar[tuple[str, ...] | None] = ContextVar("catalog_logical_layers", default=None)

OBJECT_RECORDS = """jsonb_to_recordset(CAST(:object_metadata AS jsonb)) AS o(
    id bigint, layer text, table_role text, schema_name text, table_name text, is_active boolean, data_type text,
    data_type_source text, description text, description_source text,
    visibility text, resource_group text)"""
OBJECT_RELATION = f"(SELECT * FROM {OBJECT_RECORDS})"
RELATION_RECORDS = """jsonb_to_recordset(CAST(:relation_metadata AS jsonb)) AS r(
    name text, source_schema text, source_table text, source_fields text[],
    target_schema text, target_table text, target_fields text[], enabled boolean,
    relationship_kind text, cardinality text, description text, provenance text)"""
PROPERTY_RECORDS = """jsonb_to_recordset(CAST(:property_metadata AS jsonb)) AS p(
    object_id bigint, name text, sql_type text, ordinal_position integer,
    is_nullable boolean, is_primary_key boolean, primary_key_position integer,
    identity_kind text, generated_kind text, comment text,
    default_expression text, generation_expression text)"""


async def catalog_parameters(params=None, *, session=None, relations=False, object_id=None):
    if session is None:
        async with get_pg_client().session() as opened:
            return await catalog_parameters(params, session=opened, relations=relations, object_id=object_id)
    layers = catalog_logical_layers.get()
    rows = (await session.execute(text("""SELECT id, layer, table_role, schema_name, table_name, is_active,
        data_type, data_type_source, description, description_source, visibility, resource_group
        FROM app.helio_objects
        WHERE CAST(:layers AS text[]) IS NULL OR layer = ANY(CAST(:layers AS text[]))
    """), {"layers": list(layers) if layers is not None else None})).mappings()
    result = {**(params or {}), "object_metadata": json.dumps([dict(row) for row in rows])}
    if relations:
        rows = (await session.execute(text("""SELECT r.name, r.source_schema, r.source_table, r.source_fields,
            r.target_schema, r.target_table, r.target_fields, r.enabled, r.relationship_kind, r.cardinality,
            r.description, r.provenance FROM app.helio_object_relations r WHERE r.enabled
              AND (SELECT count(*) FROM app.helio_objects o WHERE o.is_active
                   AND o.schema_name = r.source_schema AND o.table_name = r.source_table) = 1
              AND (SELECT count(*) FROM app.helio_objects o WHERE o.is_active
                   AND o.schema_name = r.target_schema AND o.table_name = r.target_table) = 1
        """))).mappings()
        result["relation_metadata"] = json.dumps([dict(row) for row in rows])
    if object_id is not None:
        rows = (await session.execute(text("""SELECT object_id, name, sql_type, ordinal_position,
            is_nullable, is_primary_key, primary_key_position, identity_kind, generated_kind, comment,
            default_expression, generation_expression FROM app.helio_object_properties WHERE object_id=:id
        """), {"id": object_id})).mappings()
        result["property_metadata"] = json.dumps([dict(row) for row in rows])
    return result
