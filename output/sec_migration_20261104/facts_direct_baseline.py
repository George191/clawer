"""Backfill the canonical SEC facts snapshot directly into RDS and ODS."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timezone
import json
import multiprocessing
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any

import asyncpg
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import AutoReconnect, CursorNotFound, ExecutionTimeout, PyMongoError

from app.config.settings import settings
from app.etl.normalizers.financial_fact import normalize_sec_edgar_financial_fact

sys.path.append(str(Path(__file__).resolve().parent.parent / 'source_dedup_20261109'))
import registry_targets as targets


PROTOCOL = "sec_direct_baseline_v1"
COLLECTION = "financial_fact_sec_edgar"
KIND = "financial_fact"
WORKERS = 6
BATCH = 3000
SQL_BATCH = 300
LOCK = Path("/data/clawer/output/sec_migration_20261104/.facts-direct-baseline.lock")

RDS_SQL = """
INSERT INTO {table}
 (record_id,data_source,data_type,raw_data,kafka_offset,kafka_partition,kafka_topic,created_at,updated_at)
SELECT x.record_id,x.data_source,x.data_type,x.raw_data,NULL,NULL,NULL,x.created_at,x.updated_at
FROM jsonb_to_recordset($1::jsonb) AS x(
 record_id text,data_source text,data_type text,raw_data jsonb,
 created_at timestamptz,updated_at timestamptz)
ON CONFLICT (record_id,data_source,data_type) DO NOTHING
"""

ODS_SQL = """
INSERT INTO {table}
 (record_id,data_source,data_type,cik,entity_name,taxonomy,concept,concept_label,
  unit,value,accn,form,filed,fy,fp,frame,start_date,end_date,is_amendment,created_at,updated_at)
SELECT x.record_id,x.data_source,x.data_type,x.cik,x.entity_name,x.taxonomy,x.concept,x.concept_label,
 x.unit,x.value,x.accn,x.form,x.filed,x.fy,x.fp,x.frame,x.start_date,x.end_date,
 COALESCE(x.is_amendment,FALSE),x.created_at,x.updated_at
FROM jsonb_to_recordset($1::jsonb) AS x(
 record_id text,data_source text,data_type text,cik text,entity_name text,taxonomy text,
 concept text,concept_label text,unit text,value numeric,accn text,form text,filed date,
 fy integer,fp text,frame text,start_date date,end_date date,is_amendment boolean,
 created_at timestamptz,updated_at timestamptz)
ON CONFLICT (record_id,data_source,data_type) DO NOTHING
"""

VERIFY_SQL = """
SELECT count(*)
FROM jsonb_to_recordset($1::jsonb) AS x(record_id text,data_source text,data_type text)
JOIN {table} AS target USING(record_id,data_source,data_type)
"""


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2, default=str)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {} if default is None else dict(default)


def split_ranges(first: str, last: str, workers: int = WORKERS) -> list[dict[str, str | None]]:
    lower = int(first, 16)
    end = int(last, 16) + 1
    points = [lower + (end - lower) * number // workers for number in range(workers + 1)]
    if not lower < end or any(points[index] >= points[index + 1] for index in range(workers)):
        raise RuntimeError("facts baseline ObjectId range is too small")
    return [
        {
            "lower": points[number].to_bytes(12, "big").hex(),
            "upper": None if number == workers - 1 else points[number + 1].to_bytes(12, "big").hex(),
        }
        for number in range(workers)
    ]


def mongo_client() -> MongoClient:
    return MongoClient(
        settings.db_url,
        serverSelectionTimeoutMS=15000,
        connectTimeoutMS=15000,
        socketTimeoutMS=360000,
        retryReads=False,
        retryWrites=False,
        tz_aware=True,
        appname="sec-facts-direct-baseline",
    )


def json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


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
            raise RuntimeError("facts baseline invalid or duplicate record_id in batch")
        if meta.get("template") != COLLECTION or meta.get("data_source") != COLLECTION or meta.get("data_type") != KIND:
            raise RuntimeError(f"facts baseline naming incomplete at {original['_id']}")
        seen.add(record_id)
        document["_meta"] = meta
        document["data_source"] = COLLECTION
        created_at = meta.get("created_at") or now
        updated_at = meta.get("updated_at") or created_at
        key = {"record_id": record_id, "data_source": COLLECTION, "data_type": KIND}
        keys.append(key)
        rds.append({
            **key,
            "raw_data": document,
            "created_at": created_at,
            "updated_at": updated_at,
        })
        normalized = normalize_sec_edgar_financial_fact(document)
        normalized.update(key)
        normalized["created_at"] = created_at
        normalized["updated_at"] = updated_at
        ods.append(normalized)
    return (
        json.dumps(rds, ensure_ascii=False, default=json_default),
        json.dumps(ods, ensure_ascii=False, default=json_default),
        json.dumps(keys, ensure_ascii=True),
    )


async def pg_connect(worker: int) -> asyncpg.Connection:
    return await targets.connect(f"sec-facts-baseline-{worker}", timeout=30, command_timeout=900)


async def write_batch(connection: asyncpg.Connection, docs: list[dict[str, Any]]) -> None:
    for start in range(0, len(docs), SQL_BATCH):
        chunk = docs[start:start + SQL_BATCH]
        rds, ods, keys = prepare_rows(chunk)
        async with connection.transaction():
            await connection.execute("SET LOCAL statement_timeout='900s'")
            await connection.execute("SET LOCAL lock_timeout='30s'")
            await connection.execute(targets.render(RDS_SQL, 'rds', KIND), rds)
            await connection.execute(targets.render(ODS_SQL, 'ods', KIND), ods)
            rds_count = await connection.fetchval(targets.render(VERIFY_SQL, 'rds', KIND), keys)
            ods_count = await connection.fetchval(targets.render(VERIFY_SQL, 'ods', KIND), keys)
            if rds_count != len(chunk) or ods_count != len(chunk):
                raise RuntimeError(f"facts baseline batch verification failed: rds={rds_count} ods={ods_count} expected={len(chunk)}")


def read_batch(collection, query: dict[str, Any], *, limit: int = BATCH) -> list[dict[str, Any]]:
    for attempt in range(5):
        try:
            return list(
                collection.find(query).sort("_id", 1).hint("_id_").limit(limit)
                .batch_size(limit).max_time_ms(300000)
            )
        except (AutoReconnect, CursorNotFound, ExecutionTimeout):
            if attempt == 4:
                raise
            time.sleep(min(5 * (attempt + 1), 20))
    raise AssertionError("unreachable")


async def worker_async(root: Path, number: int, part: dict[str, str | None]) -> None:
    progress_path = root / f"worker-{number}.json"
    progress = read_json(progress_path, {
        "worker": number, "processed": 0, "batches": 0, "last_id": None,
        "done": False, "last_error": None,
    })
    if (root / "STOP").exists():
        return
    client = mongo_client()
    pg: asyncpg.Connection | None = None
    try:
        collection = client[settings.db_name][COLLECTION]
        pg = await pg_connect(number)
        while not progress.get("done"):
            if (root / "STOP").exists():
                return
            query: dict[str, Any] = {"_id": {"$gte": ObjectId(part["lower"])}}
            if progress.get("last_id"):
                query["_id"] = {"$gt": ObjectId(progress["last_id"])}
            if part.get("upper"):
                query["_id"]["$lt"] = ObjectId(part["upper"])
            docs = read_batch(collection, query)
            if (root / "STOP").exists():
                return
            if not docs:
                progress.update(done=True, observed_at=datetime.now(timezone.utc).isoformat(), last_error=None)
                atomic_json(progress_path, progress)
                break
            for start in range(0, len(docs), SQL_BATCH):
                chunk = docs[start:start + SQL_BATCH]
                for attempt in range(5):
                    if (root / "STOP").exists():
                        return
                    try:
                        if pg.is_closed():
                            pg = await pg_connect(number)
                        await write_batch(pg, chunk)
                        break
                    except (asyncpg.PostgresError, asyncio.TimeoutError, ConnectionError, OSError):
                        if pg is not None and not pg.is_closed():
                            await pg.close()
                        if attempt == 4:
                            raise
                        await asyncio.sleep(min(5 * (attempt + 1), 20))
                        pg = await pg_connect(number)
                progress.update(
                    processed=int(progress.get("processed", 0)) + len(chunk),
                    batches=int(progress.get("batches", 0)) + 1,
                    last_id=str(chunk[-1]["_id"]),
                    observed_at=datetime.now(timezone.utc).isoformat(),
                    last_error=None,
                )
                atomic_json(progress_path, progress)
    except BaseException as error:
        progress.update(last_error=f"{type(error).__name__}: {error}", observed_at=datetime.now(timezone.utc).isoformat())
        atomic_json(progress_path, progress)
        raise
    finally:
        client.close()
        if pg is not None and not pg.is_closed():
            await pg.close()


def worker(root_value: str, number: int, part: dict[str, str | None]) -> None:
    asyncio.run(worker_async(Path(root_value), number, part))


async def exact_pg_counts() -> tuple[int, int]:
    connection = await pg_connect(99)
    try:
        await connection.execute("SET statement_timeout='3600s'")
        rds = await connection.fetchval(
            f"SELECT count(*) FROM {targets.table('rds', KIND)} WHERE data_source=$1 AND data_type=$2",
            COLLECTION, KIND,
        )
        ods = await connection.fetchval(
            f"SELECT count(*) FROM {targets.table('ods', KIND)} WHERE data_source=$1 AND data_type=$2",
            COLLECTION, KIND,
        )
        return int(rds), int(ods)
    finally:
        await connection.close()


def validate_namespace(facts_root: Path) -> tuple[int, str, str, str]:
    state = read_json(facts_root / "facts-cdc-state.json")
    if state.get("stage") != "naming_complete_waiting_backfill" or state.get("cdc_active") is not False:
        raise RuntimeError("facts baseline started before naming gate")
    bounds = read_json(facts_root / "bounds.json")
    client = mongo_client()
    try:
        database = client[settings.db_name]
        rows = list(database.list_collections(filter={"name": COLLECTION}, maxTimeMS=15000))
        if len(rows) != 1 or str(bytes(rows[0]["info"]["uuid"])) != bounds.get("uuid"):
            raise RuntimeError("facts baseline collection UUID changed")
        record_index = database[COLLECTION].index_information().get("_meta.record_id_1") or {}
        if record_index.get("key") != [("_meta.record_id", 1)] or record_index.get("unique") is not True:
            raise RuntimeError("facts baseline record_id uniqueness index changed")
    finally:
        client.close()
    return int(state["total"]), str(bounds["uuid"]), str(bounds["first"]), str(bounds["last"])


def record_paused(root: Path, total: int) -> None:
    progress = [read_json(root / f"worker-{number}.json") for number in range(WORKERS)]
    atomic_json(root / "state.json", {
        "observed_at": datetime.now(timezone.utc).isoformat(), "stage": "paused",
        "total": total, "processed": sum(int(value.get("processed", 0)) for value in progress),
        "workers": 0, "worker_pids": [], "batch_size": BATCH, "sql_batch_size": SQL_BATCH,
        "last_error": None,
    })


async def controller(root: Path, facts_root: Path) -> None:
    import fcntl

    root.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            state = read_json(facts_root / "facts-cdc-state.json")
            if (root / "STOP").exists():
                record_paused(root, int(state.get("total", read_json(root / "plan.json").get("total", 0))))
                return
            if state.get("last_error"):
                raise RuntimeError("facts naming failed before baseline")
            if state.get("stage") == "naming_complete_waiting_backfill" and state.get("cdc_active") is False:
                break
            atomic_json(root / "state.json", {
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "stage": "waiting_for_facts_naming", "last_error": None,
            })
            await asyncio.sleep(30)
        total, mongo_uuid, first, last = validate_namespace(facts_root)
        parts = split_ranges(first, last)
        plan = {"protocol": PROTOCOL, "workers": WORKERS, "batch_size": BATCH, "total": total,
                "mongo_uuid": mongo_uuid, "parts": parts}
        plan_path = root / "plan.json"
        if plan_path.exists() and read_json(plan_path) != plan:
            raise RuntimeError("facts baseline resume plan changed")
        if not plan_path.exists():
            atomic_json(plan_path, plan)
        context = multiprocessing.get_context("spawn")
        started_at = datetime.now(timezone.utc).isoformat()
        processes = [context.Process(target=worker, args=(str(root), number, part), name=f"facts-baseline-{number}")
                     for number, part in enumerate(parts)]
        for process in processes:
            process.start()
        try:
            while any(process.is_alive() for process in processes):
                progress = [read_json(root / f"worker-{number}.json", {"worker": number, "processed": 0})
                            for number in range(WORKERS)]
                errors = [value for value in progress if value.get("last_error")
                          and value.get("observed_at", "") >= started_at]
                stopping = (root / "STOP").exists()
                atomic_json(root / "state.json", {
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "stage": "pausing" if stopping else "direct_baseline", "total": total,
                    "processed": sum(int(value.get("processed", 0)) for value in progress),
                    "workers": sum(process.is_alive() for process in processes),
                    "worker_pids": [process.pid for process in processes if process.is_alive()],
                    "batch_size": BATCH, "sql_batch_size": SQL_BATCH,
                    "last_error": errors[0]["last_error"] if errors else None,
                })
                if not stopping and (errors or any(process.exitcode not in (None, 0) for process in processes)):
                    for process in processes:
                        if process.is_alive():
                            process.terminate()
                    raise RuntimeError("facts direct baseline worker failed")
                await asyncio.sleep(10)
            if (root / "STOP").exists():
                record_paused(root, total)
                return
            if any(process.exitcode != 0 for process in processes):
                raise RuntimeError("facts direct baseline worker exited")
            progress = [read_json(root / f"worker-{number}.json") for number in range(WORKERS)]
            processed = sum(int(value.get("processed", 0)) for value in progress)
            if processed != total or not all(value.get("done") is True for value in progress):
                raise RuntimeError(f"facts direct baseline coverage differs: {processed} != {total}")
            rds_count, ods_count = await exact_pg_counts()
            if (root / "STOP").exists():
                record_paused(root, total)
                return
            if rds_count != total or ods_count != total:
                raise RuntimeError(f"facts direct baseline counts differ: rds={rds_count} ods={ods_count} expected={total}")
            receipt = {
                "protocol": PROTOCOL, "scope": KIND, "collection": COLLECTION,
                "mongo_uuid": mongo_uuid, "mongo_total": total,
                "rds_count": rds_count, "ods_count": ods_count,
                "missing_rds": 0, "missing_ods": 0, "validated": True,
            }
            atomic_json(root / "baseline-receipt.json", {**receipt, "workers": progress,
                        "completed_at": datetime.now(timezone.utc).isoformat()})
            atomic_json(facts_root / "baseline-ready.json", receipt)
            atomic_json(root / "state.json", {
                "observed_at": datetime.now(timezone.utc).isoformat(), "stage": "complete",
                "total": total, "processed": processed, "workers": 0, "batch_size": BATCH,
                "last_error": None, "marker": str(facts_root / "baseline-ready.json"),
            })
        finally:
            for process in processes:
                process.join(5)
                if process.is_alive():
                    process.terminate()
                    process.join(5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--facts-root", required=True)
    args = parser.parse_args()
    asyncio.run(controller(Path(args.root), Path(args.facts_root)))


if __name__ == "__main__":
    main()
