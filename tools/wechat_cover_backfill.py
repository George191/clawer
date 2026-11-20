"""One-time WeChat cover downloads; six disjoint, resumable OS workers."""
import argparse
import asyncio
import fcntl
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bson import ObjectId
from pymongo import MongoClient

from app.config.settings import settings
from app.downloader.worker import DownloadWorker
from app.engine.template_loader import TemplateLoader
from app.records.download_resources import (
    decode_manifest, encode_manifest, encode_plan, item_fingerprint, template_download_items,
)

WORKERS = 6
BATCH_SIZE = 200


def collection():
    client = MongoClient(settings.db_url, serverSelectionTimeoutMS=10000, tz_aware=True)
    return client, client[settings.db_name]['acticle_wechat']


def write_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2))
    temp.replace(path)


async def work(folder, number):
    folder = Path(folder)
    lock = (folder / f'worker-{number}.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    plan = json.loads((folder / 'ranges.json').read_text())
    bounds = plan['ranges'][number]
    client, col = collection()
    journal = sqlite3.connect(folder / f'worker-{number}.sqlite')
    journal.execute('PRAGMA journal_mode=WAL')
    journal.execute('CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, status TEXT, object_key TEXT, error TEXT, attempts INTEGER, source_url TEXT)')
    journal.execute('CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), last_id TEXT)')
    downloader = DownloadWorker(template_name='acticle_wechat', data_type='acticle')
    released = await TemplateLoader().load_released('acticle_wechat', validate_params=False, load_adapter=False)
    template = released.template
    configs = [config for config in template.download if config.selector == 'cover']
    assert len(configs) == 1, 'Published WeChat template must declare exactly one cover download'

    async def process(doc):
        ident = str(doc['_id'])
        before = journal.execute('SELECT status,object_key,attempts,source_url FROM items WHERE id=?', (ident,)).fetchone()
        if before and before[0] in ('downloaded', 'already_local', 'no_cover'):
            return
        attempts = (before[2] if before else 0) + 1
        stored, status, error = '', 'failed', ''
        try:
            url = doc.get('cover')
            if not isinstance(url, str) or not url.startswith(('https://', 'http://')):
                status = 'no_cover'
            elif (doc.get('_meta') or {}).get('download_status') == 'downloading':
                raise RuntimeError('Record currently claimed by production downloader')
            else:
                meta = doc.get('_meta') or {}
                stored = (doc.get('assets') or {}).get('cover')
                if isinstance(stored, str) and stored:
                    if not await downloader._minio.object_exists(stored):
                        raise RuntimeError('Existing cover reference has no readable MinIO object')
                    status = 'already_local'
                else:
                    item, = template_download_items(doc, configs)
                    # A prior upload followed by a write conflict can be reused on resume.
                    stored = before[1] if before and before[1] and before[3] == url else ''
                    if not stored:
                        stored = await downloader._download_item(
                            item, 'acticle_wechat', 'acticle', meta['record_id'], template.effective_download_use_proxy,
                        )
                    if not stored or not await downloader._minio.object_exists(stored):
                        raise RuntimeError('Cover download failed or object verification failed')
                    inputs = decode_manifest(meta.get('download_inputs'))
                    verified = decode_manifest(meta.get('downloaded_inputs'))
                    fingerprint = item_fingerprint(item)
                    inputs['assets.cover'] = verified['assets.cover'] = fingerprint
                    download_plan = [p for p in meta.get('download_plan', []) if p['asset_key'] != 'assets.cover']
                    download_plan.extend(encode_plan([item]))
                    changes = {
                        'assets.cover': stored,
                        '_meta.download_inputs': encode_manifest(inputs),
                        '_meta.downloaded_inputs': encode_manifest(verified),
                        '_meta.download_plan': download_plan,
                        '_meta.download_version': int(meta.get('download_version') or 0) + 1,
                        '_meta.updated_at': datetime.now(timezone.utc),
                        '_meta.sync_status': 'pending',
                        '_meta.write_token': uuid4().hex,
                    }
                    if meta.get('download_status') == 'no_assets':
                        changes['_meta.download_status'] = 'downloaded'
                    guard = {'_id': doc['_id'], 'cover': url,
                             '_meta.download_version': meta.get('download_version'),
                             '_meta.write_token': meta.get('write_token'),
                             '_meta.download_claim_token': meta.get('download_claim_token'),
                             '_meta.download_status': meta.get('download_status')}
                    result = col.update_one(guard, {'$set': changes})
                    if result.matched_count != 1:
                        raise RuntimeError('Concurrent record change; retry with the latest record')
                    assert col.find_one({'_id': doc['_id']}, {'assets.cover': 1})['assets']['cover'] == stored
                    status = 'downloaded'
        except Exception as exc:
            error = type(exc).__name__ + ': ' + str(exc)[:300]
        journal.execute('INSERT OR REPLACE INTO items VALUES (?,?,?,?,?,?)', (ident, status, stored or '', error, attempts, doc.get('cover')))
        journal.commit()
        if error:
            print(json.dumps({'worker': number, 'id': ident, 'status': status, 'error': error}), flush=True)

    try:
        # Retry journaled failures before resuming the committed traversal cursor.
        failed = journal.execute("SELECT id FROM items WHERE status='failed'").fetchall()
        for ident, in failed:
            doc = col.find_one({'_id': ObjectId(ident)}, {'cover': 1, 'assets.cover': 1, '_meta': 1})
            if doc:
                await process(doc)
        checkpoint = journal.execute('SELECT last_id FROM checkpoint WHERE id=1').fetchone()
        last = checkpoint[0] if checkpoint else None
        while True:
            scope = {'$gte': ObjectId(bounds['start']), '$lte': ObjectId(bounds['end'])}
            if last:
                scope['$gt'] = ObjectId(last)
            rows = list(col.find({'_id': scope}, {'cover': 1, 'assets.cover': 1, '_meta': 1})
                        .sort('_id', 1).limit(BATCH_SIZE))
            if not rows:
                break
            for doc in rows:
                await process(doc)
            last = str(rows[-1]['_id'])
            journal.execute('INSERT OR REPLACE INTO checkpoint VALUES (1,?)', (last,))
            journal.commit()
            counts = dict(journal.execute('SELECT status,count(*) FROM items GROUP BY status'))
            report = {'worker': number, 'pid': os.getpid(), 'last_id': last, 'counts': counts, 'complete': False}
            write_json(folder / f'worker-{number}.json', report)
            print(json.dumps(report), flush=True)
        counts = dict(journal.execute('SELECT status,count(*) FROM items GROUP BY status'))
        write_json(folder / f'worker-{number}.json', {
            'worker': number, 'pid': os.getpid(), 'counts': counts,
            'complete': not counts.get('failed'), 'traversal_complete': True,
        })
    finally:
        await downloader.stop()
        journal.close()
        client.close()
        lock.close()


def worker(folder, number):
    with (Path(folder) / f'worker-{number}.log').open('a', buffering=1) as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        asyncio.run(work(folder, number))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', required=True)
    args = parser.parse_args()
    folder = Path(args.state_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    controller_lock = (folder / 'controller.lock').open('a')
    fcntl.flock(controller_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    path = folder / 'ranges.json'
    if not path.exists():
        client, col = collection()
        count = col.count_documents({}, hint='_id_')
        if count < WORKERS:
            raise RuntimeError('Expected at least six records for six disjoint worker ranges')
        points = [col.find({}, {'_id': 1}).sort('_id', 1).skip(count * i // WORKERS).limit(1).next()['_id'] for i in range(WORKERS)]
        high = col.find({}, {'_id': 1}).sort('_id', -1).limit(1).next()['_id']
        ranges = [{'start': str(point), 'end': str(ObjectId((int.from_bytes(points[i + 1].binary, 'big') - 1).to_bytes(12, 'big'))) if i + 1 < WORKERS else str(high)} for i, point in enumerate(points)]
        assert all(int(ranges[i]['end'], 16) + 1 == int(ranges[i + 1]['start'], 16) for i in range(WORKERS - 1))
        write_json(path, {'workers': WORKERS, 'batch_size': BATCH_SIZE, 'snapshot_count': count, 'ranges': ranges})
        client.close()
    plan = json.loads(path.read_text())
    assert plan['workers'] == WORKERS and plan['batch_size'] <= 3000
    ctx = multiprocessing.get_context('spawn')
    processes = [ctx.Process(target=worker, args=(str(folder), i)) for i in range(WORKERS)]
    for process in processes:
        process.start()
    write_json(folder / 'processes.json', {'controller': os.getpid(), 'workers': [p.pid for p in processes]})
    for process in processes:
        process.join()
    assert all(p.exitcode == 0 for p in processes), 'Worker crashed; inspect journals and resume the same state directory'
    reports = [json.loads((folder / f'worker-{i}.json').read_text()) for i in range(WORKERS)]
    assert sum(sum(r['counts'].values()) for r in reports) == plan['snapshot_count'], 'Snapshot coverage differs; investigate concurrent deletions'
    write_json(folder / 'result.json', {'complete': all(r['complete'] for r in reports), 'workers': reports})


if __name__ == '__main__':
    main()
