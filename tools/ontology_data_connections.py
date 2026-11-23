"""Explicitly install the encrypted external source registry; never reads external business data."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.etl.schema.data_connections import (
    DATA_CONNECTIONS_DDL, DATA_CONNECTIONS_ENGINE_DDL, DATA_CONNECTIONS_PAUSE_DDL, ENGINE_CHECK_INSTALLED,
)
from app.storage.postgres_client import get_pg_client


EXPECTED = {"id": "uuid", "name": "text", "engine": "text", "config": "jsonb",
            "credentials_ciphertext": "text", "created_by": "text", "created_at": "timestamp with time zone",
            "updated_at": "timestamp with time zone", "tested_at": "timestamp with time zone"}
PAUSED_CONTRACT = {**EXPECTED, "paused_at": "timestamp with time zone"}


async def required_changes(session):
    rows = (await session.execute(text("""SELECT a.attname AS name,
        format_type(a.atttypid,a.atttypmod) AS type, a.attnotnull AS not_null
        FROM pg_attribute a WHERE a.attrelid=to_regclass('public.ontology_data_connections')
        AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum"""))).mappings().all()
    if not rows:
        return ["create_external_source_registry"]
    actual = {row["name"]: row["type"] for row in rows}
    legacy = actual == EXPECTED and all(row["not_null"] for row in rows)
    upgraded = actual == PAUSED_CONTRACT and all(
        row["not_null"] == (row["name"] not in ("tested_at", "paused_at")) for row in rows
    )
    if not (legacy or upgraded):
        raise RuntimeError("Existing external source registry differs from its expected column contract")
    primary = (await session.execute(text("""SELECT EXISTS (SELECT 1 FROM pg_constraint k
        JOIN pg_attribute a ON a.attrelid=k.conrelid AND a.attname='id'
        WHERE k.conrelid=to_regclass('public.ontology_data_connections') AND k.contype='p'
        AND k.conkey=ARRAY[a.attnum]::smallint[])"""))).scalar_one()
    if not primary:
        raise RuntimeError("External source registry is missing its UUID primary key")
    changes = ["enable_postgresql_source_pause"] if legacy else []
    if not await session.scalar(text(ENGINE_CHECK_INSTALLED)):
        changes.append("expand_source_engines")
    return changes


async def migrate(pg, *, apply=False):
    async with pg.session() as session:
        if apply:
            await session.execute(text("SET LOCAL lock_timeout='5s'"))
            await session.execute(text("SET LOCAL statement_timeout='30s'"))
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('public.ontology_data_connections_migration'))"))
        else:
            await session.execute(text("SET TRANSACTION READ ONLY"))
        changes = await required_changes(session)
        if apply:
            statements = {"create_external_source_registry": DATA_CONNECTIONS_DDL,
                          "enable_postgresql_source_pause": DATA_CONNECTIONS_PAUSE_DDL,
                          "expand_source_engines": DATA_CONNECTIONS_ENGINE_DDL}
            for change in changes:
                await session.execute(text(statements[change]))
        ready = not (await required_changes(session) if apply else changes)
        return {"applied": apply, "ready": ready, "changes": changes,
                "business_rows_read": 0, "business_rows_written": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()

    async def run():
        pg = get_pg_client()
        try:
            await pg.connect(max_retries=1)
            return await migrate(pg, apply=arguments.apply)
        finally:
            await pg.close()

    report = asyncio.run(run())
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
