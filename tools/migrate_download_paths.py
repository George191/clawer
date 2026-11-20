"""Journalled MinIO resource-path migration. Run with six OS workers, at most 3,000 rows per batch."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import multiprocessing
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import filetype
import urllib3
from minio import Minio
from minio.commonconfig import CopySource
from pymongo import MongoClient
from sqlalchemy import text

from app.config.settings import settings
from app.records.collection_routes import collection_route_store
from app.storage.postgres_client import get_pg_client

WORKERS = 6
BATCH = 3000
HEX = re.compile(r'^[0-9a-f]{64}$')
RECORD = re.compile(r'^[0-9a-f]{32}$')


def journal(path):
    db = sqlite3.connect(path, timeout=60)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=60000')
    db.execute('CREATE TABLE IF NOT EXISTS objects (old TEXT PRIMARY KEY,prefix TEXT,record_id TEXT,shard INTEGER,new TEXT,phase TEXT NOT NULL DEFAULT "planned",error TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS objects_work ON objects(shard,phase,prefix,record_id)')
    db.execute('CREATE TABLE IF NOT EXISTS progress (prefix TEXT PRIMARY KEY,last_key TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0)')
    db.commit()
    return db


def storage():
    address = settings.minio_public_endpoint or settings.minio_endpoint
    url = urlsplit(address if '://' in address else ('https://' if settings.minio_secure else 'http://') + address)
    return Minio(url.netloc, access_key=settings.minio_access_key, secret_key=settings.minio_secret_key, secure=url.scheme == 'https', http_client=urllib3.PoolManager(timeout=15, retries=False))


def strings(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from strings(item, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from strings(item, (*path, str(index)))
    elif isinstance(value, str):
        yield '.'.join(path), value


async def routes():
    pg = get_pg_client()
    await pg.connect(max_retries=1)
    try:
        return await collection_route_store.list_by_data_type(None)
    finally:
        await pg.close()


def inventory(path):
    db, client = journal(path), storage()
    registered = asyncio.run(routes())
    types = {r['data_type'] for r in registered} | {'social_media', 'financial_report', 'twitter'}
    prefixes = [o.object_name for o in client.list_objects(settings.minio_bucket, recursive=False) if o.object_name.rstrip('/') in types]
    for prefix in prefixes:
        checkpoint = db.execute('SELECT last_key,done FROM progress WHERE prefix=?', (prefix,)).fetchone()
        if checkpoint and checkpoint[1]:
            continue
        pending = []
        for obj in client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True, start_after=checkpoint[0] if checkpoint else None):
            parts = obj.object_name.split('/')
            if len(parts) < 4 or not RECORD.fullmatch(parts[2]):
                raise ValueError('Unsupported resource layout: ' + obj.object_name)
            canonical = len(parts) == 4 and HEX.fullmatch(Path(parts[-1]).stem)
            pending.append((obj.object_name, '/'.join(parts[:2]), parts[2], int(parts[2], 16) % WORKERS, obj.object_name if canonical else None, 'canonical' if canonical else 'planned'))
            if len(pending) == BATCH:
                db.executemany('INSERT OR IGNORE INTO objects(old,prefix,record_id,shard,new,phase) VALUES(?,?,?,?,?,?)', pending)
                db.execute('INSERT OR REPLACE INTO progress VALUES(?,?,0)', (prefix, obj.object_name))
                db.commit();pending.clear()
                print(json.dumps({'inventory':prefix,'last':obj.object_name}), flush=True)
        if pending:
            db.executemany('INSERT OR IGNORE INTO objects(old,prefix,record_id,shard,new,phase) VALUES(?,?,?,?,?,?)', pending)
        db.execute('INSERT OR REPLACE INTO progress VALUES(?,?,1)', (prefix, pending[-1][0] if pending else checkpoint[0] if checkpoint else ''))
        db.commit()
    db.close()


def copy_object(client, old, prefix, record_id):
    info = client.stat_object(settings.minio_bucket, old)
    parts = old.split('/')
    digest = parts[-2] if len(parts) == 6 and HEX.fullmatch(parts[-2]) and HEX.fullmatch(parts[-3]) else None
    response = client.get_object(settings.minio_bucket, old, length=32768 if digest else 0)
    first, calculated = b'', hashlib.sha256()
    try:
        for chunk in response.stream(1024 * 1024):
            if len(first) < 32768:
                first += chunk[:32768 - len(first)]
            if digest is None:
                calculated.update(chunk)
    finally:
        response.close();response.release_conn()
    digest = digest or calculated.hexdigest()
    detected = filetype.guess(first)
    extension = '.' + detected.extension if detected else Path(old).suffix
    new = f'{prefix}/{record_id}/{digest}{extension}'
    if new == old:
        return new
    client.copy_object(settings.minio_bucket, new, CopySource(settings.minio_bucket, old))
    after = client.stat_object(settings.minio_bucket, new)
    if after.size != info.size or after.etag != info.etag:
        response = client.get_object(settings.minio_bucket, new)
        checked = hashlib.sha256()
        try:
            for chunk in response.stream(1024 * 1024):
                checked.update(chunk)
        finally:
            response.close();response.release_conn()
        if after.size != info.size or checked.hexdigest() != digest:
            raise ValueError('Published object verification failed: ' + new)
    return new


def source_documents(mongo, registered, prefix, record_id):
    template = prefix.split('/')[1]
    candidates = [r for r in registered if r['template_name'] == template]
    if not candidates:
        raise ValueError('No registered source owns ' + prefix)
    return [(r, doc) for r in candidates if (doc := mongo[r['collection_name']].find_one({'_meta.record_id':record_id}))]


def apply_group(db, client, mongo, registered, prefix, record_id):
    rows = db.execute('SELECT old,new FROM objects WHERE prefix=? AND record_id=? AND phase IN ("planned","error")', (prefix,record_id)).fetchall()
    docs = source_documents(mongo, registered, prefix, record_id)
    replacements = {}
    for old, new in rows:
        if new and any(new in value for _, doc in docs for _, value in strings(doc.get('assets', {}))):
            replacements[old] = new;continue
        if not any(old in value for _, doc in docs for _, value in strings(doc.get('assets', {}))):
            # Unreferenced objects are explicitly recorded and left untouched.
            db.execute('UPDATE objects SET phase="unreferenced",error=NULL WHERE old=?', (old,));continue
        new = new or copy_object(client,old,prefix,record_id)
        replacements[old] = new
        db.execute('UPDATE objects SET new=?,error=NULL WHERE old=?',(new,old));db.commit()
    for route, doc in docs:
        fields, conditions = {}, {'_id':doc['_id'],'_meta.download_status':{'$ne':'downloading'}}
        for field, value in strings({k:v for k,v in doc.items() if k not in {'_meta','_id'}}):
            changed = value
            for old,new in replacements.items():
                changed = changed.replace(old,new)
            if changed != value:
                fields[field] = changed;conditions[field] = value
        if fields:
            fields.update({'_meta.sync_status':'pending','_meta.updated_at':datetime.now(timezone.utc)})
            result = mongo[route['collection_name']].update_one(conditions,{'$set':fields,'$inc':{'_meta.download_version':1}})
            if result.matched_count != 1:
                raise RuntimeError('Concurrent source update; resume after the download finishes')
    for old in replacements:
        db.execute('UPDATE objects SET phase="source_updated" WHERE old=?',(old,))
    db.commit()


async def verify_group(db, client, mongo, registered, prefix, record_id):
    from app.adapters.runtime import get_source_module
    from importlib import import_module
    from app.etl.normalizers import _NORMALIZER_MODULES, _NORMALIZER_REGISTRY
    from app.etl.schema.catalogs import validate_table_name
    rows=db.execute('SELECT old,new FROM objects WHERE prefix=? AND record_id=? AND phase="source_updated"',(prefix,record_id)).fetchall()
    docs=source_documents(mongo,registered,prefix,record_id)
    pg=get_pg_client()
    if not docs:
        raise RuntimeError('Source document disappeared; keep old object for review')
    for route, doc in docs:
        source=route['collection_name'];kind=validate_table_name(route['data_type'])
        raw=await pg.fetch_all(f'SELECT raw_data FROM ts_rds.rds_{kind} WHERE record_id=:id AND data_source=:source AND data_type=:kind', {'id':record_id,'source':source,'kind':kind})
        if not raw:
            return False
        raw_json=json.dumps(raw[0]['raw_data'],default=str)
        current_json=json.dumps(doc,default=str)
        if any(old in current_json or old in raw_json or (new in current_json and new not in raw_json) for old,new in rows):
            return False
        module=await get_source_module(source,export='NORMALIZERS')
        normalizer=getattr(module,'NORMALIZERS',{}).get(kind) if module else None
        if normalizer is None:
            if kind in _NORMALIZER_MODULES:import_module('app.etl.normalizers.'+_NORMALIZER_MODULES[kind])
            normalizer=_NORMALIZER_REGISTRY.get(kind+':'+source) or _NORMALIZER_REGISTRY.get(kind+':*')
        if normalizer is None:raise RuntimeError('No declared normalizer for '+source+'/'+kind)
        normalized=normalizer({**doc,'data_source':source})
        if inspect.isawaitable(normalized):
            normalized=await normalized
        outputs=normalized if isinstance(normalized,list) else [normalized]
        for output in outputs:
            expected=json.dumps(output,default=str)
            relevant=[new for _,new in rows if new in expected]
            if not relevant:
                continue
            table=validate_table_name(output.get('data_type') or kind)
            found=await pg.fetch_all(f'SELECT to_jsonb(t) AS row FROM ts_ods.ods_{table} t WHERE record_id=:id AND data_source=:source AND data_type=:kind', {'id':output.get('record_id') or record_id,'source':source,'kind':table})
            if not found or any(new not in json.dumps(found[0]['row'],default=str) for new in relevant):
                return False
    for old,new in rows:
        client.stat_object(settings.minio_bucket,new)
        db.execute('UPDATE objects SET phase="verified",error=NULL WHERE old=?',(old,))
    db.commit();return True


def worker(path, shard, mode):
    db,client=journal(path),storage()
    registered=asyncio.run(routes())
    mongo_client=MongoClient(settings.db_url,serverSelectionTimeoutMS=15000)
    mongo=mongo_client[settings.db_name]
    async def run():
        pg=get_pg_client()
        if mode=='verify':
            await pg.connect(max_retries=1)
        try:
            phases=('planned','error') if mode=='apply' else ('source_updated',) if mode=='verify' else ('verified',)
            last=('', '')
            while True:
                groups=db.execute('SELECT DISTINCT prefix,record_id FROM objects WHERE shard=? AND phase IN ('+','.join('?' for _ in phases)+') AND (prefix,record_id)>(?,?) ORDER BY prefix,record_id LIMIT ?', (shard,*phases,*last,BATCH)).fetchall()
                if not groups:
                    break
                for prefix,record_id in groups:
                    assert int(record_id,16)%WORKERS==shard
                    try:
                        if mode=='apply':apply_group(db,client,mongo,registered,prefix,record_id)
                        elif mode=='verify':await verify_group(db,client,mongo,registered,prefix,record_id)
                        else:
                            for old,new in db.execute('SELECT old,new FROM objects WHERE prefix=? AND record_id=? AND phase="verified"',(prefix,record_id)).fetchall():
                                client.stat_object(settings.minio_bucket,new)
                                client.remove_object(settings.minio_bucket,old)
                                db.execute('UPDATE objects SET phase="complete",error=NULL WHERE old=?',(old,))
                            db.commit()
                    except Exception as error:
                        db.rollback()
                        db.execute('UPDATE objects SET error=? WHERE prefix=? AND record_id=? AND phase NOT IN ("verified","canonical","unreferenced")',(str(error)[:1000],prefix,record_id));db.commit()
                        print(json.dumps({'shard':shard,'prefix':prefix,'record_id':record_id,'error':str(error)[:500]}),flush=True)
                last=groups[-1]
                print(json.dumps({'shard':shard,'mode':mode,'checkpoint':last}),flush=True)
        finally:
            if mode=='verify':await pg.close()
    asyncio.run(run());mongo_client.close();db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['inventory','apply','verify','clean','status'])
    parser.add_argument('--journal',type=Path,required=True)
    args=parser.parse_args();args.journal.parent.mkdir(parents=True,exist_ok=True)
    if args.mode=='inventory':inventory(args.journal)
    elif args.mode in {'apply','verify','clean'}:
        # Immutable shard ownership and a durable shared journal make restarts safe.
        ctx=multiprocessing.get_context('spawn')
        processes=[ctx.Process(target=worker,args=(str(args.journal),n,args.mode)) for n in range(WORKERS)]
        for process in processes:process.start()
        for process in processes:process.join()
        if any(p.exitcode for p in processes):raise SystemExit(1)
    db=journal(args.journal)
    print(json.dumps({'objects':dict(db.execute('SELECT phase,count(*) FROM objects GROUP BY phase')),'errors':db.execute('SELECT count(*) FROM objects WHERE error IS NOT NULL').fetchone()[0],'inventory':list(db.execute('SELECT * FROM progress'))}),flush=True)
    errors=db.execute('SELECT count(*) FROM objects WHERE error IS NOT NULL').fetchone()[0]
    db.close()
    if errors:raise SystemExit(2)


if __name__=='__main__':main()
