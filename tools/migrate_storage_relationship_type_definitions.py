"""Explicitly allow semantic relationship types without changing definitions or links."""

import argparse
import asyncio
import json
from pathlib import Path
import re
import sys

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.etl.schema.object_relations import RELATION_IMPORT_FIELDS_CHECK
from app.storage.postgres_client import get_pg_client


LEGACY_IMPORT_FIELDS_CHECK = """cardinality(source_fields) > 0
    AND cardinality(source_fields) = cardinality(target_fields)
    AND array_ndims(source_fields) = 1 AND array_ndims(target_fields) = 1
    AND array_position(source_fields, NULL) IS NULL
    AND array_position(target_fields, NULL) IS NULL"""
ENDPOINT_FIELDS = ("source_schema", "source_table", "source_fields", "target_schema", "target_table", "target_fields")


def _expression(value):
    return re.sub(r"[\s()]+", "", value.lower().removeprefix("check").replace("::text", ""))


async def _constraints(session):
    return (await session.execute(text("""SELECT c.conname::text AS name, c.contype::text AS kind,
        pg_get_constraintdef(c.oid) AS definition,
        ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(attnum,position)
            JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.attnum ORDER BY k.position) AS columns
        FROM pg_constraint c WHERE c.conrelid='app.ontology_object_relations'::regclass ORDER BY c.conname"""))).mappings().all()


def _plan(constraints):
    field_checks = [row for row in constraints if row["kind"] == "c" and set(row["columns"]) == {"source_fields", "target_fields"}]
    if len(field_checks) != 1:
        raise ValueError("Expected one reviewed relationship import-field constraint")
    field_check = field_checks[0]
    expression = _expression(field_check["definition"])
    if expression not in {_expression(LEGACY_IMPORT_FIELDS_CHECK), _expression(RELATION_IMPORT_FIELDS_CHECK)}:
        raise ValueError("Relationship import-field constraint differs from the reviewed contract")
    endpoint_unique = [row for row in constraints if row["kind"] == "u" and set(row["columns"]) == set(ENDPOINT_FIELDS)]
    if len(endpoint_unique) > 1:
        raise ValueError("Ambiguous relationship endpoint uniqueness constraints")
    if not any(row["kind"] == "u" and list(row["columns"]) == ["name"] for row in constraints):
        raise ValueError("The relationship name uniqueness constraint is missing")
    return field_check if expression == _expression(LEGACY_IMPORT_FIELDS_CHECK) else None, endpoint_unique


async def _snapshot(session):
    result = {}
    for table in ("ontology_object_relations", "ontology_object_links"):
        row = (await session.execute(text(f"""SELECT count(*) AS rows,
            md5(COALESCE(string_agg(md5(to_jsonb(t)::text), '' ORDER BY id), '')) AS md5
            FROM public.{table} t"""))).mappings().one()
        result[table] = dict(row)
    return result


async def migrate_relationship_type_definitions(session, *, apply=False):
    if not apply:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL lock_timeout='3s'"))
    await session.execute(text("SET LOCAL statement_timeout='30s'"))
    await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    installed = await session.scalar(text("""SELECT to_regclass('app.ontology_object_relations') IS NOT NULL
        AND to_regclass('app.ontology_object_links') IS NOT NULL"""))
    if not installed:
        raise ValueError("Stored relationship metadata must be initialized before this migration")
    if apply:
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('app.ontology_object_relations'))"))
        await session.execute(text("LOCK TABLE app.ontology_object_relations IN ACCESS EXCLUSIVE MODE"))
        await session.execute(text("LOCK TABLE app.ontology_object_links IN SHARE MODE"))
    before = await _snapshot(session)
    old_check, endpoint_unique = _plan(await _constraints(session))
    required = bool(old_check or endpoint_unique)
    changed = []
    if apply:
        # The names come only from constraints whose exact reviewed definition/columns were checked above.
        for row in ([old_check] if old_check else []) + endpoint_unique:
            quoted_name = '"' + row["name"].replace('"', '""') + '"'
            await session.execute(text(f"ALTER TABLE app.ontology_object_relations DROP CONSTRAINT {quoted_name}"))
            changed.append(row["name"])
        if old_check:
            await session.execute(text("ALTER TABLE app.ontology_object_relations ADD CONSTRAINT "
                "ck_ontology_relation_import_fields CHECK (" + RELATION_IMPORT_FIELDS_CHECK + ")"))
        remaining_check, remaining_unique = _plan(await _constraints(session))
        if remaining_check or remaining_unique:
            raise ValueError("Relationship definition migration did not reach its reviewed schema")
    after = await _snapshot(session)
    if before != after:
        raise ValueError("Relationship definitions or stored link rows changed during the schema migration")
    return {"applied": apply, "migrationRequired": required, "changedConstraints": changed,
            "definitionsAndLinksUnchanged": True, "before": before, "after": after}


async def run(apply):
    pg = get_pg_client()
    await pg.connect(max_retries=1)
    try:
        async with pg.session() as session:
            result = await migrate_relationship_type_definitions(session, apply=apply)
        print(json.dumps(result))
    finally:
        await pg.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(run(parser.parse_args().apply))
