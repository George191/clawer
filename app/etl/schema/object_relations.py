"""Declared table relationships; installation/seed writes are explicit, preview reads are not."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from app.etl.schema.catalog_metadata import OBJECT_RELATION, RELATION_RECORDS, catalog_parameters

RELATION_TABLE = "app.ontology_object_relations"
CARDINALITIES = {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}
_FIELDS = ("name", "source_schema", "source_table", "source_fields", "target_schema",
           "target_table", "target_fields", "relationship_kind", "cardinality", "description",
           "provenance", "enabled")
RELATION_IMPORT_FIELDS_CHECK = """cardinality(source_fields) = cardinality(target_fields)
    AND (cardinality(source_fields) = 0 OR
        (array_ndims(source_fields) = 1 AND array_ndims(target_fields) = 1))
    AND array_position(source_fields, NULL) IS NULL
    AND array_position(target_fields, NULL) IS NULL"""
OBJECT_RELATIONS_DDL_STATEMENTS = (
    "CREATE SCHEMA IF NOT EXISTS app",
    f"""CREATE TABLE IF NOT EXISTS app.ontology_object_relations (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        source_schema TEXT NOT NULL,
        source_table TEXT NOT NULL,
        source_fields TEXT[] NOT NULL,
        target_schema TEXT NOT NULL,
        target_table TEXT NOT NULL,
        target_fields TEXT[] NOT NULL,
        relationship_kind TEXT NOT NULL,
        cardinality TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        provenance TEXT NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (source_schema ~ '^ts_[a-z0-9_]{{1,60}}$'
            AND source_schema NOT IN ('ts_meta','ts_meta_hist')),
        CHECK (target_schema ~ '^ts_[a-z0-9_]{{1,60}}$'
            AND target_schema NOT IN ('ts_meta','ts_meta_hist')),
        CHECK (source_table ~ '^[a-z_][a-z0-9_]{{0,62}}$'),
        CHECK (target_table ~ '^[a-z_][a-z0-9_]{{0,62}}$'),
        CONSTRAINT ck_ontology_relation_import_fields CHECK ({RELATION_IMPORT_FIELDS_CHECK}),
        CHECK (cardinality IN ('one_to_one','one_to_many','many_to_one','many_to_many')),
        CHECK (length(provenance) > 0)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_ontology_object_relations_target
        ON app.ontology_object_relations (target_schema, target_table) WHERE enabled""",
    """CREATE INDEX IF NOT EXISTS idx_ontology_object_relations_source
        ON app.ontology_object_relations (source_schema, source_table) WHERE enabled""",
)
OBJECT_RELATIONS_DDL = ";\n\n".join(OBJECT_RELATIONS_DDL_STATEMENTS) + ";\n"


class ObjectRelationsNotInitialized(RuntimeError):
    pass


def relation_mapping(row: dict[str, Any]) -> tuple:
    """Compare column pairs independently of order and of declared relationship direction."""
    source = (row["source_schema"], row["source_table"])
    target = (row["target_schema"], row["target_table"])
    pairs = tuple(sorted(zip(row["columns"], row["referenced_columns"])))
    reverse = tuple(sorted((right, left) for left, right in pairs))
    return min((source, target, pairs), (target, source, reverse))


def validate_definition(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - set(_FIELDS):
        raise ValueError("A relationship must contain only the documented metadata fields")
    row = {"description": "", "enabled": True, **value}
    name = row.get("name")
    if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 128 or re.search(r"[\x00-\x1f\x7f]", name):
        raise ValueError("A relationship name must contain 1–128 visible characters")
    if not isinstance(row.get("relationship_kind"), str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,127}", row["relationship_kind"]):
        raise ValueError("Invalid relationship relationship_kind")
    for side in ("source", "target"):
        schema, table = row.get(f"{side}_schema"), row.get(f"{side}_table")
        if not isinstance(schema, str) or not re.fullmatch(r"ts_[a-z0-9_]{1,60}", schema) or schema in {"ts_meta", "ts_meta_hist"}:
            raise ValueError(f"Invalid relationship {side} schema")
        if not isinstance(table, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", table):
            raise ValueError(f"Invalid relationship {side} table")
        fields = row.get(f"{side}_fields")
        if not isinstance(fields, list) or not fields or any(not isinstance(field, str) or
                not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", field) for field in fields) or len(fields) != len(set(fields)):
            raise ValueError(f"Invalid ordered relationship {side} fields")
    if len(row["source_fields"]) != len(row["target_fields"]):
        raise ValueError("Relationship field lists must have equal lengths")
    if not isinstance(row.get("cardinality"), str) or row["cardinality"] not in CARDINALITIES:
        raise ValueError("Invalid declared relationship cardinality")
    if not isinstance(row["description"], str) or len(row["description"]) > 4000:
        raise ValueError("Invalid relationship description")
    if not isinstance(row.get("provenance"), str) or not row["provenance"].strip() or len(row["provenance"]) > 2000:
        raise ValueError("Relationship provenance is required")
    if not isinstance(row["enabled"], bool):
        raise ValueError("Relationship enabled must be boolean")
    return row


async def _installed(session) -> bool:
    return bool((await session.execute(text("SELECT to_regclass(:table_name) IS NOT NULL"),
                                      {"table_name": RELATION_TABLE})).scalar_one())


async def read_object_relations(session, schema: str, table: str, *, catalog_session) -> list[dict[str, Any]]:
    if not await _installed(session):
        raise ObjectRelationsNotInitialized("Logical relationship registry is not initialized")
    params = await catalog_parameters({"schema": schema, "table": table}, session=session, relations=True)
    result = await catalog_session.execute(text(f"""
        SELECT r.name, r.source_schema, r.source_table, r.source_fields AS columns,
               r.target_schema, r.target_table, r.target_fields AS referenced_columns,
               r.relationship_kind, r.cardinality, r.description, r.provenance,
               so.data_type AS source_data_type, target_object.data_type AS target_data_type,
               COALESCE(so.visibility, 'normal') AS source_visibility, COALESCE(target_object.visibility, 'normal') AS target_visibility
        FROM {RELATION_RECORDS}
        JOIN pg_namespace sn ON sn.nspname = r.source_schema
        JOIN pg_class sc ON sc.relnamespace = sn.oid AND sc.relname = r.source_table
        JOIN pg_namespace tn ON tn.nspname = r.target_schema
        JOIN pg_class tc ON tc.relnamespace = tn.oid AND tc.relname = r.target_table
        LEFT JOIN {OBJECT_RELATION} so ON so.schema_name = sn.nspname
            AND so.table_name = sc.relname AND so.is_active
        LEFT JOIN {OBJECT_RELATION} target_object ON target_object.schema_name = tn.nspname
            AND target_object.table_name = tc.relname AND target_object.is_active
        WHERE r.enabled AND ((r.source_schema = :schema AND r.source_table = :table)
                         OR (r.target_schema = :schema AND r.target_table = :table))
          AND sn.nspname ~ '^ts_[a-z0-9_]+$' AND sn.nspname NOT IN ('ts_meta','ts_meta_hist')
          AND tn.nspname ~ '^ts_[a-z0-9_]+$' AND tn.nspname NOT IN ('ts_meta','ts_meta_hist')
          AND sc.relkind IN ('r','p') AND tc.relkind IN ('r','p')
          AND NOT sc.relispartition AND NOT tc.relispartition
          AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = sc.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = tc.oid)
          AND cardinality(r.source_fields) = cardinality(r.target_fields)
          AND NOT EXISTS (SELECT 1 FROM unnest(r.source_fields) f(name)
              WHERE NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = sc.oid
                  AND a.attname = f.name AND a.attnum > 0 AND NOT a.attisdropped))
          AND NOT EXISTS (SELECT 1 FROM unnest(r.target_fields) f(name)
              WHERE NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = tc.oid
                  AND a.attname = f.name AND a.attnum > 0 AND NOT a.attisdropped))
        ORDER BY r.name
    """), params)
    return [{**dict(row), "source_kind": "logical"} for row in result.mappings()]


def merge_object_relations(foreign_keys: list[dict[str, Any]], logical: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple, dict[str, Any]] = {}
    for foreign in foreign_keys:
        merged.setdefault(relation_mapping(foreign), {**foreign, "source_kind": "foreign_key",
            "relationship_kind": "foreign_key", "cardinality": None, "description": "",
            "provenance": "pg_constraint"})
    inverse = {"one_to_many": "many_to_one", "many_to_one": "one_to_many"}
    for relation in logical:
        key = relation_mapping(relation)
        if key not in merged:
            merged[key] = dict(relation)
            continue
        existing = merged[key]
        if existing["source_kind"] != "foreign_key":
            continue
        same_direction = (existing["source_schema"], existing["source_table"],
                          sorted(zip(existing["columns"], existing["referenced_columns"]))) == (
                          relation["source_schema"], relation["source_table"],
                          sorted(zip(relation["columns"], relation["referenced_columns"])))
        existing.update(relationship_kind=relation["relationship_kind"], description=relation["description"],
                        cardinality=relation["cardinality"] if same_direction else inverse.get(relation["cardinality"], relation["cardinality"]),
                        provenance="pg_constraint; " + relation["provenance"])
    return list(merged.values())


async def manage_object_relations(pg, definitions: list[dict[str, Any]], *, catalog_pg, apply: bool = False) -> dict[str, Any]:
    """Validate declared metadata; only explicit apply writes, all in one existing PG transaction."""
    rows = [validate_definition(value) for value in definitions]
    if len({row["name"] for row in rows}) != len(rows):
        raise ValueError("Relationship names must be distinct")
    mappings = [relation_mapping({**row, "columns": row["source_fields"],
                                 "referenced_columns": row["target_fields"]}) for row in rows]
    if len(set(mappings)) != len(mappings):
        raise ValueError("Duplicate relationship field mapping, including a reverse definition")
    async with pg.session() as session, catalog_pg.session() as catalog_session:
        await catalog_session.execute(text("SET TRANSACTION READ ONLY"))
        if apply:
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('app.ontology_object_relations'))"))
        else:
            await session.execute(text("SET TRANSACTION READ ONLY"))
        installed = await _installed(session)
        # Validate physical metadata only; cardinality is declared, never inferred from business rows.
        for row in rows:
            for side in ("source", "target"):
                result = await catalog_session.execute(text("""
                    SELECT a.attname AS name FROM pg_namespace n
                    JOIN pg_class c ON c.relnamespace = n.oid
                    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                    WHERE n.nspname = :schema AND c.relname = :table
                      AND c.relkind IN ('r','p') AND NOT c.relispartition
                      AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid)
                """), {"schema": row[f"{side}_schema"], "table": row[f"{side}_table"]})
                names = {item["name"] for item in result.mappings()}
                if not set(row[f"{side}_fields"]).issubset(names):
                    raise ValueError(f'Relationship {row["name"]} has a missing table or {side} field')
        existing = {}
        if installed:
            result = await session.execute(text("SELECT " + ", ".join(_FIELDS) + " FROM app.ontology_object_relations ORDER BY name"))
            existing = {item["name"]: dict(item) for item in result.mappings()}
        existing_mappings = {relation_mapping({**row, "columns": row["source_fields"],
            "referenced_columns": row["target_fields"]}): name for name, row in existing.items()}
        for row, mapping in zip(rows, mappings):
            if mapping in existing_mappings and existing_mappings[mapping] != row["name"]:
                raise ValueError(f'Relationship mapping already belongs to {existing_mappings[mapping]}')
        changes = {"insert": [], "update": [], "unchanged": []}
        for row in rows:
            status = "insert" if row["name"] not in existing else "unchanged" if row == existing[row["name"]] else "update"
            changes[status].append(row["name"])
        if apply:
            for statement in OBJECT_RELATIONS_DDL_STATEMENTS:
                await session.execute(text(statement))
            for row in rows:
                if row["name"] in changes["unchanged"]:
                    continue
                await session.execute(text("""
                    INSERT INTO app.ontology_object_relations (name, source_schema, source_table, source_fields,
                        target_schema, target_table, target_fields, relationship_kind, cardinality,
                        description, provenance, enabled)
                    VALUES (:name, :source_schema, :source_table, CAST(:source_fields AS text[]),
                        :target_schema, :target_table, CAST(:target_fields AS text[]), :relationship_kind,
                        :cardinality, :description, :provenance, :enabled)
                    ON CONFLICT (name) DO UPDATE SET source_schema = EXCLUDED.source_schema,
                        source_table = EXCLUDED.source_table, source_fields = EXCLUDED.source_fields,
                        target_schema = EXCLUDED.target_schema, target_table = EXCLUDED.target_table,
                        target_fields = EXCLUDED.target_fields, relationship_kind = EXCLUDED.relationship_kind,
                        cardinality = EXCLUDED.cardinality, description = EXCLUDED.description,
                        provenance = EXCLUDED.provenance, enabled = EXCLUDED.enabled, updated_at = now()
                """), row)
        return {"installed": installed or apply, "applied": apply, "definitions": len(rows), "changes": changes}
