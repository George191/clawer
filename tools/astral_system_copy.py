"""Stream the selected PostgreSQL tables between databases under one exported snapshot."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import asyncpg

WORKERS = 6
BATCH_SIZE = 3000


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def _connect(dsn: str):
    return await asyncpg.connect(
        dsn,
        timeout=12,
        server_settings={"application_name": "astral-public-direct", "lock_timeout": "3000"},
    )


async def _copy_tables(spec: dict[str, Any], tables: list[dict[str, Any]]) -> dict[str, int]:
    source = await _connect(spec["source_dsn"])
    target = await _connect(spec["target_dsn"])
    results: dict[str, int] = {}
    try:
        async with source.transaction(isolation="repeatable_read", readonly=True):
            await source.execute(f"SET TRANSACTION SNAPSHOT '{spec['snapshot']}'")
            for table in tables:
                name, columns = table["name"], table["columns"]
                selection = ", ".join(map(_identifier, columns))
                qualified = f"public.{_identifier(name)}"
                total = 0
                async with target.transaction():
                    cursor = await source.cursor(f"SELECT {selection} FROM {qualified}")
                    while batch := await cursor.fetch(BATCH_SIZE):
                        status = await target.copy_records_to_table(
                            name, schema_name="public", columns=columns,
                            records=[tuple(row) for row in batch],
                        )
                        if status != f"COPY {len(batch)}":
                            raise RuntimeError(f"PostgreSQL did not acknowledge the full batch for {name}")
                        total += len(batch)
                        if total % 300_000 == 0:
                            print(json.dumps({"table": name, "streamed_rows": total}), flush=True)
                results[name] = total
    finally:
        await target.close()
        await source.close()
    return results


def _run_worker(spec: dict[str, Any], tables: list[dict[str, Any]]) -> dict[str, int]:
    return asyncio.run(_copy_tables(spec, tables))


async def _plan(spec: dict[str, Any]) -> tuple[list[list[dict[str, Any]]], list[tuple[str, int, bool]]]:
    source = await _connect(spec["source_dsn"])
    target = await _connect(spec["target_dsn"])
    try:
        if await source.fetchval("SELECT current_database()") != "dw_etl":
            raise ValueError("Expected the dw_etl source")
        if await target.fetchval("SELECT current_database()") != "www.astral.io":
            raise ValueError("Expected the www.astral.io target")
        async with source.transaction(isolation="repeatable_read", readonly=True):
            await source.execute(f"SET TRANSACTION SNAPSHOT '{spec['snapshot']}'")
            records = await source.fetch("""
                SELECT c.relname AS name, greatest(c.relpages, 1) AS pages,
                       ARRAY(SELECT a.attname::text FROM pg_attribute a
                             WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
                             AND a.attgenerated='' ORDER BY a.attnum) AS columns
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind='r' AND NOT c.relispartition
                  AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
                      AND d.objid=c.oid AND d.refclassid='pg_extension'::regclass AND d.deptype='e')
                ORDER BY pages DESC, name
            """)
            expected = set(spec["tables"])
            if len(expected) != 105 or {record["name"] for record in records} != expected:
                raise ValueError("Source inventory differs from the 105 approved application tables")
            target_tables = await target.fetch("""
                SELECT c.relname AS name FROM pg_class c
                WHERE c.relnamespace=to_regnamespace('public') AND c.relkind='r'
            """)
            if {record["name"] for record in target_tables} != expected:
                raise ValueError("Target schema tables differ from the selected source inventory")
            sequences = []
            for row in await source.fetch("""
                SELECT relname FROM pg_class WHERE relnamespace=to_regnamespace('public') AND relkind='S'
                ORDER BY relname
            """):
                name = row["relname"]
                state = await source.fetchrow(f"SELECT last_value, is_called FROM public.{_identifier(name)}")
                sequences.append((name, state["last_value"], state["is_called"]))
    finally:
        await target.close()
        await source.close()

    lanes: list[list[dict[str, Any]]] = [[] for _ in range(WORKERS)]
    pages = [0] * WORKERS
    for record in records:
        lane = min(range(WORKERS), key=pages.__getitem__)
        lanes[lane].append(dict(record))
        pages[lane] += record["pages"]
    return lanes, sequences


async def _finish_sequences(spec: dict[str, Any], sequences: list[tuple[str, int, bool]]) -> None:
    target = await _connect(spec["target_dsn"])
    try:
        async with target.transaction():
            for name, value, called in sequences:
                await target.execute(
                    "SELECT setval($1::text::regclass, $2, $3)", f"public.{_identifier(name)}", value, called,
                )
    finally:
        await target.close()


def main() -> None:
    spec = json.load(sys.stdin)
    if not re.fullmatch(r"[0-9A-Fa-f]+(?:-[0-9A-Fa-f]+){2}", spec["snapshot"]):
        raise ValueError("Invalid exported snapshot")
    lanes, sequences = asyncio.run(_plan(spec))
    counts: dict[str, int] = {}
    with ProcessPoolExecutor(max_workers=WORKERS, mp_context=multiprocessing.get_context("fork")) as pool:
        jobs = [pool.submit(_run_worker, spec, lane) for lane in lanes]
        for job in jobs:
            counts.update(job.result())
    asyncio.run(_finish_sequences(spec, sequences))
    print(json.dumps({"copied_rows": counts, "sequences": len(sequences)}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
