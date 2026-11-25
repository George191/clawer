"""Explicitly add Object Type visibility and per-user favorites to the public Ontology catalog."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.etl.schema.ontology import ONTOLOGY_FAVORITES_DDL
from app.storage.postgres_client import get_pg_client


VISIBILITY_DDL = """ALTER TABLE app.ontology_objects ADD COLUMN visibility
    TEXT NOT NULL DEFAULT 'normal' CONSTRAINT ontology_objects_visibility_check
    CHECK (visibility IN ('normal','prominent','hidden'))"""


async def definition(session) -> dict | None:
    row = (await session.execute(text("""
        SELECT format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull AS not_null,
               pg_get_expr(d.adbin, d.adrelid) AS default_expression,
               pg_get_constraintdef(k.oid) AS constraint_definition, k.convalidated AS validated
        FROM pg_attribute a
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        LEFT JOIN pg_constraint k ON k.conrelid = a.attrelid
            AND k.conname = 'ontology_objects_visibility_check' AND k.contype = 'c'
        WHERE a.attrelid = to_regclass('app.ontology_objects')
          AND a.attname = 'visibility' AND a.attnum > 0 AND NOT a.attisdropped
    """))).mappings().first()
    return dict(row) if row else None


def ready(row: dict | None) -> bool:
    if not row:
        return False
    check = re.sub(r"[\s()]|::text", "", row.get("constraint_definition") or "")
    return (row["type"] == "text" and row["not_null"] and
            row["default_expression"] == "'normal'::text" and row["validated"] and
            check == "CHECKvisibility=ANYARRAY['normal','prominent','hidden']")


async def favorites_definition(session) -> dict | None:
    row = (await session.execute(text("""
        SELECT c.relkind = 'r' AND NOT c.relispartition AS ordinary_table,
            EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'user_id'
                AND a.atttypid = 'uuid'::regtype AND a.attnotnull AND NOT a.attisdropped) AS user_id,
            EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attname = 'object_id'
                AND a.atttypid = 'bigint'::regtype AND a.attnotnull AND NOT a.attisdropped) AS object_id,
            EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                WHERE a.attrelid = c.oid AND a.attname = 'created_at' AND a.atttypid = 'timestamptz'::regtype
                AND a.attnotnull AND NOT a.attisdropped AND pg_get_expr(d.adbin, d.adrelid) = 'now()') AS created_at,
            EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conrelid = c.oid AND k.contype = 'p'
                AND k.conkey = ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid = c.oid AND attname = 'user_id'),
                    (SELECT attnum FROM pg_attribute WHERE attrelid = c.oid AND attname = 'object_id')]::smallint[]) AS primary_key,
            EXISTS (SELECT 1 FROM pg_constraint k JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'object_id'
                JOIN pg_attribute target ON target.attrelid = to_regclass('app.ontology_objects') AND target.attname = 'id'
                WHERE k.conrelid = c.oid AND k.contype = 'f' AND k.confdeltype = 'c' AND k.convalidated
                AND k.confrelid = target.attrelid AND k.conkey = ARRAY[a.attnum]::smallint[]
                AND k.confkey = ARRAY[target.attnum]::smallint[]) AS object_foreign_key
        FROM pg_class c WHERE c.oid = to_regclass('app.ontology_object_favorites')
    """))).mappings().first()
    return dict(row) if row else None


def favorites_ready(row: dict | None) -> bool:
    return bool(row) and all(row.values())


async def migrate(pg, *, apply: bool = False) -> dict:
    async with pg.session() as session:
        if apply:
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('app.ontology_visibility_migration'))"))
            await session.execute(text("SET LOCAL lock_timeout = '5s'"))
            await session.execute(text("SET LOCAL statement_timeout = '30s'"))
        else:
            await session.execute(text("SET TRANSACTION READ ONLY"))
        installed = bool((await session.execute(text(
            "SELECT to_regclass('app.ontology_objects') IS NOT NULL"))).scalar_one())
        if not installed:
            raise RuntimeError("Initialize the app Ontology catalog before adding visibility")
        before = await definition(session)
        if before and not ready(before):
            raise RuntimeError("Existing visibility definition differs from the expected contract; no settings were changed")
        favorites = bool((await session.execute(text(
            "SELECT to_regclass('app.ontology_object_favorites') IS NOT NULL"))).scalar_one())
        favorite_contract = await favorites_definition(session) if favorites else None
        if favorites and not favorites_ready(favorite_contract):
            raise RuntimeError("Existing favorites definition differs from the expected contract; no settings were changed")
        changes = ([] if before else ["add_visibility"]) + ([] if favorites else ["create_favorites"])
        if apply and before is None:
            await session.execute(text(VISIBILITY_DDL))
        if apply and not favorites:
            await session.execute(text(ONTOLOGY_FAVORITES_DDL))
            favorite_contract = await favorites_definition(session)
        after = await definition(session) if apply and before is None else before
        if apply and not (ready(after) and favorites_ready(favorite_contract)):
            raise RuntimeError("Ontology visibility/favorites migration verification failed")
        return {"applied": apply, "ready": ready(after) and favorites_ready(favorite_contract), "changes": changes,
                "business_rows_read": 0, "business_rows_written": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    async def run():
        pg = get_pg_client()
        try:
            await pg.connect(max_retries=1)
            return await migrate(pg, apply=args.apply)
        finally:
            await pg.close()

    report = asyncio.run(run())
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
