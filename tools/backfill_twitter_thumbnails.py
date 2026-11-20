"""Fill missing ODS thumbnails from Mongo assets with six resumable OS workers."""
import argparse
import asyncio
import json
import multiprocessing
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asyncpg
from pymongo import MongoClient

from app.config.settings import settings
from app.etl.normalizers.social_media import normalize_twitter_media
from app.storage.postgres_client import _resolved_pg_url

WORKERS = 6
BATCH = 3000


def worker(folder, shard):
    async def run():
        journal = sqlite3.connect(Path(folder) / f'worker-{shard}.sqlite')
        journal.execute('CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY, last_id TEXT, scanned INTEGER, changed INTEGER, done INTEGER)')
        previous = journal.execute('SELECT last_id,scanned,changed,done FROM checkpoint WHERE id=1').fetchone()
        if previous and previous[3]:
            journal.close()
            return
        last, scanned, changed = previous[:3] if previous else (None, 0, 0)
        start = f'{((1 << 128) * shard // WORKERS):032x}'
        end = f'{((1 << 128) * (shard + 1) // WORKERS):032x}' if shard < WORKERS - 1 else None
        mongo = MongoClient(settings.db_url, serverSelectionTimeoutMS=15000)
        col = mongo[settings.db_name]['tweet_twitter']
        pg = await asyncpg.connect(_resolved_pg_url().replace('postgresql+asyncpg://', 'postgresql://'),
                                   timeout=10, command_timeout=60)
        try:
            while True:
                bounds = {'$gt': last} if last else {'$gte': start}
                if end:
                    bounds['$lt'] = end
                docs = list(col.find({'_meta.record_id': bounds},
                                     {'id': 1, 'media': 1, 'assets': 1, '_meta.record_id': 1})
                            .sort('_meta.record_id', 1).limit(BATCH).max_time_ms(120000))
                if not docs:
                    journal.execute('INSERT OR REPLACE INTO checkpoint VALUES(1,?,?,?,1)', (last, scanned, changed))
                    journal.commit()
                    break
                updates = []
                for doc in docs:
                    assert int(start, 16) <= int(doc['_meta']['record_id'], 16)
                    for media in normalize_twitter_media(doc):
                        if media['thumbnail']:
                            updates.append((media['thumbnail'], media['record_id'], media['source_url']))
                async with pg.transaction():
                    for thumbnail, record_id, source_url in updates:
                        result = await pg.execute("""UPDATE ts_ods.ods_social_media
                            SET thumbnail=$1, updated_at=now()
                            WHERE record_id=$2 AND data_source='tweet_twitter' AND data_type='social_media'
                              AND source_url IS NOT DISTINCT FROM $3 AND thumbnail IS NULL""",
                                                  thumbnail, record_id, source_url)
                        changed += int(result.split()[-1])
                last = docs[-1]['_meta']['record_id']
                scanned += len(docs)
                journal.execute('INSERT OR REPLACE INTO checkpoint VALUES(1,?,?,?,0)', (last, scanned, changed))
                journal.commit()
                print(json.dumps({'worker': shard, 'scanned': scanned, 'changed': changed}), flush=True)
        finally:
            await pg.close()
            mongo.close()
            journal.close()
    asyncio.run(run())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', type=Path, required=True)
    args = parser.parse_args()
    args.journal.mkdir(parents=True, exist_ok=True)
    ctx = multiprocessing.get_context('spawn')
    processes = [ctx.Process(target=worker, args=(str(args.journal), n)) for n in range(WORKERS)]
    for process in processes:
        process.start()
    for process in processes:
        process.join()
    if any(process.exitcode for process in processes):
        raise SystemExit(1)
