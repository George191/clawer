"""The current ETL object/DDL and property metadata, stored in public Ontology tables."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text

from app.etl.schema.catalogs import TABLE_DEFINITIONS
from app.etl.schema.ddl import ddl_checksum
from app.etl.schema.layout import logical_table_name, physical_table_name

ONTOLOGY_OBJECTS_TABLE = "public.ontology_objects"
ONTOLOGY_PROPERTIES_TABLE = "public.ontology_object_properties"
_EXTRA_OBJECT_COLUMNS = (
    "data_type TEXT NOT NULL DEFAULT ''",
    "data_type_source TEXT NOT NULL DEFAULT 'not_configured'",
    "description_source TEXT NOT NULL DEFAULT 'not_configured'",
    "provenance TEXT NOT NULL DEFAULT ''",
    "resource_group TEXT NOT NULL DEFAULT 'other' CONSTRAINT ontology_objects_resource_group_check CHECK (resource_group IN ('ingestion','processing','serving','other'))",
)
ONTOLOGY_OBJECT_COLUMNS_ADD_STATEMENTS = tuple(
    f"ALTER TABLE public.ontology_objects ADD COLUMN IF NOT EXISTS {column}"
    for column in _EXTRA_OBJECT_COLUMNS
)
ONTOLOGY_OBJECT_DEFAULT_STATEMENTS = (
    "ALTER TABLE public.ontology_objects ALTER COLUMN ddl_sql SET DEFAULT ''",
    "ALTER TABLE public.ontology_objects ALTER COLUMN checksum SET DEFAULT ''",
    "ALTER TABLE public.ontology_objects ALTER COLUMN version SET DEFAULT 0",
)
ONTOLOGY_OBJECTS_DDL = """CREATE TABLE IF NOT EXISTS public.ontology_objects (
    id BIGSERIAL PRIMARY KEY,
    layer TEXT NOT NULL,
    table_role TEXT NOT NULL DEFAULT 'current',
    schema_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    ddl_sql TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 0,
    checksum TEXT NOT NULL DEFAULT '',
    partition_type TEXT NOT NULL DEFAULT 'none',
    partition_column TEXT,
    partition_granularity TEXT,
    partition_count INTEGER,
    description TEXT,
    updated_by TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    data_type TEXT NOT NULL DEFAULT '',
    data_type_source TEXT NOT NULL DEFAULT 'not_configured',
    description_source TEXT NOT NULL DEFAULT 'not_configured',
    provenance TEXT NOT NULL DEFAULT '',
    resource_group TEXT NOT NULL DEFAULT 'other',
    CONSTRAINT ontology_objects_resource_group_check CHECK (resource_group IN ('ingestion','processing','serving','other')),
    CONSTRAINT ontology_objects_table_role_check CHECK (table_role IN ('current','history')),
    CONSTRAINT ontology_objects_partition_type_check CHECK (partition_type IN ('none','range','hash')),
    CONSTRAINT ontology_objects_partition_granularity_check CHECK (
        partition_granularity IS NULL OR partition_granularity IN ('month','year')),
    CONSTRAINT ontology_objects_partition_layout_check CHECK (
        (partition_type = 'none' AND partition_column IS NULL AND partition_granularity IS NULL AND partition_count IS NULL)
        OR (partition_type = 'range' AND partition_column IS NOT NULL AND partition_granularity IS NOT NULL AND partition_count IS NULL)
        OR (partition_type = 'hash' AND partition_column IS NOT NULL AND partition_granularity IS NULL AND partition_count > 0))
)"""
ONTOLOGY_PROPERTIES_DDL = """CREATE TABLE IF NOT EXISTS public.ontology_object_properties (
    object_id BIGINT NOT NULL REFERENCES public.ontology_objects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sql_type TEXT NOT NULL,
    ordinal_position INTEGER NOT NULL CHECK (ordinal_position > 0),
    default_expression TEXT,
    is_nullable BOOLEAN NOT NULL,
    is_primary_key BOOLEAN NOT NULL,
    primary_key_position INTEGER,
    identity_kind TEXT NOT NULL DEFAULT '',
    generated_kind TEXT NOT NULL DEFAULT '',
    generation_expression TEXT,
    comment TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (object_id, name),
    UNIQUE (object_id, ordinal_position)
)"""
ONTOLOGY_INDEX_DDL_STATEMENTS = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_ontology_objects_identity ON public.ontology_objects (schema_name, table_name)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_ontology_objects_table ON public.ontology_objects (layer, table_role, schema_name, table_name)",
    "CREATE INDEX IF NOT EXISTS idx_ontology_objects_lookup ON public.ontology_objects (layer, table_role, schema_name, table_name, version DESC)",
)
ONTOLOGY_DDL_STATEMENTS = (ONTOLOGY_OBJECTS_DDL, *ONTOLOGY_INDEX_DDL_STATEMENTS, ONTOLOGY_PROPERTIES_DDL)
ONTOLOGY_DDL = ";\n\n".join(ONTOLOGY_DDL_STATEMENTS) + ";\n"


def resource_group(layer: str) -> str:
    return "ingestion" if layer in {"rds", "ods"} else "processing" if layer in {"task", "dim", "dwd", "dws"} else "serving" if layer == "ads" else "other"


def _catalog_text(value: Any) -> str:
    return bytes(value).decode("utf-8") if isinstance(value, (bytes, bytearray, memoryview)) else str(value or "")


def _identity(schema: str, table: str) -> tuple[str, str]:
    if any(not isinstance(value, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", value) for value in (schema, table)):
        raise ValueError("Invalid Ontology object schema or table")
    if not schema.startswith("ts_") or schema in {"ts_meta", "ts_meta_hist"}:
        raise ValueError("This schema is not a business Ontology schema")
    return (schema[3:-5], "history") if schema.endswith("_hist") else (schema[3:], "current")


def _known_data_type(layer: str, table: str) -> str:
    logical = logical_table_name(layer, table)
    return logical if logical in TABLE_DEFINITIONS.get(layer, {}) and physical_table_name(layer, logical) == table else ""


def _constant_type(expression: str | None) -> str:
    # The database deparser is read; SQL defaults are never evaluated.
    match = re.fullmatch(r"'([a-z_][a-z0-9_]{0,49})'::(?:text|character varying|varchar)", expression or "")
    return match.group(1) if match else ""


async def _replace_properties(session, object_id: int, properties: list[dict[str, Any]], provenance: str) -> None:
    await session.execute(text("DELETE FROM public.ontology_object_properties WHERE object_id = :object_id"), {"object_id": object_id})
    if properties:
        await session.execute(text("""INSERT INTO public.ontology_object_properties (
            object_id, name, sql_type, ordinal_position, default_expression, is_nullable,
            is_primary_key, primary_key_position, identity_kind, generated_kind,
            generation_expression, comment, provenance)
            VALUES (:object_id, :name, :sql_type, :ordinal_position, :default_expression,
                :is_nullable, :is_primary_key, :primary_key_position, :identity_kind,
                :generated_kind, :generation_expression, :comment, :provenance)"""),
            [{**row, "object_id": object_id, "provenance": provenance} for row in properties])


async def sync_declared_object_properties(session, object_id: int, layer: str,
                                         logical_table: str, table_role: str = "current") -> bool:
    definition = TABLE_DEFINITIONS.get(layer, {}).get(logical_table) if table_role == "current" else None
    if not definition:
        return False  # Dynamic declarations are synchronized after their physical CREATE succeeds.
    primary = list(definition["primary_key"])
    properties = []
    for position, (name, declaration) in enumerate(definition["columns"].items(), 1):
        sql_type = re.split(r"\s+(?:NOT\s+NULL|NULL|DEFAULT|GENERATED|PRIMARY\s+KEY)\b", declaration, maxsplit=1, flags=re.I)[0].lower()
        sql_type = {"timestamptz": "timestamp with time zone", "varchar": "character varying"}.get(sql_type, sql_type)
        default = re.search(r"\bDEFAULT\s+(.+)$", declaration, re.I)
        properties.append({"name": name, "sql_type": sql_type, "ordinal_position": position,
            "default_expression": default.group(1) if default else None,
            "is_nullable": name not in primary and not bool(re.search(r"\bNOT\s+NULL\b", declaration, re.I)),
            "is_primary_key": name in primary, "primary_key_position": primary.index(name) + 1 if name in primary else None,
            "identity_kind": "", "generated_kind": "", "generation_expression": None, "comment": ""})
    await _replace_properties(session, object_id, properties, "table_definition")
    return True


async def sync_object_metadata(session, schema: str, table: str, *,
        data_type: str | None = None, data_type_source: str | None = None,
        description: str | None = None, description_source: str | None = None,
        provenance: str = "pg_catalog", ddl_sql: str | None = None,
        allow_missing: bool = False) -> dict[str, Any] | None:
    """Synchronize after DDL using the caller's transaction; never bootstrap or scan records."""
    layer, role = _identity(schema, table)
    identity = {"schema": schema, "table": table}
    result = await session.execute(text("""
        SELECT c.oid, obj_description(c.oid, 'pg_class') AS comment, p.partstrat,
               ARRAY(SELECT a.attname FROM unnest(p.partattrs) WITH ORDINALITY k(num, pos)
                     JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.num ORDER BY k.pos) AS partition_fields,
               (SELECT count(*) FROM pg_inherits i WHERE i.inhparent = c.oid) AS partition_count
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_partitioned_table p ON p.partrelid = c.oid
        WHERE n.nspname = :schema AND c.relname = :table AND c.relkind IN ('r','p')
          AND NOT c.relispartition AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid)
    """), identity)
    physical = result.mappings().first()
    if physical is None:
        if allow_missing:
            return None
        raise ValueError("The physical Ontology object does not exist or is a partition child")
    result = await session.execute(text("""
        SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS sql_type,
               a.attnum AS ordinal_position, NOT a.attnotnull AS is_nullable,
               CASE WHEN a.attgenerated = '' THEN pg_get_expr(d.adbin, d.adrelid) END AS default_expression,
               COALESCE(array_position(i.indkey::smallint[], a.attnum) < i.indnkeyatts, false) AS is_primary_key,
               CASE WHEN array_position(i.indkey::smallint[], a.attnum) < i.indnkeyatts
                    THEN array_position(i.indkey::smallint[], a.attnum) + 1 END AS primary_key_position,
               a.attidentity AS identity_kind, a.attgenerated AS generated_kind,
               CASE WHEN a.attgenerated <> '' THEN pg_get_expr(d.adbin, d.adrelid) END AS generation_expression,
               COALESCE(col_description(a.attrelid, a.attnum), '') AS comment
        FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        LEFT JOIN pg_index i ON i.indrelid = a.attrelid AND i.indisprimary
        WHERE a.attrelid = CAST(:oid AS oid) AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum
    """), {"oid": physical["oid"]})
    properties = [{**dict(row), "identity_kind": _catalog_text(row["identity_kind"]),
                   "generated_kind": _catalog_text(row["generated_kind"])} for row in result.mappings()]
    current = (await session.execute(text("SELECT * FROM public.ontology_objects WHERE schema_name = :schema AND table_name = :table FOR UPDATE"), identity)).mappings().first()
    current = dict(current) if current else {}
    if data_type is not None:
        semantic_type, type_source = data_type, data_type_source or ("registration" if data_type else "not_configured")
    elif current.get("data_type") and current.get("data_type_source") == "registration":
        semantic_type, type_source = current["data_type"], current["data_type_source"]
    else:
        expression = next((row["default_expression"] for row in properties if row["name"] == "data_type"), None)
        semantic_type = _constant_type(expression)
        type_source = "column_default" if semantic_type else "not_configured"
        if role == "current" and expression is None and not semantic_type and (current.get("ddl_sql") or ddl_sql):
            semantic_type = _known_data_type(layer, table)
            type_source = "table_definition" if semantic_type else "not_configured"
    physical_comment = physical["comment"] or ""
    declared_description = description if description is not None else current.get("description") or ""
    if physical_comment.strip():
        actual_description, desc_source = physical_comment, "table_comment"
    elif description is not None:
        actual_description = declared_description
        desc_source = description_source or ("registration" if declared_description.strip() else "not_configured")
    elif current.get("description_source") == "table_comment":
        actual_description, desc_source = "", "not_configured"
    else:
        actual_description = declared_description
        desc_source = current.get("description_source") if current.get("description_source") not in {None, "not_configured"} else "registration" if declared_description.strip() else "not_configured"
    version, checksum, managed_ddl = int(current.get("version") or 0), current.get("checksum") or "", current.get("ddl_sql") or ""
    layout = {key: current.get(key) for key in ("partition_type", "partition_column", "partition_granularity", "partition_count")}
    layout["partition_type"] = layout["partition_type"] or "none"
    if ddl_sql is not None:
        strategy = _catalog_text(physical["partstrat"])
        partition_type = {"h": "hash", "r": "range", "": "none"}.get(strategy)
        if partition_type is None:
            raise ValueError("This partition layout requires explicit DDL registration")
        fields = list(physical["partition_fields"] or [])
        column = ", ".join(fields) if fields else None
        if partition_type != "none" and not column:
            raise ValueError("Expression partition keys require explicit DDL registration")
        granularity = layout["partition_granularity"] if partition_type == "range" else None
        count = int(physical["partition_count"]) if partition_type == "hash" else None
        if (partition_type == "range" and granularity not in {"month", "year"}) or (partition_type == "hash" and not count):
            raise ValueError("Incomplete partition layout requires explicit DDL registration")
        layout = {"partition_type": partition_type, "partition_column": column,
                  "partition_granularity": granularity, "partition_count": count}
        next_checksum = ddl_checksum(ddl_sql, table_role=role, **layout)
        version = version if checksum == next_checksum else version + 1
        checksum, managed_ddl = next_checksum, ddl_sql
    params = {**identity, "layer": layer, "role": role, "ddl_sql": managed_ddl, "version": version,
              "checksum": checksum, **layout, "data_type": semantic_type, "data_type_source": type_source,
              "description": actual_description, "description_source": desc_source,
              "provenance": provenance, "resource_group": resource_group(layer)}
    result = await session.execute(text("""
        INSERT INTO public.ontology_objects (layer, table_role, schema_name, table_name,
            ddl_sql, version, checksum, partition_type, partition_column, partition_granularity,
            partition_count, data_type, data_type_source, description, description_source, provenance, resource_group)
        VALUES (:layer, :role, :schema, :table, :ddl_sql, :version, :checksum, :partition_type,
            :partition_column, :partition_granularity, :partition_count, :data_type,
            :data_type_source, :description, :description_source, :provenance, :resource_group)
        ON CONFLICT (schema_name, table_name) DO UPDATE SET layer = EXCLUDED.layer,
            table_role = EXCLUDED.table_role, ddl_sql = EXCLUDED.ddl_sql, version = EXCLUDED.version,
            checksum = EXCLUDED.checksum, partition_type = EXCLUDED.partition_type,
            partition_column = EXCLUDED.partition_column, partition_granularity = EXCLUDED.partition_granularity,
            partition_count = EXCLUDED.partition_count, data_type = EXCLUDED.data_type,
            data_type_source = EXCLUDED.data_type_source, description = EXCLUDED.description,
            description_source = EXCLUDED.description_source, provenance = EXCLUDED.provenance,
            resource_group = EXCLUDED.resource_group, updated_at = now() RETURNING id
    """), params)
    object_id = int(result.scalar_one())
    await _replace_properties(session, object_id, properties, "pg_catalog")
    return {"id": object_id, "schema_name": schema, "table_name": table, "data_type": semantic_type,
            "version": version, "checksum": checksum, "properties_count": len(properties)}
