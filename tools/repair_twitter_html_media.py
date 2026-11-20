"""Re-download Twitter media saved as HTML; six resumable, disjoint OS workers."""
import argparse
import asyncio
import fcntl
from itertools import islice
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient
from app.config.settings import settings
from app.downloader.worker import DownloadWorker
from app.engine.template_loader import TemplateLoader
from app.storage.minio_client import get_business_metadata_minio_client
from app.utils.path import get_nested_value
from tools.migrate_download_paths import connect_pg, journal, repair_references, routes, storage, verify_group

WORKERS = 6
BATCH = 200
PREFIXES = ('tweet/twitter/', 'social_media/twitter/', 'twitter/twitter/')
PARTITIONS = [list('012'), list('345'), list('678'), list('9ab'), list('cd'), list('ef')]


def report(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2))
    temporary.replace(path)


async def work(folder, number, mode):
    folder = Path(folder)
    lock = (folder / f'worker-{number}.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    plan = json.loads((folder / 'plan.json').read_text())
    db = journal(folder / f'worker-{number}.sqlite')
    db.execute('CREATE TABLE IF NOT EXISTS items (old TEXT PRIMARY KEY, new TEXT, status TEXT, error TEXT, attempts INTEGER)')
    db.execute('CREATE TABLE IF NOT EXISTS checkpoints (prefix TEXT PRIMARY KEY, last_key TEXT, done INTEGER)')
    mongo = MongoClient(settings.db_url, serverSelectionTimeoutMS=10000, tz_aware=True)
    collection = mongo[settings.db_name]['tweet_twitter']
    client = storage()
    downloader = DownloadWorker(template_name='twitter', data_type='tweet')
    # Use the verified published storage endpoint for this standalone repair.
    downloader._minio._client = client
    metadata = get_business_metadata_minio_client()
    metadata._client = client
    registered = await routes()
    pg = await connect_pg()
    template = (await TemplateLoader().load_released('twitter', validate_params=False, load_adapter=False)).template

    async def process(key):
        previous = db.execute('SELECT status,attempts,new FROM items WHERE old=?', (key,)).fetchone()
        if previous and previous[0] in ('repaired', 'deleted', 'unreferenced'):
            return
        attempts = (previous[1] if previous else 0) + 1
        status, new, error = 'failed', '', ''
        try:
            record_id = key.split('/')[2]
            if len(record_id) != 32 or any(c not in '0123456789abcdef' for c in record_id):
                raise ValueError('Unexpected media record directory')
            doc = collection.find_one({'_meta.record_id': record_id})
            prefix = '/'.join(key.split('/')[:2])
            pending = db.execute('SELECT new FROM objects WHERE old=?', (key,)).fetchone()
            if pending and doc and pending[0] in json.dumps(doc, default=str):
                db.execute('UPDATE objects SET phase="source_updated" WHERE old=?', (key,))
                db.commit()
                await repair_references(db, mongo[settings.db_name], registered, prefix, record_id)
                if not await verify_group(db, client, mongo[settings.db_name], registered, prefix, record_id):
                    raise RuntimeError('Replacement is not verified in Mongo, RDS and ODS')
                db.execute('INSERT OR REPLACE INTO items VALUES (?,?,?,?,?)', (key, pending[0], 'repaired', '', attempts))
                db.commit()
                return
            items = downloader._plan_download(doc, template, 'twitter', 'tweet')[0] if doc else []
            matching = [item for item in items if get_nested_value(doc, item.asset_key) == key]
            if not matching:
                status = 'unreferenced'
            elif mode == 'inspect':
                status = 'planned'
            else:
                meta = doc['_meta']
                if meta.get('download_status') == 'downloading':
                    raise RuntimeError('Record is claimed by the production downloader; resume later')
                updates = {}
                guard = {'_id': doc['_id'], '_meta.download_version': meta.get('download_version'),
                         '_meta.download_status': {'$ne': 'downloading'}}
                for item in matching:
                    new = await downloader._download_item(item, 'twitter', 'tweet', record_id, template.effective_download_use_proxy)
                    if not new:
                        raise RuntimeError('Original media is unavailable; old reference retained')
                    info = await asyncio.to_thread(client.stat_object, settings.minio_bucket, new)
                    if not (info.content_type or '').startswith(('image/', 'video/', 'audio/')):
                        raise RuntimeError('Replacement is not verified media')
                    updates[item.asset_key] = new
                    guard[item.asset_key] = key
                db.execute('INSERT OR REPLACE INTO objects (old,prefix,record_id,shard,new,phase) VALUES (?,?,?,?,?,"downloaded")',
                           (key, prefix, record_id, number, new))
                db.commit()
                updates.update({'_meta.updated_at': datetime.now(timezone.utc), '_meta.sync_status': 'pending',
                                '_meta.write_token': uuid4().hex})
                result = collection.update_one(guard, {'$set': updates, '$inc': {'_meta.download_version': 1}})
                if result.matched_count != 1:
                    raise RuntimeError('Concurrent source change; resume from the journal')
                db.execute('UPDATE objects SET phase="source_updated" WHERE old=?', (key,))
                db.commit()
                await repair_references(db, mongo[settings.db_name], registered, prefix, record_id)
                if not await verify_group(db, client, mongo[settings.db_name], registered, prefix, record_id):
                    raise RuntimeError('Replacement is not verified in Mongo, RDS and ODS')
                status = 'repaired'
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'[:1000]
        db.execute('INSERT OR REPLACE INTO items VALUES (?,?,?,?,?)', (key, new, status, error, attempts))
        db.commit()

    def counts():
        return dict(db.execute('SELECT status,count(*) FROM items GROUP BY status'))

    try:
        if mode == 'clean':
            for key, new in db.execute('SELECT old,new FROM items WHERE status IN ("repaired","cleanup_failed")').fetchall():
                try:
                    prefix, record_id = '/'.join(key.split('/')[:2]), key.split('/')[2]
                    db.execute('UPDATE objects SET phase="source_updated" WHERE old=?', (key,))
                    db.commit()
                    if not await verify_group(db, client, mongo[settings.db_name], registered, prefix, record_id):
                        raise RuntimeError('References changed; old object retained')
                    try:
                        info = client.stat_object(settings.minio_bucket, key)
                    except Exception as exc:
                        if getattr(exc, 'code', None) != 'NoSuchKey':
                            raise
                        info = None
                    if info is not None:
                        if not (info.content_type or '').lower().startswith(('text/html', 'application/xhtml+xml')):
                            raise RuntimeError('Old object is no longer HTML; retained for review')
                        client.remove_object(settings.minio_bucket, key)
                    try:
                        client.stat_object(settings.minio_bucket, key)
                    except Exception as exc:
                        if getattr(exc, 'code', None) != 'NoSuchKey':
                            raise
                    else:
                        raise RuntimeError('Old object still exists after deletion')
                    db.execute('UPDATE items SET status="deleted",error="" WHERE old=?', (key,))
                except Exception as exc:
                    db.execute('UPDATE items SET status="cleanup_failed",error=? WHERE old=?', (str(exc)[:1000], key))
                db.commit()
            report(folder / f'worker-{number}.json', {'worker': number, 'pid': os.getpid(), 'counts': counts(),
                                                    'complete': not counts().get('cleanup_failed') and not counts().get('failed')})
            return
        for (key,) in db.execute('SELECT old FROM items WHERE status IN ("failed","planned")').fetchall():
            await process(key)
        prefixes = [prefix + digit for prefix in PREFIXES for digit in plan['partitions'][number]]
        priority = [prefix + record + '/' for prefix in PREFIXES for record in plan['priority_records']
                    if record[0] in plan['partitions'][number]]
        for prefix in priority + prefixes:
            checkpoint = db.execute('SELECT last_key,done FROM checkpoints WHERE prefix=?', (prefix,)).fetchone()
            if checkpoint and checkpoint[1]:
                continue
            iterator = client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True,
                                           include_user_meta=True, start_after=checkpoint[0] if checkpoint else None)
            while batch := list(islice(iterator, BATCH)):
                for obj in batch:
                    if (obj.metadata or {}).get('content-type', '').lower().startswith(('text/html', 'application/xhtml+xml')):
                        await process(obj.object_name)
                db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?,0)', (prefix, batch[-1].object_name))
                db.commit()
                report(folder / f'worker-{number}.json', {'worker': number, 'pid': os.getpid(), 'counts': counts(), 'complete': False})
            db.execute('INSERT OR REPLACE INTO checkpoints VALUES (?,?,1)', (prefix, checkpoint[0] if checkpoint else ''))
            db.commit()
        report(folder / f'worker-{number}.json', {'worker': number, 'pid': os.getpid(), 'counts': counts(),
                                                'traversal_complete': True, 'complete': not counts().get('failed')})
    finally:
        await downloader.stop()
        await metadata.close()
        await pg.close()
        mongo.close()
        db.close()
        lock.close()


def worker(folder, number, mode):
    with (Path(folder) / f'worker-{number}.log').open('a', buffering=1) as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        asyncio.run(work(folder, number, mode))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['inspect', 'apply', 'clean'])
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--priority-record', action='append', default=[])
    args = parser.parse_args()
    folder = Path(args.state_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    lock = (folder / 'controller.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    path = folder / 'plan.json'
    if not path.exists():
        assert all(len(value) == 32 and set(value) <= set('0123456789abcdef') for value in args.priority_record)
        report(path, {'workers': WORKERS, 'batch_size': BATCH, 'partitions': PARTITIONS, 'priority_records': args.priority_record})
    plan = json.loads(path.read_text())
    assert plan['workers'] == WORKERS and plan['batch_size'] == BATCH <= 3000
    assert plan['partitions'] == PARTITIONS and sorted(sum(plan['partitions'], [])) == list('0123456789abcdef')
    context = multiprocessing.get_context('spawn')
    processes = [context.Process(target=worker, args=(str(folder), number, args.mode)) for number in range(WORKERS)]
    for process in processes:
        process.start()
    report(folder / 'processes.json', {'controller': os.getpid(), 'workers': [p.pid for p in processes]})
    for process in processes:
        process.join()
    assert all(process.exitcode == 0 for process in processes), 'Worker failed; inspect logs and resume the same state directory'
    reports = [json.loads((folder / f'worker-{number}.json').read_text()) for number in range(WORKERS)]
    report(folder / (args.mode + '-result.json'), {'complete': all(value['complete'] for value in reports), 'workers': reports})


if __name__ == '__main__':
    main()
