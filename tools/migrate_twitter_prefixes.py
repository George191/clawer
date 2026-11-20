"""Finish the two obsolete Twitter namespaces using the existing migration journal."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient

from tools import migrate_download_paths as migration

SCOPE = "prefix IN ('social_media/twitter','twitter/twitter')"


def worker(path, shard):
    client = migration.storage()
    mongo_client = MongoClient(migration.settings.db_url, serverSelectionTimeoutMS=15000)
    mongo = mongo_client[migration.settings.db_name]
    registered = asyncio.run(migration.routes())

    def apply(group):
        db = migration.journal(path)
        try:
            migration.apply_group(db, client, mongo, registered, *group)
        finally:
            db.close()

    async def run():
        db = migration.journal(path)
        pg = migration.get_pg_client()
        await pg.connect(max_retries=1)
        try:
            while True:
                groups = db.execute(f'SELECT DISTINCT prefix,record_id FROM objects WHERE {SCOPE} AND shard=? AND phase="planned" LIMIT ?',
                                    (shard, migration.BATCH)).fetchall()
                if not groups:
                    break
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(apply, groups))
                print(json.dumps({'worker': shard, 'source_groups_updated': len(groups)}), flush=True)
            while True:
                groups = db.execute(f'SELECT DISTINCT prefix,record_id FROM objects WHERE {SCOPE} AND shard=? AND phase="source_updated" LIMIT ?',
                                    (shard, migration.BATCH)).fetchall()
                if not groups:
                    break
                semaphore = asyncio.Semaphore(8)

                async def verify(group):
                    async with semaphore:
                        check = migration.journal(path)
                        try:
                            return await migration.verify_group(check, client, mongo, registered, *group)
                        finally:
                            check.close()

                results = await asyncio.gather(*(verify(group) for group in groups))
                print(json.dumps({'worker': shard, 'verified_groups': sum(results), 'waiting': results.count(False)}), flush=True)
                if not all(results):
                    await asyncio.sleep(30)
            print(json.dumps({'worker': shard, 'verification_complete': True, 'old_objects_retained': True}), flush=True)
        finally:
            await pg.close()
            db.close()
            mongo_client.close()
    asyncio.run(run())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', type=Path, required=True)
    args = parser.parse_args()
    assert args.journal.is_file(), 'Resume the existing inventory journal'
    ctx = multiprocessing.get_context('spawn')
    processes = [ctx.Process(target=worker, args=(str(args.journal), n)) for n in range(migration.WORKERS)]
    for process in processes:
        process.start()
    for process in processes:
        process.join()
    if any(process.exitcode for process in processes):
        raise SystemExit(1)
