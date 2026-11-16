"""Backfill the canonical Google Patent snapshot directly into RDS and ODS."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import multiprocessing
from pathlib import Path
import sys
import time
from typing import Any

import asyncpg
from bson import ObjectId
from pymongo.errors import AutoReconnect, CursorNotFound, ExecutionTimeout

from app.config.settings import settings
from app.etl.normalizers.patent import normalize_google_patent

sys.path.insert(0, str(Path(__file__).resolve().parent))
import facts_direct_baseline as base


PROTOCOL = base.PROTOCOL
COLLECTION = "patent_google"
KIND = "patent"
WORKERS = 6
BATCH = 3000
SQL_BATCH = 300
LOCK = Path("/data/clawer/output/sec_migration_20261104/.patent-direct-baseline.lock")

RDS_SQL = base.RDS_SQL.replace("rds_financial_fact", "rds_patent")
ODS_SQL = """
INSERT INTO ts_ods.ods_patent
 (record_id,data_source,data_type,title,publication_number,application_number,assignee,inventor,
  publication_date,filing_date,priority_date,grant_date,abstract,claims,legal_status,
  ipc_classification,cpc_classification,patent_type,url,thumbnail,figures,created_at,updated_at)
SELECT x.record_id,x.data_source,x.data_type,x.title,x.publication_number,x.application_number,x.assignee,x.inventor,
 x.publication_date,x.filing_date,x.priority_date,x.grant_date,x.abstract,x.claims,x.legal_status,
 x.ipc_classification,x.cpc_classification,x.patent_type,x.url,x.thumbnail,x.figures,x.created_at,x.updated_at
FROM jsonb_to_recordset($1::jsonb) AS x(
 record_id text,data_source text,data_type text,title text,publication_number text,application_number text,
 assignee text,inventor text,publication_date date,filing_date date,priority_date date,grant_date date,
 abstract text,claims jsonb,legal_status text,ipc_classification text,cpc_classification text,
 patent_type text,url text,thumbnail text,figures jsonb,created_at timestamptz,updated_at timestamptz)
ON CONFLICT (record_id,data_source,data_type) DO NOTHING
"""


def prepare_rows(docs: list[dict[str, Any]]) -> tuple[str, str, str]:
    rds: list[dict[str, Any]] = []
    ods: list[dict[str, Any]] = []
    keys: list[dict[str, str]] = []
    now = datetime.now(timezone.utc)
    seen: set[str] = set()
    for original in docs:
        document = {key: value for key, value in original.items() if key != "_id"}
        meta = dict(document.get("_meta") or {})
        record_id = meta.get("record_id")
        if not isinstance(record_id, str) or not record_id or record_id in seen:
            raise RuntimeError("patent baseline invalid or duplicate record_id in batch")
        if meta.get("template") != COLLECTION or meta.get("data_source") != COLLECTION or meta.get("data_type") != KIND:
            raise RuntimeError(f"patent baseline naming incomplete at {original['_id']}")
        seen.add(record_id)
        document["_meta"] = meta
        document["data_source"] = COLLECTION
        created_at = meta.get("created_at") or now
        updated_at = meta.get("updated_at") or created_at
        key = {"record_id": record_id, "data_source": COLLECTION, "data_type": KIND}
        keys.append(key)
        rds.append({**key, "raw_data": document, "created_at": created_at, "updated_at": updated_at})
        normalized = normalize_google_patent(document)
        normalized.update(key)
        if isinstance(normalized.get("figures"), str):
            normalized["figures"] = json.loads(normalized["figures"])
        normalized["created_at"] = created_at
        normalized["updated_at"] = updated_at
        ods.append(normalized)
    return (
        json.dumps(rds, ensure_ascii=False, default=base.json_default),
        json.dumps(ods, ensure_ascii=False, default=base.json_default),
        json.dumps(keys, ensure_ascii=True),
    )


async def write_batch(connection: asyncpg.Connection, docs: list[dict[str, Any]]) -> None:
    for start in range(0, len(docs), SQL_BATCH):
        chunk = docs[start:start + SQL_BATCH]
        rds, ods, keys = prepare_rows(chunk)
        async with connection.transaction():
            await connection.execute("SET LOCAL statement_timeout='900s'")
            await connection.execute("SET LOCAL lock_timeout='30s'")
            await connection.execute(RDS_SQL, rds)
            await connection.execute(ODS_SQL, ods)
            rds_count = await connection.fetchval(base.VERIFY_SQL.format(table="ts_rds.rds_patent"), keys)
            ods_count = await connection.fetchval(base.VERIFY_SQL.format(table="ts_ods.ods_patent"), keys)
            if rds_count != len(chunk) or ods_count != len(chunk):
                raise RuntimeError(f"patent baseline batch verification failed: rds={rds_count} ods={ods_count} expected={len(chunk)}")


def read_batch(collection, query: dict[str, Any]) -> list[dict[str, Any]]:
    for attempt in range(5):
        try:
            return list(collection.find(query).sort("_id", 1).hint("_id_").limit(BATCH)
                        .batch_size(BATCH).max_time_ms(300000))
        except (AutoReconnect, CursorNotFound, ExecutionTimeout):
            if attempt == 4:
                raise
            time.sleep(min(5 * (attempt + 1), 20))
    raise AssertionError("unreachable")


async def worker_async(root: Path, number: int, part: dict[str, str | None]) -> None:
    progress_path = root / f"worker-{number}.json"
    progress = base.read_json(progress_path, {
        "worker": number, "processed": 0, "batches": 0, "last_id": None,
        "done": False, "last_error": None,
    })
    client = base.mongo_client()
    pg: asyncpg.Connection | None = None
    try:
        collection = client[settings.db_name][COLLECTION]
        while not progress.get("done"):
            query: dict[str, Any] = {"_id": {"$gte": ObjectId(part["lower"])}}
            if progress.get("last_id"):
                query["_id"] = {"$gt": ObjectId(progress["last_id"])}
            if part.get("upper"):
                query["_id"]["$lt"] = ObjectId(part["upper"])
            docs = read_batch(collection, query)
            if not docs:
                progress.update(done=True, observed_at=datetime.now(timezone.utc).isoformat(), last_error=None)
                base.atomic_json(progress_path, progress)
                break
            for attempt in range(5):
                try:
                    if pg is None or pg.is_closed():
                        pg = await base.pg_connect(200 + number)
                    await write_batch(pg, docs)
                    break
                except (asyncpg.PostgresError, ConnectionError, OSError, asyncio.TimeoutError):
                    if pg is not None and not pg.is_closed():
                        await pg.close()
                    pg = None
                    if attempt == 4:
                        raise
                    await asyncio.sleep(min(5 * (attempt + 1), 20))
            progress.update(processed=int(progress.get("processed", 0)) + len(docs),
                            batches=int(progress.get("batches", 0)) + 1,
                            last_id=str(docs[-1]["_id"]), observed_at=datetime.now(timezone.utc).isoformat(),
                            last_error=None)
            base.atomic_json(progress_path, progress)
    except BaseException as error:
        progress.update(last_error=f"{type(error).__name__}: {error}", observed_at=datetime.now(timezone.utc).isoformat())
        base.atomic_json(progress_path, progress)
        raise
    finally:
        client.close()
        if pg is not None and not pg.is_closed():
            await pg.close()


def worker(root_value: str, number: int, part: dict[str, str | None]) -> None:
    asyncio.run(worker_async(Path(root_value), number, part))


def validate_namespace(run_root: Path) -> tuple[int, str, str, str]:
    state = base.read_json(Path("/data/clawer/output/sec_migration_20261104/patent-cdc-state.json"))
    if state.get("stage") != "naming_complete_waiting_backfill" or state.get("cdc_active") is not False:
        raise RuntimeError("patent baseline started before naming gate")
    ranges = base.read_json(run_root / "ranges-name.json")
    if (ranges.get("run_id") != state.get("run_id") or ranges.get("phase") != "name"
            or ranges.get("collection") != COLLECTION):
        raise RuntimeError("patent baseline naming range identity changed")
    parts = ranges.get("ranges") or []
    if (len(parts) != 8 or any(type(part.get("worker")) is not int for part in parts)
            or {part["worker"] for part in parts} != set(range(8))):
        raise RuntimeError("patent baseline naming partitions changed")
    total = 0
    previous = None
    for part in sorted(parts, key=lambda value: value["lower"]):
        if (ObjectId(part["lower"]) >= ObjectId(part["upper"])
                or (previous is not None and ObjectId(part["lower"]) < previous)):
            raise RuntimeError("patent baseline naming partitions overlap")
        previous = ObjectId(part["upper"])
        checkpoint = base.read_json(run_root / "journals" / f"name-worker-{part['worker']}.json")
        if (any(checkpoint.get(key) != value for key, value in {
                "run_id": ranges["run_id"], "phase": "name", "worker": part["worker"],
                "lower": part["lower"], "upper": part["upper"],
            }.items()) or checkpoint.get("complete") is not True
                or type(checkpoint.get("processed")) is not int or checkpoint["processed"] < 0):
            raise RuntimeError("patent baseline naming checkpoint is incomplete or differs")
        total += checkpoint["processed"]
    client = base.mongo_client()
    try:
        database = client[settings.db_name]
        rows = list(database.list_collections(filter={"name": COLLECTION}, maxTimeMS=15000))
        uid = bytes(rows[0]["info"]["uuid"]).hex() if len(rows) == 1 else None
        if uid != ranges.get("uuid"):
            raise RuntimeError("patent baseline collection UUID changed")
        record_index = database[COLLECTION].index_information().get("_meta.record_id_1") or {}
        if record_index.get("key") != [("_meta.record_id", 1)] or record_index.get("unique") is not True:
            raise RuntimeError("patent baseline record_id uniqueness index changed")
        first = database[COLLECTION].find_one({}, {"_id": 1}, sort=[("_id", 1)])
        last = database[COLLECTION].find_one({}, {"_id": 1}, sort=[("_id", -1)])
        if not first or not last:
            raise RuntimeError("patent baseline collection is empty")
    finally:
        client.close()
    return total, str(uid), str(first["_id"]), str(last["_id"])


async def exact_pg_counts() -> tuple[int, int]:
    connection = await base.pg_connect(299)
    try:
        await connection.execute("SET statement_timeout='3600s'")
        rds = await connection.fetchval("SELECT count(*) FROM ts_rds.rds_patent WHERE data_source=$1 AND data_type=$2", COLLECTION, KIND)
        ods = await connection.fetchval("SELECT count(*) FROM ts_ods.ods_patent WHERE data_source=$1 AND data_type=$2", COLLECTION, KIND)
        return int(rds), int(ods)
    finally:
        await connection.close()


async def controller(root: Path, run_root: Path) -> None:
    import fcntl

    root.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path = Path("/data/clawer/output/sec_migration_20261104/patent-cdc-state.json")
        while True:
            state = base.read_json(state_path)
            if state.get("last_error"):
                raise RuntimeError("patent pipeline failed before baseline")
            if state.get("stage") == "naming_complete_waiting_backfill" and state.get("cdc_active") is False:
                break
            base.atomic_json(root / "state.json", {"observed_at": datetime.now(timezone.utc).isoformat(),
                                                   "stage": "waiting_for_patent_naming", "last_error": None})
            await asyncio.sleep(30)
        total, mongo_uuid, first, last = validate_namespace(run_root)
        parts = base.split_ranges(first, last)
        plan = {"protocol": PROTOCOL, "workers": WORKERS, "batch_size": BATCH, "total": total,
                "mongo_uuid": mongo_uuid, "parts": parts}
        plan_path = root / "plan.json"
        if plan_path.exists() and base.read_json(plan_path) != plan:
            raise RuntimeError("patent baseline resume plan changed")
        if not plan_path.exists():
            base.atomic_json(plan_path, plan)
        context = multiprocessing.get_context("spawn")
        processes = [context.Process(target=worker, args=(str(root), number, part), name=f"patent-baseline-{number}")
                     for number, part in enumerate(parts)]
        for process in processes:
            process.start()
        try:
            while any(process.is_alive() for process in processes):
                progress = [base.read_json(root / f"worker-{number}.json", {"worker": number, "processed": 0})
                            for number in range(WORKERS)]
                errors = [value for value in progress if value.get("last_error")]
                base.atomic_json(root / "state.json", {"observed_at": datetime.now(timezone.utc).isoformat(),
                    "stage": "direct_baseline", "total": total,
                    "processed": sum(int(value.get("processed", 0)) for value in progress),
                    "workers": sum(process.is_alive() for process in processes), "batch_size": BATCH,
                    "last_error": errors[0]["last_error"] if errors else None})
                if errors or any(process.exitcode not in (None, 0) for process in processes):
                    for process in processes:
                        if process.is_alive():
                            process.terminate()
                    raise RuntimeError("patent direct baseline worker failed")
                await asyncio.sleep(10)
            progress = [base.read_json(root / f"worker-{number}.json") for number in range(WORKERS)]
            processed = sum(int(value.get("processed", 0)) for value in progress)
            if any(process.exitcode != 0 for process in processes) or processed <= 0 or not all(value.get("done") is True for value in progress):
                raise RuntimeError(f"patent direct baseline coverage is incomplete: {processed}")
            rds_count, ods_count = await exact_pg_counts()
            if rds_count != processed or ods_count != processed:
                raise RuntimeError(f"patent direct baseline counts differ: rds={rds_count} ods={ods_count} expected={processed}")
            marker = {"protocol": PROTOCOL, "scope": KIND, "collection": COLLECTION, "mongo_uuid": mongo_uuid,
                      "mongo_total": processed, "rds_count": rds_count, "ods_count": ods_count,
                      "missing_rds": 0, "missing_ods": 0, "validated": True}
            base.atomic_json(root / "baseline-receipt.json", {**marker, "workers": progress,
                             "completed_at": datetime.now(timezone.utc).isoformat()})
            base.atomic_json(run_root / "baseline-ready.json", marker)
            base.atomic_json(root / "state.json", {"observed_at": datetime.now(timezone.utc).isoformat(),
                                                   "stage": "complete", "total": processed, "processed": processed,
                                                   "workers": 0, "batch_size": BATCH, "last_error": None})
        finally:
            for process in processes:
                process.join(5)
                if process.is_alive():
                    process.terminate();process.join(5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--run-root", required=True)
    args = parser.parse_args()
    asyncio.run(controller(Path(args.root), Path(args.run_root)))


if __name__ == "__main__":
    main()
