"""Explicitly import reviewed local demo relationships into independent link records.

Dry-run never creates database objects. Apply consumes its immutable plan, starts
six separate worker processes, and never revives existing or removed pairs.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.etl.schema.object_links import canonical_object_key, ensure_object_links_schema, object_key_hash

WORKERS = 6
BATCH_SIZE = 3000
TARGET = ("ts_ods", "local_demo_companies")
APPROVED = {
    "专家所属企业": ("ts_ods", "local_demo_experts", "LOCAL-EX-", 12),
    "申请企业": ("ts_ods", "local_demo_patents", "LOCAL-PT-", 20),
    "承担企业": ("ts_dwd", "local_demo_project_portfolio", "LOCAL-RD-", 8),
}


def get_pg_client():
    # Help/planning helpers do not initialize application configuration or connections.
    from app.storage.postgres_client import get_pg_client as client_factory
    return client_factory()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def qualified(schema, table):
    # Only the fixed approved identifiers reach generated SQL.
    if (schema, table) not in [TARGET, *[item[:2] for item in APPROVED.values()]]:
        raise ValueError("Only reviewed local demo tables may be read")
    return f'"{schema}"."{table}"'


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        output.write(encoded(value) + "\n"); output.flush(); os.fsync(output.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


async def asset(session, schema, table):
    qualified(schema, table)
    result = (await session.execute(text("""
        SELECT c.oid::bigint AS oid, a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
               a.attcollation::bigint AS collation, array_position(i.indkey::smallint[], a.attnum) AS pk
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        LEFT JOIN pg_index i ON i.indrelid=c.oid AND i.indisprimary AND i.indisvalid
        WHERE n.nspname=:schema AND c.relname=:table AND c.relkind='r' AND NOT c.relispartition
          AND NOT EXISTS (SELECT 1 FROM pg_inherits p WHERE p.inhrelid=c.oid OR p.inhparent=c.oid)
        ORDER BY a.attnum
    """), {"schema": schema, "table": table})).mappings().all()
    columns = {row["name"]: row for row in result}
    if not columns or [row["name"] for row in result if row["pk"] is not None] != ["id"]:
        raise ValueError(f"{schema}.{table} must retain its reviewed id primary key")
    for field in ["id", *([] if (schema, table) == TARGET else ["company_id"])]:
        if field not in columns or columns[field]["type"] != "text":
            raise ValueError(f"Unexpected local demo key type: {schema}.{table}.{field}")
    return {"schema": schema, "table": table, "oid": columns["id"]["oid"],
            "collation": columns["id"]["collation"],
            "matchCollation": columns["id" if (schema, table) == TARGET else "company_id"]["collation"]}


async def definition(session, name, *, lock=False):
    row = (await session.execute(text("""
        SELECT id, name, source_schema, source_table, source_fields, target_schema, target_table,
               target_fields, cardinality, enabled, updated_at::text AS revision
        FROM public.ontology_object_relations WHERE name=:name
    """ + (" FOR UPDATE" if lock else "")), {"name": name})).mappings().one_or_none()
    if row is None:
        raise ValueError(f"Reviewed logical definition is missing: {name}; physical FKs are never imported")
    spec = APPROVED[name]
    if (row["source_schema"], row["source_table"]) != spec[:2] or \
       (row["target_schema"], row["target_table"]) != TARGET or \
       list(row["source_fields"]) != ["company_id"] or list(row["target_fields"]) != ["id"] or \
       row["cardinality"] != "many_to_one" or row["enabled"] is not True:
        raise ValueError(f"Definition differs from its reviewed local demo contract: {name}")
    return dict(row)


async def preflight(names):
    pg = get_pg_client()
    await pg.connect(max_retries=1)
    try:
        async with pg.session() as session:
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout='5s'"))
            await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            target = await asset(session, *TARGET)
            companies = (await session.execute(text(f"""SELECT id, tags->>'fixture' AS fixture
                FROM {qualified(*TARGET)} ORDER BY id LIMIT 9"""))).mappings().all()
            if len(companies) > 8 or any(not re.fullmatch(r"LOCAL-CO-[0-9]{3}", row["id"]) or
                    row["fixture"] != "local-ontology-demo-v1" for row in companies):
                raise ValueError("The target is not the reviewed synthetic company fixture")
            company_ids = {row["id"] for row in companies}
            relations, pairs = [], []
            for name in sorted(set(names)):
                row = await definition(session, name)
                schema, table, prefix, maximum = APPROVED[name]
                source = await asset(session, schema, table)
                if source["matchCollation"] != target["matchCollation"]:
                    raise ValueError(f"Key collations differ for {name}")
                records = (await session.execute(text(f"SELECT id, company_id FROM {qualified(schema, table)} ORDER BY id LIMIT :limit"),
                                                 {"limit": maximum + 1})).mappings().all()
                if len(records) > maximum or any(not re.fullmatch(re.escape(prefix) + r"[0-9]{3}", item["id"]) for item in records):
                    raise ValueError(f"Source differs from its bounded synthetic fixture: {name}")
                if any(item["company_id"] not in company_ids for item in records):
                    raise ValueError(f"Unmatched company key in {name}; no missing target is invented")
                relations.append({"definition": row, "source": source, "target": target})
                pairs.extend({"relation": name, "relationId": str(row["id"]),
                              "sourceKey": {"id": item["id"]}, "targetKey": {"id": item["company_id"]}} for item in records)
            body = {"format": 1, "relations": relations, "pairs": pairs}
            return {**body, "planId": fingerprint(body)}
    finally:
        await pg.close()


def partitions(plan):
    """Assign complete source groups to six disjoint, contiguous ranges."""
    groups = {}
    for pair in plan["pairs"]:
        groups.setdefault((pair["relationId"], canonical_object_key(pair["sourceKey"])), []).append(pair)
    ordered = sorted(groups)
    return [[pair for group in ordered[len(ordered) * worker // WORKERS:len(ordered) * (worker + 1) // WORKERS]
             for pair in groups[group]] for worker in range(WORKERS)]


async def import_batch(session, expected, pairs):
    inserted = skipped = 0
    for name in sorted({pair["relation"] for pair in pairs}):
        current = await definition(session, name, lock=True)
        if current != expected[name]["definition"]:
            raise ValueError(f"Definition changed after preflight: {name}")
        source, target = expected[name]["source"], expected[name]["target"]
        await session.execute(text(f"LOCK TABLE {qualified(source['schema'], source['table'])}, {qualified(*TARGET)} IN ACCESS SHARE MODE"))
        for endpoint in (source, target):
            actual = await session.scalar(text("SELECT CAST(to_regclass(:name) AS oid)::bigint"),
                                          {"name": qualified(endpoint["schema"], endpoint["table"])})
            if actual != endpoint["oid"]:
                raise ValueError("An approved physical table was replaced after preflight")
    for pair in pairs:
        info = expected[pair["relation"]]
        source = info["source"]
        params = {"relation": int(pair["relationId"]), "source": canonical_object_key(pair["sourceKey"]),
                  "target": canonical_object_key(pair["targetKey"]), "source_hash": object_key_hash(pair["sourceKey"]),
                  "target_hash": object_key_hash(pair["targetKey"]), "id": str(uuid4()),
                  "source_id": pair["sourceKey"]["id"], "target_id": pair["targetKey"]["id"]}
        matched = (await session.execute(text(f"""SELECT s.id::text AS source_id, t.id::text AS target_id
            FROM {qualified(source['schema'], source['table'])} s JOIN {qualified(*TARGET)} t ON s.company_id=t.id
            WHERE s.id=:source_id AND t.id=:target_id FOR KEY SHARE OF s,t"""), params)).first()
        if matched is None:
            raise ValueError("An approved source/target match changed after preflight")
        existing = (await session.execute(text("""SELECT source_key, target_key, removed_at
            FROM public.ontology_object_links WHERE relation_id=:relation
              AND source_hash=:source_hash AND target_hash=:target_hash"""), params)).mappings().one_or_none()
        if existing is not None:
            if existing["source_key"] != pair["sourceKey"] or existing["target_key"] != pair["targetKey"]:
                raise ValueError("Canonical key hash collision")
            skipped += 1
            continue
        occupied = await session.scalar(text("""SELECT EXISTS (SELECT 1 FROM public.ontology_object_links
            WHERE relation_id=:relation AND source_hash=:source_hash AND removed_at IS NULL)"""), params)
        if occupied:
            raise ValueError("An existing active link conflicts with the reviewed many-to-one cardinality")
        result = await session.execute(text("""INSERT INTO public.ontology_object_links
            (id, relation_id, source_key, target_key, source_hash, target_hash)
            VALUES (CAST(:id AS uuid), :relation, CAST(:source AS jsonb), CAST(:target AS jsonb), :source_hash, :target_hash)
            ON CONFLICT (relation_id, source_hash, target_hash) DO NOTHING RETURNING id"""), params)
        if result.scalar_one_or_none() is None:
            skipped += 1
        else:
            inserted += 1
    return {"inserted": inserted, "existingOrRemoved": skipped}


async def worker_run(number, plan, pairs, directory):
    checkpoint = directory / f"worker-{number}.checkpoint.json"
    journal = directory / f"worker-{number}.journal"
    journal.mkdir(exist_ok=True)
    start = 0
    if checkpoint.exists():
        state = json.loads(checkpoint.read_text())
        if state["planId"] != plan["planId"] or state["rangeHash"] != fingerprint(pairs):
            raise ValueError("Existing checkpoint belongs to a different plan or source range")
        start = state["next"]
    if not isinstance(start, int) or not 0 <= start <= len(pairs):
        raise ValueError("Invalid durable checkpoint")
    expected = {item["definition"]["name"]: item for item in plan["relations"]}
    pg = get_pg_client()
    await pg.connect(max_retries=1)
    try:
        for offset in range(start, len(pairs), BATCH_SIZE):
            batch = pairs[offset:offset + BATCH_SIZE]
            async with pg.session() as session:
                await session.execute(text("SET LOCAL statement_timeout='10s'"))
                await session.execute(text("SET LOCAL lock_timeout='2s'"))
                await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
                report = await import_batch(session, expected, batch)
            end = offset + len(batch)
            event = {"planId": plan["planId"], "worker": number, "pid": os.getpid(), "from": offset, "next": end, **report}
            atomic_json(journal / f"{offset}-{uuid4()}.json", event)
            atomic_json(checkpoint, {"planId": plan["planId"], "rangeHash": fingerprint(pairs), "next": end})
        if not pairs:
            atomic_json(journal / f"empty-{uuid4()}.json", {"planId": plan["planId"], "worker": number, "pid": os.getpid(), "next": 0})
            atomic_json(checkpoint, {"planId": plan["planId"], "rangeHash": fingerprint(pairs), "next": 0})
        return {"worker": number, "pid": os.getpid(), "assignedPairs": len(pairs), "complete": True}
    finally:
        await pg.close()


def worker(number, plan, pairs, directory, results):
    try:
        results.put(asyncio.run(worker_run(number, plan, pairs, Path(directory))))
    except Exception as failure:
        results.put({"worker": number, "pid": os.getpid(), "error": str(failure), "complete": False})
        raise SystemExit(1)


async def initialize():
    pg = get_pg_client(); await pg.connect(max_retries=1)
    try:
        async with pg.session() as session:
            await ensure_object_links_schema(session)
    finally:
        await pg.close()


async def verify_coverage(plan):
    pg = get_pg_client(); await pg.connect(max_retries=1)
    active = removed = 0
    try:
        async with pg.session() as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            for pair in plan["pairs"]:
                row = (await session.execute(text("""SELECT source_key, target_key, removed_at FROM public.ontology_object_links
                    WHERE relation_id=:relation AND source_hash=:source_hash AND target_hash=:target_hash"""), {
                        "relation": int(pair["relationId"]), "source_hash": object_key_hash(pair["sourceKey"]),
                        "target_hash": object_key_hash(pair["targetKey"]),
                    })).mappings().one_or_none()
                if row is None or row["source_key"] != pair["sourceKey"] or row["target_key"] != pair["targetKey"]:
                    raise ValueError("Imported source ranges do not completely cover the approved plan")
                if row["removed_at"] is None: active += 1
                else: removed += 1
        return {"coveredPairs": active + removed, "activePairs": active, "removedPairs": removed}
    finally:
        await pg.close()


def apply_workers(plan, directory):
    manifest = directory / "plan.json"
    if manifest.exists() and json.loads(manifest.read_text()) != plan:
        raise ValueError("Journal directory belongs to another immutable plan")
    atomic_json(manifest, plan)
    context = multiprocessing.get_context("spawn"); results = context.Queue()
    processes = [context.Process(target=worker, args=(number, plan, part, str(directory), results))
                 for number, part in enumerate(partitions(plan))]
    for process in processes: process.start()
    for process in processes: process.join()
    reports = [results.get(timeout=2) for _ in processes]
    success = all(process.exitcode == 0 for process in processes) and all(report["complete"] for report in reports)
    coverage = asyncio.run(verify_coverage(plan)) if success else {}
    print(encoded({"planId": plan["planId"], "complete": success, "workers": sorted(reports, key=lambda item: item["worker"]),
                   "batchLimit": BATCH_SIZE, "businessRowsChanged": 0, **coverage}))
    if not success: raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--initialize", action="store_true", help="Explicitly install only the independent link metadata table")
    action.add_argument("--apply", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--journal-dir", type=Path)
    parser.add_argument("--relation", action="append", choices=sorted(APPROVED))
    args = parser.parse_args()
    if args.initialize:
        asyncio.run(initialize()); print(encoded({"initialized": True, "businessRowsChanged": 0})); return
    if args.plan is None or args.apply and args.journal_dir is None:
        parser.error("A plan is required; apply also requires a durable journal directory")
    if args.dry_run:
        plan = asyncio.run(preflight(args.relation or sorted(APPROVED)))
        if args.plan.exists() and json.loads(args.plan.read_text()) != plan:
            parser.error("Existing plan differs; choose a new plan path and preserve previous journals")
        atomic_json(args.plan, plan)
        print(encoded({"dryRun": True, "planId": plan["planId"], "relations": len(plan["relations"]), "pairs": len(plan["pairs"]),
                       "workers": WORKERS, "assignedPairs": [len(part) for part in partitions(plan)], "batchLimit": BATCH_SIZE})); return
    plan = json.loads(args.plan.read_text())
    names = [item["definition"]["name"] for item in plan["relations"]]
    if (args.relation and sorted(set(args.relation)) != names) or asyncio.run(preflight(names)) != plan:
        parser.error("The reviewed plan or source fixture changed; preserve journals and run a new dry-run")
    directory = args.journal_dir; directory.mkdir(parents=True, exist_ok=True)
    with (directory / "import.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        apply_workers(plan, directory)


if __name__ == "__main__":
    main()
