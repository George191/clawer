"""Explicit, transactional migration from DDL registration to the public Ontology catalog."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.etl.schema.ontology import (
    ONTOLOGY_DDL_STATEMENTS,
    ONTOLOGY_OBJECT_COLUMNS_ADD_STATEMENTS,
    ONTOLOGY_OBJECT_DEFAULT_STATEMENTS,
    sync_object_metadata,
)
from app.etl.schema.object_relations import OBJECT_RELATIONS_DDL_STATEMENTS
from app.storage.postgres_client import get_pg_client

OLD_OBJECTS = "ts_meta.ddlregistry"
OBJECTS = "public.ontology_objects"
PROPERTIES = "public.ontology_object_properties"
RELATIONS = "public.ontology_object_relations"
OLD_RELATIONS = "ts_meta.object_relations"
IDENTITY_COLUMNS = "id,layer,table_role,schema_name,table_name,ddl_sql,version,checksum,partition_type,partition_column,partition_granularity,partition_count"


async def exists(session, name: str) -> bool:
    return bool((await session.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": name})).scalar_one())


async def rows(session, statement: str, params=None) -> list[dict]:
    return [dict(row) for row in (await session.execute(text(statement), params or {})).mappings()]


def fingerprint(values: list[dict]) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def identifier(value: str) -> str:
    if len(value.encode("utf-8")) > 63:
        raise ValueError("Migrated metadata identifier exceeds PostgreSQL's limit")
    return '"' + value.replace('"', '""') + '"'


async def snapshot(session) -> dict:
    installed = {name: await exists(session, name) for name in (OLD_OBJECTS, OBJECTS, PROPERTIES, OLD_RELATIONS, RELATIONS)}
    if installed[OLD_OBJECTS] and installed[OBJECTS]:
        raise RuntimeError("Both object registries exist; reconcile their definitions before migration")
    if installed[OLD_RELATIONS] and installed[RELATIONS]:
        raise RuntimeError("Both relationship registries exist; reconcile their definitions before migration")
    source = OLD_OBJECTS if installed[OLD_OBJECTS] else OBJECTS if installed[OBJECTS] else None
    registered = await rows(session, f"SELECT {IDENTITY_COLUMNS} FROM {source} ORDER BY id") if source else []
    tables = await rows(session, """
        SELECT n.nspname AS schema, c.relname AS name,
            (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = c.oid
                AND a.attnum > 0 AND NOT a.attisdropped) AS fields
        FROM pg_namespace n JOIN pg_class c ON c.relnamespace = n.oid
        WHERE n.nspname ~ '^ts_[a-z0-9_]+$' AND n.nspname NOT IN ('ts_meta','ts_meta_hist')
          AND c.relkind IN ('r','p') AND NOT c.relispartition
          AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid)
        ORDER BY n.nspname, c.relname
    """)
    return {"installed": installed, "source": source, "registered": registered, "tables": tables,
            "registration_fingerprint": fingerprint(registered)}


async def migrate(pg, *, apply: bool = False) -> dict:
    async with pg.session() as session:
        if apply:
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('public.ontology_metadata_migration'))"))
            await session.execute(text("SET LOCAL lock_timeout = '5s'"))
            await session.execute(text("SET LOCAL statement_timeout = '60s'"))
        else:
            await session.execute(text("SET TRANSACTION READ ONLY"))
        before = await snapshot(session)
        report = {"applied": apply, "installed": before["installed"],
                  "registered_objects": len(before["registered"]), "physical_objects": len(before["tables"]),
                  "physical_properties": sum(int(table["fields"]) for table in before["tables"]),
                  "registration_fingerprint_before": before["registration_fingerprint"],
                  "business_rows_read": 0, "business_rows_written": 0}
        if not apply:
            return report
        if before["installed"][OLD_OBJECTS]:
            await session.execute(text("ALTER TABLE ts_meta.ddlregistry SET SCHEMA public"))
            await session.execute(text("ALTER TABLE public.ddlregistry RENAME TO ontology_objects"))
            # Rename the old catalog's own objects, preserving their OIDs, ACLs and ownership.
            constraints = await rows(session, "SELECT conname FROM pg_constraint WHERE conrelid = 'public.ontology_objects'::regclass")
            for value in constraints:
                name = value["conname"]
                if "ddlregistry" in name:
                    await session.execute(text(f'ALTER TABLE public.ontology_objects RENAME CONSTRAINT {identifier(name)} TO {identifier(name.replace("ddlregistry", "ontology_objects"))}'))
            indexes = await rows(session, """
                SELECT index_class.relname AS name FROM pg_index i
                JOIN pg_class index_class ON index_class.oid = i.indexrelid
                WHERE i.indrelid = 'public.ontology_objects'::regclass
            """)
            for value in indexes:
                name = value["name"]
                if "ddlregistry" in name:
                    await session.execute(text(f'ALTER INDEX public.{identifier(name)} RENAME TO {identifier(name.replace("ddlregistry", "ontology_objects"))}'))
            if await exists(session, "public.ddlregistry_id_seq"):
                await session.execute(text("ALTER SEQUENCE public.ddlregistry_id_seq RENAME TO ontology_objects_id_seq"))
        for statement in ONTOLOGY_DDL_STATEMENTS:
            await session.execute(text(statement))
        for statement in ONTOLOGY_OBJECT_COLUMNS_ADD_STATEMENTS:
            await session.execute(text(statement))
        for statement in ONTOLOGY_OBJECT_DEFAULT_STATEMENTS:
            await session.execute(text(statement))
        if before["installed"][OLD_RELATIONS]:
            await session.execute(text("ALTER TABLE ts_meta.object_relations SET SCHEMA public"))
            await session.execute(text("ALTER TABLE public.object_relations RENAME TO ontology_object_relations"))
        for statement in OBJECT_RELATIONS_DDL_STATEMENTS:
            await session.execute(text(statement))
        for table in before["tables"]:
            await sync_object_metadata(session, table["schema"], table["name"], provenance="pg_catalog")
        after = await snapshot(session)
        by_id = {row["id"]: row for row in after["registered"]}
        if any(by_id.get(row["id"]) != row for row in before["registered"]):
            raise RuntimeError("Migration changed an existing object's identity or registered DDL")
        counts = await rows(session, """
            SELECT o.schema_name AS schema, o.table_name AS name, count(p.name) AS fields
            FROM public.ontology_objects o LEFT JOIN public.ontology_object_properties p ON p.object_id = o.id
            GROUP BY o.id, o.schema_name, o.table_name
        """)
        actual = {(row["schema"], row["name"]): int(row["fields"]) for row in counts}
        if any(actual.get((table["schema"], table["name"])) != table["fields"] for table in before["tables"]):
            raise RuntimeError("Ontology property counts do not match the physical table catalog")
        if after["installed"][OLD_OBJECTS] or after["installed"][OLD_RELATIONS]:
            raise RuntimeError("A superseded metadata table remains")
        report.update(installed=after["installed"], registered_objects_after=len(after["registered"]),
                      existing_registration_preserved=True, physical_property_counts_verified=True)
        return report


async def run(apply: bool) -> int:
    pg = get_pg_client()
    try:
        await pg.connect(max_retries=1)
        result = await migrate(pg, apply=apply)
        print(json.dumps(result))
        return 0
    finally:
        await pg.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Inspect the migration plan without writes")
    group.add_argument("--apply", action="store_true", help="Migrate the catalog and persist actual table properties atomically")
    args = parser.parse_args()
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
