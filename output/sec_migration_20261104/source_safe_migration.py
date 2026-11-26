"""Migrate existing source identities and fill only missing Mongo identities, without CDC replay."""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import multiprocessing
from pathlib import Path
import sys
import uuid

import asyncpg
from bson import ObjectId
from pymongo.errors import PyMongoError

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(str(HERE.parent / "source_dedup_20261109"))
import facts_direct_baseline as facts
import patent_direct_baseline as patent
import pg_worker as pg

PROTOCOL = "source_safe_mongo_migration_v1"
WORKERS, PAGE_SIZE, SQL_BATCH = 6, 3000, 300
SCOPES = {
    "facts": (facts, ["sec_edgar_financial_fact", "sec_edgar_company"]),
    "facts_full": (facts, ["sec_edgar_financial_fact", "sec_edgar_company"]),
    "patent": (patent, ["google_patent"]),
}


def require(value, message):
    if not value:
        raise RuntimeError("source safe migration: " + message)


def document_ids(docs, plan):
    ids = []
    for doc in docs:
        meta = doc.get("_meta") or {}
        rid = meta.get("record_id")
        require(type(doc.get("_id")) is ObjectId and isinstance(rid, str) and rid.strip(), "invalid Mongo identity")
        require(meta.get("template") == plan["collection"] and meta.get("data_source") == plan["collection"]
                and meta.get("data_type") == plan["data_type"], "Mongo routing is not canonical")
        ids.append(rid)
    require(len(ids) == len(set(ids)), "duplicate Mongo record_id in chunk")
    return ids


def table_name(plan, layer):
    return pg.qualified("ts_" + layer, layer + "_" + plan["data_type"])


def validate_plan(plan):
    require(plan.get("kind") in SCOPES, "unknown migration kind")
    baseline, aliases = SCOPES[plan["kind"]]
    require(plan.get("protocol") == PROTOCOL and plan.get("collection") == baseline.COLLECTION
            and plan.get("data_type") == baseline.KIND and plan.get("aliases") == aliases
            and plan.get("workers") == WORKERS and plan.get("page_size") == PAGE_SIZE
            and plan.get("sql_batch_size") == SQL_BATCH, "plan scope/layout changed")
    require(isinstance(plan.get("mongo_uuid"), str) and len(plan["mongo_uuid"]) == 32
            and all(c in "0123456789abcdef" for c in plan["mongo_uuid"]), "invalid Mongo UUID")
    require(pg.sha(pg.encoded({k: v for k, v in plan.items() if k != "plan_sha256"})) == plan.get("plan_sha256"),
            "plan hash changed")
    parts = plan.get("parts") or []
    require(len(parts) == WORKERS and [p.get("worker") for p in parts] == list(range(WORKERS)), "worker coverage changed")
    previous = None
    for number, part in enumerate(parts):
        lower = ObjectId(part["lower"])
        upper = ObjectId(part["upper_inclusive"]) if part.get("upper_inclusive") else ObjectId(part["upper"]) if part.get("upper") else None
        require(upper is not None and (lower <= upper if part.get("upper_inclusive") else lower < upper), "invalid worker bounds")
        if previous is not None:
            last, inclusive = previous
            require(last < lower if inclusive else last <= lower, "Mongo worker ranges overlap")
        previous = (upper, bool(part.get("upper_inclusive")))
        if plan["kind"] == "facts":
            require(part.get("upper_inclusive") and type(part.get("actual_count")) is int
                    and part["actual_count"] == part.get("processed") and part["actual_count"] > 0,
                    "invalid bounded facts range")
        elif plan.get("continuation"):
            pass  # Frozen committed prefixes intentionally leave gaps between remaining ranges.
        elif number < WORKERS - 1:
            require(not part.get("upper_inclusive") and part["upper"] == parts[number + 1]["lower"],
                    "migration ranges have a gap")
        else:
            require(part.get("upper") is None and part.get("upper_inclusive"),
                    "final migration range must have a fixed inclusive upper bound")
    if plan["kind"] == "facts":
        require(sum(p["actual_count"] for p in parts) == 78600, "facts plan exceeds approved burst")
    elif plan["kind"] == "facts_full":
        require(plan.get("manifest_path") is None and plan.get("manifest_sha256") is None,
                "full facts migration cannot reuse the bounded cleanup manifest")
    if plan.get("continuation"):
        validate_continuation(plan)


def interval(part):
    return (int(part["lower"], 16), int(part["upper"], 16) if part.get("upper")
            else int(part["upper_inclusive"], 16) + 1)


def merged_ranges(ranges):
    result = []
    for lower, upper in sorted(ranges):
        require(lower < upper and (not result or result[-1][1] <= lower), "remaining ranges overlap")
        if result and result[-1][1] == lower:
            result[-1] = (result[-1][0], upper)
        else:
            result.append((lower, upper))
    return result


def validate_continuation(plan):
    lineage = plan["continuation"]
    parent, states = lineage["parent_plan"], lineage["checkpoints"]
    require(plan["kind"] == parent["kind"] and plan["kind"] in ("facts_full", "patent"),
            "continuation requires a full migration plan")
    validate_plan(parent)
    require(all(plan[k] == parent[k] for k in ("collection", "data_type", "aliases", "mongo_uuid"))
            and len(states) == WORKERS, "continuation identity changed")
    remaining = []
    for n, (state, part) in enumerate(zip(states, parent["parts"])):
        validate_checkpoint(state, parent, n, None)
        require(not state.get("pending") and not state.get("last_error"), "parent checkpoint is unresolved")
        if not state["done"]:
            lower, upper = interval(part)
            lower = int(state["cursor"], 16) + 1 if state["cursor"] else lower
            if lower < upper:
                remaining.append((lower, upper))
    inherited = parent.get("continuation", {}).get("processed_offset", 0)
    require(lineage["processed_offset"] == inherited + sum(s["processed"] for s in states)
            and merged_ranges(interval(p) for p in plan["parts"]) == merged_ranges(remaining),
            "continuation must cover exactly the uncommitted parent ranges")


def validate_parent_files(plan):
    if not plan.get("continuation"):
        return
    lineage = plan["continuation"]
    parent = Path(lineage["parent_root"])
    require((parent / "STOP").is_file() and pg.read(parent / "plan.json") == lineage["parent_plan"],
            "parent must remain frozen")
    require(all(pg.read(parent / f"worker-{i}.json") == state
                for i, state in enumerate(lineage["checkpoints"])), "parent checkpoints changed")
    validate_parent_files(lineage["parent_plan"])


def validate_checkpoint(state, plan, worker, expected):
    require(state.get("plan_sha256") == plan["plan_sha256"] and state.get("worker") == worker
            and type(state.get("processed")) is int and state["processed"] >= 0, "checkpoint binding changed")
    part = plan["parts"][worker]
    cursor = state.get("cursor")
    require((state["processed"] == 0) == (cursor is None), "checkpoint cursor/count differs")
    if cursor is not None:
        value = ObjectId(cursor)
        require(value >= ObjectId(part["lower"]) and (not part.get("upper") or value < ObjectId(part["upper"]))
                and (not part.get("upper_inclusive") or value <= ObjectId(part["upper_inclusive"])), "checkpoint cursor outside range")
    if expected is not None:
        require(state["processed"] <= len(expected) and (cursor is None or expected[state["processed"] - 1]["mongo_id"] == cursor),
                "checkpoint differs from approved facts records")


def runtime_layout(root, plan):
    layout = pg.read(root / "layout.json", {"workers": WORKERS, "page_size": PAGE_SIZE,
                                          "sql_batch_size": SQL_BATCH, "plan_sha256": plan["plan_sha256"]})
    value = {key: layout.get(key) for key in ("workers", "page_size", "sql_batch_size", "plan_sha256")}
    require(all(type(value[key]) is int for key in ("workers", "page_size", "sql_batch_size"))
            and (value["workers"], value["page_size"]) in ((WORKERS, PAGE_SIZE), (5, 8000), (6, 8000))
            and 1 <= value["sql_batch_size"] <= PAGE_SIZE and value["plan_sha256"] == plan["plan_sha256"],
            "runtime layout requires 6x3000, 5x8000 or 6x8000, with SQL batches at most 3000")
    require(plan["kind"] != "facts" or (value["sql_batch_size"] == SQL_BATCH
            and value["workers"] == WORKERS and value["page_size"] == PAGE_SIZE),
            "bounded facts cleanup requires 300-row SQL batches")
    return value


async def locked_rows(connection, table, ids, sources, kind):
    keys = [{"record_id": rid, "data_source": source, "data_type": kind}
            for rid in sorted(ids) for source in sorted(sources)]
    rows = await connection.fetch(f"""
        WITH k AS MATERIALIZED(SELECT * FROM jsonb_to_recordset($1::jsonb)
          AS k(record_id text,data_source text,data_type text) ORDER BY record_id,data_source,data_type)
        SELECT k.record_id,k.data_source,k.data_type,t.raw
        FROM k CROSS JOIN LATERAL(SELECT to_jsonb(t)::text AS raw FROM {table} t
          WHERE t.record_id=k.record_id AND t.data_source=k.data_source AND t.data_type=k.data_type
          FOR UPDATE OF t OFFSET 0) t
    """, json.dumps(keys))
    result = {(r["record_id"], r["data_source"]): r["raw"] for r in rows}
    require(len(result) == len(rows), "duplicate PostgreSQL identity")
    return result


async def routing_rows(connection, layer, rows, old, new):
    unit = {"layer": layer, "old": old, "new": new}
    values = [{"record_id": rid, "old_raw": raw, "action": "rename"} for rid, raw in rows.items()]
    return await pg.expected_rows(connection, unit, values)


async def migrate_layer(connection, plan, layer, ids, locked, archives):
    table, new, kind = table_name(plan, layer), plan["collection"], plan["data_type"]
    canonical = {rid: locked[(rid, new)] for rid in ids if (rid, new) in locked}
    for old in plan["aliases"]:
        old_rows = {rid: locked[(rid, old)] for rid in ids if (rid, old) in locked}
        rename = {rid: raw for rid, raw in old_rows.items() if rid not in canonical}
        duplicates = {rid: raw for rid, raw in old_rows.items() if rid in canonical}
        if rename:
            expected = await routing_rows(connection, layer, rename, old, new)
            update_raw = (",raw_data=(" + pg.routing_sql("to_jsonb(t)", "rds") + ")->'raw_data'" if layer == "rds" else "")
            changed = await connection.fetch(f"""
                WITH b AS MATERIALIZED(SELECT value FROM jsonb_array_elements($1::jsonb))
                UPDATE {table} t SET data_source=$3::text {update_raw} FROM b
                WHERE t.record_id=b.value->>'record_id' AND t.data_source=$2::text
                  AND t.data_type=$4::text AND to_jsonb(t)=b.value
                RETURNING t.record_id,t.data_source,to_jsonb(t)::text AS raw
            """, "[" + ",".join(rename.values()) + "]", old, new, kind)
            actual = {r["record_id"]: r["raw"] for r in changed if r["data_source"] == new}
            require(len(changed) == len(rename) and actual == expected, "source UPDATE afterimages differ")
            canonical.update(actual)
        if duplicates:
            archives.extend({"table": table, "old_source": old, "raw": raw} for raw in duplicates.values())
            deleted = await connection.fetch(f"""
                WITH b AS MATERIALIZED(SELECT value FROM jsonb_array_elements($1::jsonb))
                DELETE FROM {table} t USING b WHERE t.record_id=b.value->>'record_id'
                  AND t.data_source=$2::text AND t.data_type=$3::text AND to_jsonb(t)=b.value
                RETURNING t.record_id
            """, "[" + ",".join(duplicates.values()) + "]", old, kind)
            require(len(deleted) == len(duplicates) and {r["record_id"] for r in deleted} == set(duplicates),
                    "conflicting source DELETE differs")
        if layer == "rds" and canonical:
            expected = await routing_rows(connection, layer, canonical, old, new)
            # Both snapshots use PostgreSQL jsonb::text, preserving numeric values and serialization.
            candidates = {rid: raw for rid, raw in canonical.items() if expected[rid] != raw}
            changed = []
            if candidates:
                changed = await connection.fetch(f"""
                    WITH b AS MATERIALIZED(SELECT value FROM jsonb_array_elements($1::jsonb))
                    UPDATE {table} t SET raw_data=({pg.routing_sql('to_jsonb(t)', 'rds')})->'raw_data'
                    FROM b WHERE t.record_id=b.value->>'record_id' AND t.data_source=$3::text
                      AND t.data_type=$4::text AND to_jsonb(t)=b.value
                      AND t.raw_data IS DISTINCT FROM ({pg.routing_sql('to_jsonb(t)', 'rds')})->'raw_data'
                    RETURNING t.record_id,to_jsonb(t)::text AS raw
                """, "[" + ",".join(candidates.values()) + "]", old, new, kind)
            actual = dict(canonical)
            actual.update((r["record_id"], r["raw"]) for r in changed)
            require(len(changed) == len(candidates)
                    and {r["record_id"] for r in changed} == set(candidates) and actual == expected,
                    "canonical routing UPDATE afterimages differ")
            canonical = actual
    return canonical


async def verify_chunk(connection, plan, ids, expected=None):
    result = {}
    for layer in ("rds", "ods"):
        table = table_name(plan, layer)
        rows = await locked_rows(connection, table, ids, [*plan["aliases"], plan["collection"]], plan["data_type"])
        require(set(rows) == {(rid, plan["collection"]) for rid in ids}, "canonical or old-alias coverage differs: " + table)
        result[layer] = {rid: pg.sha(raw) for (rid, _), raw in rows.items()}
        if expected is not None:
            require(result[layer] == expected[layer], "committed afterimages changed: " + table)
    return result


def advance(root, worker, state, receipt):
    value = {**state, "cursor": receipt["last_id"], "processed": state["processed"] + receipt["rows"],
             "pending": None, "last_error": None, "last_receipt": receipt["receipt"]}
    pg.atomic(root / f"worker-{worker}.json", value)
    return value


async def recover_pending(connection, root, plan, worker, state):
    if not state.get("pending"):
        return state
    receipt = pg.read(root / state["pending"])
    require(receipt["protocol"] == PROTOCOL and receipt["plan_sha256"] == plan["plan_sha256"]
            and receipt["worker"] == worker and receipt["mongo_uuid"] == plan["mongo_uuid"]
            and receipt["part"] == plan["parts"][worker]
            and receipt["previous_cursor"] == state["cursor"] and receipt["previous_processed"] == state["processed"]
            and type(receipt["rows"]) is int
            and 0 < receipt["rows"] <= (SQL_BATCH if plan["kind"] == "facts" else PAGE_SIZE)
            and len(receipt["record_ids"]) == len(set(receipt["record_ids"])) == receipt["rows"],
            "pending receipt binding differs")
    if receipt.get("archive"):
        require(pg.sha((root / receipt["archive"]["path"]).read_bytes()) == receipt["archive"]["sha256"],
                "pending conflict archive hash differs")
    status = await connection.fetchval("SELECT pg_xact_status($1::text::xid8)", str(receipt["txid_full"]))
    if status == "aborted":
        value = {**state, "pending": None}
        pg.atomic(root / f"worker-{worker}.json", value)
        return value
    require(status == "committed", "pending transaction outcome is unknown: " + str(status))
    async with connection.transaction():
        await verify_chunk(connection, plan, receipt["record_ids"], receipt["after_sha256"])
    pg.immutable(root / receipt["receipt"], pg.encoded({**receipt, "committed": True}))
    return advance(root, worker, state, receipt)


async def apply_chunk(connection, root, plan, worker, state, docs):
    require(0 < len(docs) <= PAGE_SIZE, "SQL chunk exceeds 3000")
    ids = document_ids(docs, plan)
    baseline = SCOPES[plan["kind"]][0]
    token = uuid.uuid4().hex
    prepared_path = "transactions/" + token + ".prepared.json"
    receipt_path = "transactions/" + token + ".committed.json"
    archive_path = "transactions/" + token + ".conflicts.jsonl.gz"
    archives = []
    async with connection.transaction():
        await connection.execute("SET LOCAL TIME ZONE 'UTC'")
        await connection.execute("SET LOCAL lock_timeout='15s'")
        await connection.execute("SET LOCAL statement_timeout='120s'")
        # Owned, disjoint Mongo ranges and unique record_id already isolate these workers.
        # Existing rows remain protected by FOR UPDATE, CAS and unique constraints below.
        retained = {}
        inserted = {}
        for layer in ("rds", "ods"):
            table = table_name(plan, layer)
            locked = await locked_rows(connection, table, ids, [*plan["aliases"], plan["collection"]], plan["data_type"])
            retained[layer] = await migrate_layer(connection, plan, layer, ids, locked, archives)
            missing = [doc for doc, rid in zip(docs, ids) if rid not in retained[layer]]
            inserted[layer] = len(missing)
            if missing:
                rds, ods, _ = baseline.prepare_rows(missing)
                await connection.execute(baseline.RDS_SQL if layer == "rds" else baseline.ODS_SQL,
                                         rds if layer == "rds" else ods)
        after = await verify_chunk(connection, plan, ids)
        for layer, rows in retained.items():
            require(all(after[layer][rid] == pg.sha(raw) for rid, raw in rows.items()), "retained business fields changed")
        archive = None
        if archives:
            raw = gzip.compress(("\n".join(json.dumps({"table": r["table"], "old_source": r["old_source"]})[:-1]
                                + ',"row":' + r["raw"] + "}" for r in archives) + "\n").encode(), mtime=0)
            pg.immutable(root / archive_path, raw)
            archive = {"path": archive_path, "sha256": pg.sha(raw), "rows": len(archives)}
        receipt = {"protocol": PROTOCOL, "plan_sha256": plan["plan_sha256"], "mongo_uuid": plan["mongo_uuid"],
                   "worker": worker, "part": plan["parts"][worker], "txid_full": int(await connection.fetchval("SELECT txid_current()")),
                   "previous_cursor": state["cursor"], "previous_processed": state["processed"],
                   "first_id": str(docs[0]["_id"]), "last_id": str(docs[-1]["_id"]), "rows": len(docs),
                   "record_ids": ids, "after_sha256": after, "missing_insert_attempts": inserted,
                   "archive": archive, "receipt": receipt_path}
        pg.immutable(root / prepared_path, pg.encoded(receipt))
        pg.atomic(root / f"worker-{worker}.json", {**state, "pending": prepared_path})
    pg.immutable(root / receipt_path, pg.encoded({**receipt, "committed": True}))
    return advance(root, worker, state, receipt)


async def apply_facts_chunk(connection, root, plan, worker, state, docs):
    require(plan["kind"] == "facts" and 0 < len(docs) <= SQL_BATCH, "invalid bounded facts cleanup")
    ids = document_ids(docs, plan)
    token = uuid.uuid4().hex
    prepared_path, receipt_path = f"transactions/{token}.prepared.json", f"transactions/{token}.committed.json"
    archive_path = f"transactions/{token}.conflicts.jsonl.gz"
    after, archives = {}, []
    async with connection.transaction():
        await connection.execute("SET LOCAL TIME ZONE 'UTC'")
        await connection.execute("SET LOCAL lock_timeout='15s'")
        await connection.execute("SET LOCAL statement_timeout='120s'")
        locks = sorted("|".join(("ts_" + layer, layer + "_financial_fact", plan["collection"], plan["data_type"], rid))
                       for layer in ("rds", "ods") for rid in ids)
        await connection.execute("""WITH k AS MATERIALIZED(SELECT value FROM unnest($1::text[]) x(value) ORDER BY value)
            SELECT pg_advisory_xact_lock(hashtextextended(value,0)) FROM k""", locks)
        for layer in ("rds", "ods"):
            table = table_name(plan, layer)
            routing = ("t.raw_data->>'data_source'=$2::text AND t.raw_data#>>'{_meta,template}'=$2::text "
                       "AND t.raw_data#>>'{_meta,data_source}'=$2::text" if layer == "rds" else "true")
            rows = await connection.fetch(f"""
                WITH k AS MATERIALIZED(SELECT unnest($1::text[]) AS record_id ORDER BY record_id)
                SELECT k.record_id,t.after_sha256,t.routing_valid FROM k CROSS JOIN LATERAL(
                  SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS after_sha256,
                    ({routing}) AS routing_valid FROM {table} t
                  WHERE t.record_id=k.record_id AND t.data_source=$2::text AND t.data_type=$3::text
                  FOR UPDATE OF t OFFSET 0) t
            """, sorted(ids), plan["collection"], plan["data_type"])
            require(len(rows) == len(ids) and {row["record_id"] for row in rows} == set(ids),
                    "facts canonical coverage is missing: " + table)
            require(all(row["routing_valid"] is True for row in rows), "facts canonical routing differs: " + table)
            after[layer] = {row["record_id"]: row["after_sha256"] for row in rows}
        for layer in ("rds", "ods"):
            table = table_name(plan, layer)
            for old in plan["aliases"]:
                removed = await connection.fetch(f"""
                    WITH k AS MATERIALIZED(SELECT unnest($1::text[]) AS record_id)
                    DELETE FROM {table} t USING k WHERE t.record_id=k.record_id
                      AND t.data_source=$2::text AND t.data_type=$3::text
                    RETURNING t.record_id,to_jsonb(t)::text AS raw
                """, ids, old, plan["data_type"])
                require(len({row["record_id"] for row in removed}) == len(removed)
                        and {row["record_id"] for row in removed} <= set(ids), "facts DELETE returned unexpected IDs")
                archives.extend({"table": table, "old_source": old, "raw": row["raw"]} for row in removed)
            keys = [{"record_id": rid, "data_source": source, "data_type": plan["data_type"]}
                    for rid in ids for source in [*plan["aliases"], plan["collection"]]]
            rows = await connection.fetch(f"""
                WITH k AS MATERIALIZED(SELECT * FROM jsonb_to_recordset($1::jsonb)
                  AS k(record_id text,data_source text,data_type text))
                SELECT k.record_id,k.data_source FROM k CROSS JOIN LATERAL(SELECT 1 FROM {table} t
                  WHERE t.record_id=k.record_id AND t.data_source=k.data_source AND t.data_type=k.data_type OFFSET 0) t
            """, json.dumps(keys))
            require(len(rows) == len(ids) and {(row["record_id"], row["data_source"]) for row in rows}
                    == {(rid, plan["collection"]) for rid in ids}, "facts old-alias deletion verification differs: " + table)
        archive = None
        if archives:
            raw = gzip.compress(("\n".join(json.dumps({"table": r["table"], "old_source": r["old_source"]})[:-1]
                                + ',"row":' + r["raw"] + "}" for r in archives) + "\n").encode(), mtime=0)
            pg.immutable(root / archive_path, raw)
            archive = {"path": archive_path, "sha256": pg.sha(raw), "rows": len(archives)}
        receipt = {"protocol": PROTOCOL, "plan_sha256": plan["plan_sha256"], "mongo_uuid": plan["mongo_uuid"],
                   "worker": worker, "part": plan["parts"][worker], "txid_full": int(await connection.fetchval("SELECT txid_current()")),
                   "previous_cursor": state["cursor"], "previous_processed": state["processed"],
                   "first_id": str(docs[0]["_id"]), "last_id": str(docs[-1]["_id"]), "rows": len(docs),
                   "record_ids": ids, "after_sha256": after, "missing_insert_attempts": {"rds": 0, "ods": 0},
                   "archive": archive, "receipt": receipt_path, "operation": "facts_manifest_dedup"}
        pg.immutable(root / prepared_path, pg.encoded(receipt))
        pg.atomic(root / f"worker-{worker}.json", {**state, "pending": prepared_path})
    pg.immutable(root / receipt_path, pg.encoded({**receipt, "committed": True}))
    return advance(root, worker, state, receipt)


def manifest_records(plan, worker):
    if plan["kind"] != "facts":
        return None
    part = plan["parts"][worker]
    manifest_path = Path(plan["manifest_path"])
    require(pg.sha(manifest_path.read_bytes()) == plan["manifest_sha256"], "facts manifest hash differs")
    path = manifest_path.parent / part["records_path"]
    require(path.resolve().parent == Path(plan["manifest_path"]).parent.resolve(), "facts manifest path escapes its directory")
    raw = path.read_bytes()
    require(pg.sha(raw) == part["records_sha256"], "facts record manifest hash differs")
    rows = [json.loads(line) for line in raw.splitlines()]
    require(len(rows) == part["actual_count"] == part["processed"], "facts record manifest count differs")
    require(pg.sha("".join(row["record_id"] + "\n" for row in rows)) == part["record_ids_sha256"], "facts record IDs differ")
    require(pg.sha(raw) == part["rows_sha256"], "facts record rows differ")
    require(len({row["record_id"] for row in rows}) == len(rows)
            and all(isinstance(row["record_id"], str) and row["record_id"] for row in rows)
            and [row["mongo_id"] for row in rows] == sorted(set(row["mongo_id"] for row in rows))
            and all(ObjectId(part["lower"]) <= ObjectId(row["mongo_id"]) <= ObjectId(part["upper_inclusive"]) for row in rows)
            and rows[-1]["mongo_id"] == part["upper_inclusive"], "facts manifest identities/range differ")
    return rows


def validate_namespace(client, plan):
    database = client[facts.settings.db_name]
    rows = list(database.list_collections(filter={"name": plan["collection"]}, maxTimeMS=15000))
    require(len(rows) == 1 and bytes(rows[0]["info"]["uuid"]).hex() == plan["mongo_uuid"], "Mongo UUID changed")
    collection = database[plan["collection"]]
    index = collection.index_information().get("_meta.record_id_1", {})
    require(index.get("unique") is True and index.get("key") == [("_meta.record_id", 1)], "Mongo record_id index changed")
    return collection


async def worker_run(root, worker, canary=False):
    plan = pg.read(root / "plan.json")
    validate_plan(plan)
    validate_parent_files(plan)
    layout = runtime_layout(root, plan)
    part = plan["parts"][worker]
    expected = manifest_records(plan, worker)
    state_path = root / f"worker-{worker}.json"
    state = pg.read(state_path, {"plan_sha256": plan["plan_sha256"], "worker": worker,
                    "cursor": None, "processed": 0, "pending": None, "done": False, "last_error": None})
    validate_checkpoint(state, plan, worker, expected)
    if state.get("done"):
        return
    state = {**state, "canary_complete": False}
    pg.atomic(state_path, state)
    client = facts.mongo_client()
    connection = None
    try:
        collection = None
        failures = 0
        limit = state["processed"] + 500 if canary else None
        while not state.get("done"):
            if (root / "STOP").exists():
                return
            try:
                if collection is None:
                    collection = validate_namespace(client, plan)
                if connection is None or connection.is_closed():
                    connection = await pg.owned_connection(root, worker)
                state = await recover_pending(connection, root, plan, worker, state)
                validate_checkpoint(state, plan, worker, expected)
                if limit is not None and state["processed"] >= limit:
                    pg.atomic(state_path, {**state, "canary_complete": True, "last_error": None})
                    return
                bounds = {"$gt": ObjectId(state["cursor"])} if state["cursor"] else {"$gte": ObjectId(part["lower"])}
                if part.get("upper_inclusive"):
                    bounds["$lte"] = ObjectId(part["upper_inclusive"])
                elif part.get("upper"):
                    bounds["$lt"] = ObjectId(part["upper"])
                if expected is not None:
                    docs = [{"_id": ObjectId(row["mongo_id"]), "_meta": {
                        "record_id": row["record_id"], "template": plan["collection"],
                        "data_source": plan["collection"], "data_type": plan["data_type"]}}
                        for row in expected[state["processed"]:state["processed"] + PAGE_SIZE]]
                else:
                    reader = SCOPES[plan["kind"]][0].read_batch
                    docs = (reader(collection, {"_id": bounds}, limit=layout["page_size"])
                            if layout["page_size"] != PAGE_SIZE else reader(collection, {"_id": bounds}))
                require(len(docs) <= layout["page_size"], "Mongo page exceeds runtime limit")
                if not docs:
                    require(expected is None or state["processed"] == len(expected), "facts source ended before manifest")
                    pg.atomic(state_path, {**state, "done": True, "last_error": None})
                    return
                if limit is not None:
                    docs = docs[:limit - state["processed"]]
                for start in range(0, len(docs), layout["sql_batch_size"]):
                    if (root / "STOP").exists():
                        return
                    chunk = docs[start:start + layout["sql_batch_size"]]
                    ids = document_ids(chunk, plan)
                    require(all((state["cursor"] is None or doc["_id"] > ObjectId(state["cursor"]))
                                and doc["_id"] >= ObjectId(part["lower"])
                                and (not part.get("upper") or doc["_id"] < ObjectId(part["upper"]))
                                and (not part.get("upper_inclusive") or doc["_id"] <= ObjectId(part["upper_inclusive"])) for doc in chunk)
                            and [doc["_id"] for doc in chunk] == sorted(set(doc["_id"] for doc in chunk)), "Mongo chunk range/order differs")
                    if expected is not None:
                        actual = [{"mongo_id": str(doc["_id"]), "record_id": rid} for doc, rid in zip(chunk, ids)]
                        require(actual == expected[state["processed"]:state["processed"] + len(chunk)], "Mongo facts differ from approved manifest")
                    apply = apply_facts_chunk if expected is not None else apply_chunk
                    state = await apply(connection, root, plan, worker, state, chunk)
                    failures = 0
            except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, asyncio.TimeoutError, PyMongoError) as error:
                if isinstance(error, asyncpg.InterfaceError) and (connection is None or not connection.is_closed()):
                    raise
                failures += 1
                print(json.dumps({"event": "retryable_worker_error", "worker": worker,
                                  "error": type(error).__name__, "attempt": failures, "max_attempts": 5}),
                      file=sys.stderr, flush=True)
                if connection is not None and not connection.is_closed():
                    connection.terminate()
                connection = None
                state = pg.read(state_path, state)
                if failures >= 5:
                    raise
                await asyncio.sleep(min(2 ** failures, 15))
    except BaseException as error:
        state = pg.read(state_path, state)
        pg.atomic(state_path, {**state, "last_error": type(error).__name__})
        raise
    finally:
        client.close()
        if connection is not None and not connection.is_closed():
            await connection.close()


def worker_entry(root, worker, canary=False):
    asyncio.run(worker_run(Path(root), worker, canary))


def write_state(root, plan, canary, *, failed=False, last_error=None):
    layout = runtime_layout(root, plan)
    states = [pg.read(root / f"worker-{worker}.json", {}) for worker in range(WORKERS)]
    processed = sum(s.get("processed", 0) for s in states)
    all_done = all(s.get("done") is True for s in states)
    if all_done and plan["kind"] == "facts":
        require(processed == 78600, "facts completed coverage differs from approved burst")
    stage = ("failed" if failed else "complete" if all_done and plan["kind"] == "facts"
             else "mongo_coverage_complete" if all_done else "canary_complete" if canary
             and all(s.get("done") is True or s.get("canary_complete") is True for s in states)
             else "paused" if (root / "STOP").exists() else "running")
    value = {"protocol": PROTOCOL, "kind": plan["kind"], "stage": stage, "plan_sha256": plan["plan_sha256"],
             "mongo_uuid": plan["mongo_uuid"], "processed": processed, "workers_done": sum(s.get("done") is True for s in states),
             "workers": layout["workers"], "page_size": layout["page_size"], "sql_batch_size": layout["sql_batch_size"],
             "pg_only_alias_sweep_complete": False, "cdc_replay": False,
             "last_error": last_error or next((s.get("last_error") for s in states if s.get("last_error")), None)}
    pg.atomic(root / "state.json", value)
    return value


def source_alias_tail(root, plan):
    tail = root / "pg-alias-tail"
    require(plan["kind"] in ("patent", "facts_full"), "source tail requires a full migration")
    baseline, aliases = SCOPES[plan["kind"]]
    scope = {"protocol": ("source_safe_facts_full_alias_tail_v1" if plan["kind"] == "facts_full"
                          else "source_safe_patent_alias_tail_v1"), "mongo_plan_sha256": plan["plan_sha256"],
             "workers": WORKERS, "batch_size": SQL_BATCH, "guard_table": None,
             "scopes": [{"old": old, "new": baseline.COLLECTION, "data_type": baseline.KIND,
                         "relations": [{"schema": "ts_" + layer, "name": layer + "_" + baseline.KIND, "layer": layer}
                                       for layer in ("rds", "ods")]} for old in aliases]}
    state = write_state(root, plan, False)
    require(state["workers_done"] == WORKERS, "source tail requires complete Mongo ranges")
    if (root / "STOP").exists():
        pg.atomic(root / "state.json", {**state, "stage": "paused"})
        return
    pg.atomic(root / "state.json", {**state, "stage": "pg_alias_tail"})
    pg.immutable(tail / "scope.json", pg.encoded(scope))
    tail_plan = asyncio.run(pg.prepare(tail))
    units = tail_plan["units"]
    expected = {("ts_" + layer, layer + "_" + baseline.KIND, ordinal, old)
                for old in aliases for layer in ("rds", "ods") for ordinal in range(32)}
    unit_count = len(expected)
    require(tail_plan["plan_sha256"] == pg.sha(pg.encoded(scope)) and tail_plan["workers"] == WORKERS
            and tail_plan["batch_size"] == SQL_BATCH and tail_plan.get("guard_table") is None
            and len(units) == unit_count and {u["unit"] for u in units} == set(range(unit_count))
            and {(u["schema"], u["table"], u["ordinal"], u["old"]) for u in units} == expected
            and all(u["new"] == baseline.COLLECTION and u["data_type"] == baseline.KIND for u in units),
            "source tail plan coverage differs")
    pg.main(argparse.Namespace(root=str(tail), prepare=False, worker=None))
    if (tail / "STOP").exists():
        pg.atomic(root / "state.json", {**state, "stage": "paused"})
        return
    checkpoints = [pg.read(tail / "checkpoints" / f"unit-{u['unit']}.json", {}) for u in units]
    require(all(cp.get("plan_sha256") == tail_plan["plan_sha256"] and cp.get("unit") == unit["unit"]
                and cp.get("done") is True and not cp.get("pending") and not cp.get("last_error")
                for unit, cp in zip(units, checkpoints)), "source tail checkpoints are incomplete or differ")
    pg.atomic(root / "state.json", {**state, "stage": "complete", "pg_only_alias_sweep_complete": True,
              "tail_units_done": unit_count, "tail_processed": sum(cp.get("processed", 0) for cp in checkpoints)})


def prepare_plan(root, kind, manifest_path=None):
    baseline, aliases = SCOPES[kind]
    require(kind != "facts" or manifest_path is not None, "facts require the approved bounded checkpoint manifest")
    require(kind == "facts" or manifest_path is None, "full migrations do not accept bounded facts manifests")
    client = facts.mongo_client()
    try:
        database = client[facts.settings.db_name]
        rows = list(database.list_collections(filter={"name": baseline.COLLECTION}, maxTimeMS=15000))
        require(len(rows) == 1, "Mongo collection is missing")
        uid = bytes(rows[0]["info"]["uuid"]).hex()
        if kind == "facts":
            raw = Path(manifest_path).read_bytes()
            manifest = json.loads(raw)
            require(manifest["protocol"] == "facts_burst_checkpoint_manifest_v1"
                    and manifest["mongo_uuid_hex"] == uid and manifest["total"] == 78600
                    and manifest["workers"] == WORKERS and manifest["collection"] == baseline.COLLECTION
                    and manifest["data_source"] == baseline.COLLECTION and manifest["data_type"] == baseline.KIND,
                    "facts manifest identity differs")
            parts = sorted(manifest["parts"], key=lambda value: value["worker"])
            require([part["worker"] for part in parts] == list(range(WORKERS)), "facts manifest worker coverage differs")
        else:
            collection = database[baseline.COLLECTION]
            first = collection.find_one({}, {"_id": 1}, sort=[("_id", 1)])
            last = collection.find_one({}, {"_id": 1}, sort=[("_id", -1)])
            require(first and last, "Mongo collection is empty")
            parts = [{**part, "worker": worker} for worker, part in
                     enumerate(facts.split_ranges(str(first["_id"]), str(last["_id"])))]
            parts[-1]["upper_inclusive"] = str(last["_id"])
        ordered = sorted(parts, key=lambda value: value["lower"])
        for left, right in zip(ordered, ordered[1:]):
            upper = left.get("upper_inclusive") or left.get("upper")
            require(upper is not None and (ObjectId(upper) < ObjectId(right["lower"]) if left.get("upper_inclusive")
                    else ObjectId(upper) <= ObjectId(right["lower"])), "Mongo worker ranges overlap")
        plan = {"protocol": PROTOCOL, "kind": kind, "collection": baseline.COLLECTION, "data_type": baseline.KIND,
                "aliases": aliases, "mongo_uuid": uid, "workers": WORKERS, "page_size": PAGE_SIZE,
                "sql_batch_size": SQL_BATCH, "parts": parts,
                "manifest_path": str(Path(manifest_path).resolve()) if manifest_path else None,
                "manifest_sha256": pg.sha(raw) if manifest_path else None}
        validate_namespace(client, plan)
        plan["plan_sha256"] = pg.sha(pg.encoded(plan))
        validate_plan(plan)
        for worker in range(WORKERS):
            manifest_records(plan, worker)
        pg.immutable(root / "plan.json", pg.encoded(plan))
        return plan
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--kind", choices=SCOPES)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--canary", action="store_true", help="Process at most 500 additional records per worker, then stop without changing the plan")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--worker", type=int)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.prepare:
        require(args.kind is not None, "--prepare requires --kind")
        plan = prepare_plan(root, args.kind, args.manifest)
        print(json.dumps({"root": str(root), "plan_sha256": plan["plan_sha256"], "workers": WORKERS}))
        return
    require((root / "plan.json").is_file(), "prepare an immutable plan before running")
    plan = pg.read(root / "plan.json")
    validate_plan(plan)
    validate_parent_files(plan)
    layout = runtime_layout(root, plan)
    if args.worker is not None:
        require(0 <= args.worker < WORKERS, "worker must be 0 through 5")
        worker_entry(str(root), args.worker, args.canary)
        return
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=worker_entry, args=(str(root), worker, args.canary), name=f"source-safe-{worker}")
                 for worker in range(WORKERS)]
    try:
        pending = list(processes)
        while pending or any(process.is_alive() for process in processes):
            require(all(process.exitcode in (None, 0) for process in processes), "worker failed; preserve checkpoints")
            if (root / "STOP").exists():
                pending.clear()
            while pending and sum(process.is_alive() for process in processes) < layout["workers"]:
                pending.pop(0).start()
            write_state(root, plan, args.canary)
            for process in processes:
                if process.pid is not None:
                    process.join(timeout=0.2)
        require(all(process.exitcode == 0 or (process.pid is None and (root / "STOP").exists())
                    for process in processes), "worker failed; preserve checkpoints")
        state = write_state(root, plan, args.canary)
        if plan["kind"] in ("patent", "facts_full") and not args.canary and state["workers_done"] == WORKERS:
            source_alias_tail(root, plan)
    except BaseException as error:
        write_state(root, plan, args.canary, failed=True, last_error=type(error).__name__)
        raise
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            if process.pid is not None:
                process.join()


if __name__ == "__main__":
    main()
