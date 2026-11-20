"""Rename inventoried, unreferenced Twitter account files without discarding content."""
import argparse
import asyncio
import json
import multiprocessing
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient

from app.etl.normalizers.social_account import normalize_twitter_account
from tools import migrate_download_paths as migration


async def rename_one(db, client, mongo, registered, pg, old, record_id, new):
    prefix = '/'.join(old.split('/')[:2])
    if prefix not in {'social_media/twitter', 'twitter/twitter'}:
        raise ValueError('Unexpected residual prefix: ' + old)
    owners = migration.source_documents(mongo, registered, prefix, record_id)
    if len(owners) != 1 or owners[0][0]['data_type'] != 'account':
        raise RuntimeError('Residual must have one current account owner: ' + old)
    route, _ = owners[0]
    new = new or migration.copy_object(client, old, 'account/twitter', record_id)
    db.execute('UPDATE twitter_residuals SET new=?,phase="copied",error=NULL WHERE old=?', (new, old))
    db.commit()
    # Check the current owning graph after copying, before removing the old key.
    doc = mongo[route['collection_name']].find_one({'_meta.record_id': record_id})
    if not doc:
        raise RuntimeError('Residual owner disappeared: ' + old)
    output = normalize_twitter_account(doc)
    params = {'id': record_id, 'src': route['collection_name']}
    raw = await pg.fetch_all('SELECT raw_data FROM ts_rds.rds_account WHERE record_id=:id AND data_source=:src', params)
    params['id'] = output['record_id']
    ods = await pg.fetch_all('SELECT to_jsonb(t) AS row FROM ts_ods.ods_social_account t WHERE record_id=:id AND data_source=:src', params)
    if any(old in json.dumps(value, default=str) for value in (doc, raw, ods)):
        raise RuntimeError('Residual still has a current database reference: ' + old)
    if new == old or not new.startswith('account/twitter/' + record_id + '/'):
        raise ValueError('Invalid residual rename: ' + old)
    client.stat_object(migration.settings.minio_bucket, new)
    client.remove_object(migration.settings.minio_bucket, old)
    db.execute('UPDATE twitter_residuals SET phase="complete",error=NULL WHERE old=?', (old,))
    db.commit()


def worker(path, shard, gate):
    db = migration.journal(path)
    client = migration.storage(read_timeout=300)
    source = MongoClient(migration.settings.db_url, serverSelectionTimeoutMS=15000)
    mongo = source[migration.settings.db_name]
    with gate:
        registered = asyncio.run(migration.routes())

    async def run():
        with gate:
            pg = await migration.connect_pg()
        try:
            rows = db.execute('SELECT old,record_id,new FROM twitter_residuals WHERE shard=? AND phase!="complete" LIMIT ?',
                              (shard, migration.BATCH)).fetchall()
            for old, record_id, new in rows:
                assert int(record_id, 16) % migration.WORKERS == shard
                try:
                    with gate:
                        await rename_one(db, client, mongo, registered, pg, old, record_id, new)
                except Exception as error:
                    db.rollback()
                    db.execute('UPDATE twitter_residuals SET error=? WHERE old=?', (str(error)[:1000], old))
                    db.commit()
                    raise
            print(json.dumps({'worker': shard, 'residuals_renamed': len(rows)}), flush=True)
        finally:
            await pg.close()
    try:
        asyncio.run(run())
    finally:
        source.close()
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal', type=Path, required=True)
    args = parser.parse_args()
    assert args.journal.is_file(), 'Resume the existing residual inventory'
    ctx = multiprocessing.get_context('spawn')
    gate = ctx.BoundedSemaphore(2)
    processes = [ctx.Process(target=worker, args=(str(args.journal), shard, gate))
                 for shard in range(migration.WORKERS)]
    for process in processes:
        process.start()
    for process in processes:
        process.join()
    if any(process.exitcode for process in processes):
        raise SystemExit(1)
    db = migration.journal(args.journal)
    remaining = db.execute('SELECT count(*) FROM twitter_residuals WHERE phase!="complete"').fetchone()[0]
    db.close()
    if remaining:
        raise SystemExit('Residual inventory is not complete')
