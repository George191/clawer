"""Migrate Twitter resources to the current format and remove verified old keys."""
import argparse
import asyncio
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from pathlib import Path
import sys
from datetime import datetime, timezone
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient

from tools import migrate_download_paths as migration
from app.records.field_names import normalize_record_fields, SYSTEM_ROOT_FIELDS
from app.utils.path import get_nested_value

SCOPE = "prefix IN ('social_media/twitter','twitter/twitter','tweet/twitter','account/twitter')"


def media_alias_updates(record):
    unset = {}

    def visit(value, path='', media=False):
        if isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f'{path}.{index}', media)
        elif isinstance(value, dict):
            if media and 'contentType' in value and 'content_type' in value:
                if value['contentType'] != value['content_type']:
                    raise ValueError('Conflicting media content types at ' + path)
                unset[path + '.contentType'] = ''
            for key, child in value.items():
                if key not in SYSTEM_ROOT_FIELDS:
                    visit(child, f'{path}.{key}' if path else key,
                          media or key in {'media', 'media_extra', 'source_media'})
    visit(record)
    return unset


def worker(path, shard, db_gate):
    client = migration.storage()
    mongo_client = MongoClient(migration.settings.db_url, serverSelectionTimeoutMS=15000)
    mongo = mongo_client[migration.settings.db_name]
    with db_gate:
        registered = asyncio.run(migration.routes())
    target_prefixes = tuple(f"{route['data_type']}/{route['template_name']}/"
                            for route in registered if route['template_name'] == 'twitter')

    def apply(group):
        db = migration.journal(path)
        try:
            migration.apply_group(db, client, mongo, registered, *group)
        finally:
            db.close()

    def repair(group):
        for route, doc in migration.source_documents(mongo, registered, *group):
            unset = media_alias_updates(doc)
            fields = {f'_meta.{key}': value for key, value in
                      {'data_type': route['data_type'], 'template': route['collection_name']}.items()
                      if doc['_meta'].get(key) != value}
            if not unset and not fields:
                continue
            cleaned = deepcopy(doc)
            for path in unset:
                get_nested_value(cleaned, path.rsplit('.', 1)[0]).pop('contentType')
            normalize_record_fields(cleaned)
            fields.update({'_meta.sync_status': 'pending', '_meta.updated_at': datetime.now(timezone.utc),
                           '_meta.write_token': uuid4().hex})
            guard = {'_id': doc['_id'], '_meta.updated_at': doc['_meta']['updated_at'],
                     '_meta.download_version': doc['_meta'].get('download_version'),
                     '_meta.write_token': doc['_meta'].get('write_token')}
            update = {'$set': fields}
            if unset:
                update['$unset'] = unset
            result = mongo[route['collection_name']].update_one(guard, update)
            if result.matched_count != 1:
                raise RuntimeError('Concurrent source update during Twitter metadata repair')
            print(json.dumps({'worker': shard, 'source_repaired': doc['_meta']['record_id']}), flush=True)

    async def run():
        db = migration.journal(path)
        with db_gate:
            pg = await migration.connect_pg()
        try:
            while True:
                pending = db.execute(f'SELECT DISTINCT prefix,record_id FROM objects WHERE {SCOPE} AND shard=? AND phase="source_updated" LIMIT 100', (shard,)).fetchall()
                for group in pending:
                    repair(group)
                    for attempt in range(3):
                        try:
                            with db_gate:
                                await migration.repair_references(db, mongo, registered, *group)
                                if not await migration.verify_group(db, client, mongo, registered, *group):
                                    raise RuntimeError('Twitter references did not verify: ' + '/'.join(group))
                            break
                        except TimeoutError:
                            print(json.dumps({'worker': shard, 'record': group[1], 'commit_timeout': True,
                                              'attempt': attempt + 1}), flush=True)
                            if attempt == 2:
                                raise
                            await asyncio.sleep(10)
                rows = db.execute(f'SELECT old,new FROM objects WHERE {SCOPE} AND shard=? AND phase="verified" LIMIT ?', (shard, migration.BATCH)).fetchall()
                for old, new in rows:
                    if old == new or not new.startswith(target_prefixes):
                        raise ValueError('Invalid Twitter rename: ' + old)
                    client.stat_object(migration.settings.minio_bucket, new)
                    client.remove_object(migration.settings.minio_bucket, old)
                    db.execute('UPDATE objects SET phase="complete",error=NULL WHERE old=?', (old,))
                    db.commit()
                groups = db.execute(f'SELECT DISTINCT prefix,record_id FROM objects WHERE {SCOPE} AND shard=? AND phase IN ("planned","error") LIMIT 100', (shard,)).fetchall()
                if groups:
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        list(pool.map(apply, groups))
                print(json.dumps({'worker': shard, 'copied_groups': len(groups), 'verified_groups': len(pending), 'old_keys_removed': len(rows)}), flush=True)
                if not pending and not groups and not rows:
                    break
            print(json.dumps({'worker': shard, 'complete': True}), flush=True)
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
    db_gate = ctx.BoundedSemaphore(4)
    processes = [ctx.Process(target=worker, args=(str(args.journal), n, db_gate)) for n in range(migration.WORKERS)]
    for process in processes:
        process.start()
    for process in processes:
        process.join()
    if any(process.exitcode for process in processes):
        raise SystemExit(1)
