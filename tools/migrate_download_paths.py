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
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import filetype
import urllib3
from minio import Minio
from minio.error import S3Error
from pymongo import MongoClient
from sqlalchemy import text

from app.config.settings import settings
from app.records.collection_routes import collection_route_store
from app.storage.postgres_client import get_pg_client

WORKERS = 6
BATCH = 3000
SOURCE_PAGE_SIZE = 100
HEX = re.compile(r'^[0-9a-f]{64}$')
RECORD = re.compile(r'^[0-9a-f]{32}$')


class ConcurrentSourceUpdate(RuntimeError):
    """The current source is claimed or changed; reread it before retrying."""


def journal(path):
    db = sqlite3.connect(path, timeout=60)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=60000')
    db.execute('CREATE TABLE IF NOT EXISTS objects (old TEXT PRIMARY KEY,prefix TEXT,record_id TEXT,shard INTEGER,new TEXT,phase TEXT NOT NULL DEFAULT "planned",error TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS objects_work ON objects(shard,phase,prefix,record_id)')
    db.execute('CREATE TABLE IF NOT EXISTS progress (prefix TEXT PRIMARY KEY,last_key TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0)')
    db.commit()
    return db


def storage(read_timeout=120):
    address = settings.minio_public_endpoint or settings.minio_endpoint
    url = urlsplit(address if '://' in address else ('https://' if settings.minio_secure else 'http://') + address)
    return Minio(url.netloc, access_key=settings.minio_access_key, secret_key=settings.minio_secret_key, secure=url.scheme == 'https', http_client=urllib3.PoolManager(timeout=urllib3.Timeout(connect=10, read=read_timeout), retries=False))


def strings(value, path=()):
    if isinstance(value, dict):
        if value.get('status_code') == 404 and value.get('url') == '':return
        for key, item in value.items():
            yield from strings(item, (*path, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from strings(item, (*path, str(index)))
    elif isinstance(value, str):
        yield '.'.join(path), value


async def connect_pg():
    """Do not reserve idle database slots while the six workers copy files."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.storage.postgres_client import _resolved_pg_url
    pg=get_pg_client()
    if pg._engine is None:
        pg._engine=create_async_engine(_resolved_pg_url(),poolclass=NullPool,
            connect_args={'timeout':10,'command_timeout':30,'prepared_statement_cache_size':0})
        pg._session_factory=async_sessionmaker(pg._engine,expire_on_commit=False)
        pg._connected=True
    async with pg._engine.connect() as connection:
        await connection.execute(text('SELECT 1'))
    return pg


async def routes():
    pg = await connect_pg()
    try:
        return await collection_route_store.list_by_data_type(None)
    finally:
        await pg.close()


def inventory(path):
    # Source-owned assets avoid an expensive bucket-wide object listing and retain live-reference scope.
    from bson.json_util import dumps, loads
    db = journal(path)
    registered = asyncio.run(routes())
    registered.sort(key=lambda r: (r['template_name'] != 'twitter', r['data_type'] in {'patent','filing'}, r['collection_name']))
    client = MongoClient(settings.db_url, serverSelectionTimeoutMS=15000)
    mongo = client[settings.db_name]
    snapshot = db.execute('SELECT last_key FROM progress WHERE prefix="__snapshot__"').fetchone()
    if not snapshot:
        db.execute('INSERT INTO progress VALUES(?,?,1)', ('__snapshot__',dumps(datetime.now(timezone.utc))));db.commit()
    for route in registered:
        collection = mongo[route['collection_name']]
        keys = [list(info['key']) for info in collection.index_information().values()]
        compound = [('_meta.download_status',1),('_meta.updated_at',1),('_id',1)]
        # Sources without download state indexes have no downloader lifecycle to migrate.
        if compound not in keys:
            if collection.find_one({'assets':{'$exists':True}}, {'_id':1}, max_time_ms=30000):
                raise RuntimeError('Source with assets lacks its required download index: '+route['collection_name'])
            continue
        for status in ('downloaded','downloading','pending'):
            name = 'mongo:'+route['collection_name']+':'+status
            checkpoint = db.execute('SELECT last_key,done FROM progress WHERE prefix=?',(name,)).fetchone()
            if checkpoint and checkpoint[1]:continue
            last = loads(checkpoint[0]) if checkpoint and checkpoint[0] else None
            while True:
                query = {'_meta.download_status':status}
                if last:
                    query['_meta.updated_at']={'$gte':last[0]}
                    query['$or'] = [{'_meta.updated_at':{'$gt':last[0]}},{'_meta.updated_at':last[0],'_id':{'$gt':last[1]}}]
                # Negative limit closes the server cursor after one response. A short
                # batch resumes from its last key instead of issuing getMore.
                docs = list(collection.find(query, {'assets':1,'_meta.record_id':1,'_meta.updated_at':1}).sort([('_meta.updated_at',1),('_id',1)]).hint(compound).batch_size(SOURCE_PAGE_SIZE).limit(-SOURCE_PAGE_SIZE).max_time_ms(120000))
                if not docs:
                    db.execute('INSERT OR REPLACE INTO progress VALUES(?,?,1)',(name,dumps(last) if last else ''));db.commit();break
                pending = []
                for doc in docs:
                    for _,value in strings(doc.get('assets',{})):
                        if value.startswith(('http://','https://','s3://','minio://')):
                            url=urlsplit(value)
                            if url.path.startswith('/'+settings.minio_bucket+'/'):
                                value=url.path[len(settings.minio_bucket)+2:]
                            else:continue
                        parts=value.split('/')
                        if len(parts)<4 or not RECORD.fullmatch(parts[2]):
                            if value:raise ValueError('Unsupported resource reference: '+value)
                            continue
                        if parts[2]!=doc['_meta']['record_id']:raise ValueError('Resource owned by another record: '+value)
                        canonical=(len(parts)==4 and HEX.fullmatch(Path(parts[-1]).stem)
                                   and '/'.join(parts[:2])==f"{route['data_type']}/{route['template_name']}")
                        pending.append((value,'/'.join(parts[:2]),parts[2],int(parts[2],16)%WORKERS,value if canonical else None,'canonical' if canonical else 'planned'))
                        if len(pending)==BATCH:
                            db.executemany('INSERT OR IGNORE INTO objects(old,prefix,record_id,shard,new,phase) VALUES(?,?,?,?,?,?)',pending);db.commit();pending.clear()
                if pending:db.executemany('INSERT OR IGNORE INTO objects(old,prefix,record_id,shard,new,phase) VALUES(?,?,?,?,?,?)',pending)
                last=(docs[-1]['_meta']['updated_at'],docs[-1]['_id'])
                db.execute('INSERT OR REPLACE INTO progress VALUES(?,?,0)',(name,dumps(last)));db.commit()
                print(json.dumps({'inventory':name,'records':len(docs)}),flush=True)
    db.execute('INSERT OR REPLACE INTO progress VALUES("__inventory__","",1)');db.commit()
    client.close();db.close()


def copy_object(client, old, prefix, record_id):
    info = client.stat_object(settings.minio_bucket, old)
    first, calculated, size = b'', hashlib.sha256(), 0
    with TemporaryDirectory(prefix='spider-key-migration-') as directory:
        payload = Path(directory) / 'payload'
        response = client.get_object(settings.minio_bucket, old)
        try:
            with payload.open('wb') as stream:
                for chunk in response.stream(1024 * 1024):
                    stream.write(chunk)
                    size += len(chunk)
                    calculated.update(chunk)
                    if len(first) < 32768:
                        first += chunk[:32768 - len(first)]
        finally:
            response.close();response.release_conn()
        if size != info.size:
            raise ValueError('Source object size verification failed: ' + old)
        digest = calculated.hexdigest()
        parts = old.split('/')
        if (len(parts) == 6 and HEX.fullmatch(parts[-2]) and HEX.fullmatch(parts[-3])
                and digest != parts[-2]):
            raise ValueError('Source object hash verification failed: ' + old)
        detected = filetype.guess(first)
        extension = '.' + detected.extension if detected else Path(old).suffix
        new = f'{prefix}/{record_id}/{digest}{extension}'
        if new == old:
            return new
        try:
            client.fput_object(settings.minio_bucket, new, str(payload),
                               content_type=detected.mime if detected else info.content_type)
        except urllib3.exceptions.TimeoutError as error:
            # Upload completion can precede a lost response; verify the destination.
            try:
                client.stat_object(settings.minio_bucket, new)
            except S3Error as missing:
                if missing.code in {'NoSuchKey', 'NoSuchObject'}:
                    raise error from missing
                raise
            print(json.dumps({'upload_response_timeout': old, 'checking_destination': new}), flush=True)
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
    if any((doc.get('_meta') or {}).get('download_status') == 'downloading' for _, doc in docs):
        raise ConcurrentSourceUpdate('Source is currently claimed by the downloader')
    replacements = {}
    for old, new in rows:
        if new and any(new in value for _, doc in docs for _, value in strings(doc.get('assets', {}))):
            replacements[old] = new;continue
        if not any(old in value for _, doc in docs for _, value in strings(doc.get('assets', {}))):
            # Unreferenced objects are explicitly recorded and left untouched.
            db.execute('UPDATE objects SET phase="unreferenced",error=NULL WHERE old=?', (old,));db.commit();continue
        owners = [route for route, doc in docs
                  if any(old in value for _, value in strings(doc.get('assets', {})))]
        targets = {f"{route['data_type']}/{route['template_name']}" for route in owners}
        if len(targets) != 1:
            raise ValueError('Resource must have exactly one source storage prefix: ' + old)
        new = new or copy_object(client,old,targets.pop(),record_id)
        replacements[old] = new
        db.execute('UPDATE objects SET new=?,error=NULL WHERE old=?',(new,old));db.commit()
    for route, doc in docs:
        fields, conditions = {}, {'_id':doc['_id'],'_meta.download_status':{'$ne':'downloading'}}
        for field, value in strings({k:v for k,v in doc.items() if k not in {'_meta','_id'}}):
            changed = value
            for old,new in sorted(replacements.items(), key=lambda pair: len(pair[0]), reverse=True):
                changed = changed.replace(old,new)
            if changed != value:
                fields[field] = changed;conditions[field] = value
        if fields:
            fields.update({'_meta.sync_status':'pending','_meta.updated_at':datetime.now(timezone.utc)})
            result = mongo[route['collection_name']].update_one(conditions,{'$set':fields,'$inc':{'_meta.download_version':1}})
            if result.matched_count != 1:
                raise ConcurrentSourceUpdate('Concurrent source update; reread the latest record')
    for old in replacements:
        db.execute('UPDATE objects SET phase="source_updated" WHERE old=?',(old,))
    db.commit()


def declared_normalizer(module, kind, source):
    from importlib import import_module
    from app.etl.normalizers import _NORMALIZER_MODULES, _NORMALIZER_REGISTRY
    normalizer=getattr(module,'NORMALIZERS',{}).get(kind) if module else None
    if normalizer is None:
        if kind in _NORMALIZER_MODULES:import_module('app.etl.normalizers.'+_NORMALIZER_MODULES[kind])
        normalizer=_NORMALIZER_REGISTRY.get(kind+':'+source) or _NORMALIZER_REGISTRY.get(kind+':*')
    if normalizer is None:raise RuntimeError('No declared normalizer for '+source+'/'+kind)
    return normalizer


async def repair_references(db, mongo, registered, prefix, record_id):
    """Repair current database references explicitly, including sources with paused CDC topics."""
    from app.adapters.runtime import get_source_module
    from app.etl.schema.catalogs import validate_table_name
    from app.etl.source_fences import business_version
    rows=db.execute('SELECT old,new FROM objects WHERE prefix=? AND record_id=? AND phase="source_updated"',(prefix,record_id)).fetchall()
    if not rows:return
    pg=get_pg_client()
    docs=source_documents(mongo,registered,prefix,record_id)
    if not docs:raise RuntimeError('Source disappeared before reference repair')
    for route,doc in docs:
        source=route['collection_name'];kind=validate_table_name(route['data_type'])
        body={k:v for k,v in doc.items() if k!='_id'}
        module=await get_source_module(source,export='NORMALIZERS')
        normalizer=declared_normalizer(module,kind,source)
        outputs=normalizer({**body,'data_source':source})
        if inspect.isawaitable(outputs):outputs=await outputs
        if not isinstance(outputs,list):outputs=[outputs]
        updates=[]
        for output in outputs:
            fields={key:value for key,value in output.items()
                    if key not in {'record_id','data_source','data_type'} and any(new in json.dumps(value,default=str) for _,new in rows)}
            if fields:updates.append((validate_table_name(output.get('data_type') or kind),output.get('record_id') or record_id,fields))
        async with pg.session() as session:
            await session.execute(text("SET LOCAL lock_timeout='5s'"))
            await session.execute(text("SET LOCAL statement_timeout='30s'"))
            current=(await session.execute(text(f'SELECT raw_data FROM ts_rds.rds_{kind} WHERE record_id=:id AND data_source=:src AND data_type=:kind FOR UPDATE'),{'id':record_id,'src':source,'kind':kind})).mappings().first()
            if not current:raise RuntimeError('Source has no current RDS row: '+source+'/'+record_id)
            raw=current['raw_data']
            if isinstance(raw,str):raw=json.loads(raw)
            if business_version(raw.get('_meta') or {})>business_version(doc['_meta']):
                raise RuntimeError('RDS advanced during migration; resume with the latest source')
            # Keep real delivery positions; the source snapshot and revision come from Mongo.
            if '_kafka_meta' in raw:body['_kafka_meta']=raw['_kafka_meta']
            await session.execute(text(f'UPDATE ts_rds.rds_{kind} SET raw_data=CAST(:raw AS jsonb),updated_at=NOW() WHERE record_id=:id AND data_source=:src AND data_type=:kind'),{'raw':json.dumps(body,ensure_ascii=False,default=str),'id':record_id,'src':source,'kind':kind})
            for table,identity,fields in sorted(updates,key=lambda row:(row[0],row[1])):
                columns=(await session.execute(text("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='ts_ods' AND table_name=:table"),{'table':'ods_'+table})).mappings().all()
                types={row['column_name']:row['data_type'] for row in columns}
                parameters={'id':identity,'src':source,'kind':table};assignments=[]
                for index,(name,value) in enumerate(fields.items()):
                    if name not in types:raise RuntimeError('Missing ODS path column: '+table+'.'+name)
                    parameter='value_'+str(index);quoted='"'+name.replace('"','""')+'"'
                    if types[name] in {'json','jsonb'}:
                        parameters[parameter]=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,default=str)
                        assignments.append(quoted+'=CAST(:'+parameter+' AS '+types[name]+')')
                    elif types[name] in {'text','character varying'}:
                        parameters[parameter]=value;assignments.append(quoted+'=:'+parameter)
                    else:raise RuntimeError('Unexpected storage reference column type: '+table+'.'+name)
                result=await session.execute(text(f'UPDATE ts_ods.ods_{table} SET '+','.join(assignments)+',updated_at=NOW() WHERE record_id=:id AND data_source=:src AND data_type=:kind'),parameters)
                if result.rowcount!=1:raise RuntimeError('Expected one existing ODS row: '+table+'/'+identity)


def resource_references_match(expected, actual, paths):
    """Match resource-bearing leaves, ignoring unrelated delivery metadata."""
    if isinstance(expected, str) and isinstance(actual, (dict, list)) and expected[:1] in {'{', '['}:
        expected = json.loads(expected)
    if isinstance(expected, dict):
        return all(resource_references_match(value, actual.get(key) if isinstance(actual, dict) else None, paths)
                   for key, value in expected.items())
    if isinstance(expected, list):
        return all(resource_references_match(value, actual[index] if isinstance(actual, list) and index < len(actual) else None, paths)
                   for index, value in enumerate(expected))
    return not (isinstance(expected, str) and any(path in expected for path in paths)) or expected == actual


async def verify_group(db, client, mongo, registered, prefix, record_id):
    from app.adapters.runtime import get_source_module
    from app.etl.schema.catalogs import validate_table_name
    from app.etl.source_fences import business_version
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
        snapshot = raw[0]['raw_data']
        if isinstance(snapshot, str):snapshot=json.loads(snapshot)
        source_meta, stored_meta = doc.get('_meta') or {}, snapshot.get('_meta') or {}
        if (business_version(source_meta) != business_version(stored_meta)
                or source_meta.get('write_token') != stored_meta.get('write_token')
                or stored_meta.get('data_type') != kind
                or stored_meta.get('template') != route['collection_name']):
            return False
        raw_json=json.dumps(raw[0]['raw_data'],default=str)
        current_json=json.dumps(doc,default=str)
        if any(old in current_json or old in raw_json or (new in current_json and new not in raw_json) for old,new in rows):
            return False
        module=await get_source_module(source,export='NORMALIZERS')
        normalizer=declared_normalizer(module,kind,source)
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
            if not found or not resource_references_match(output, found[0]['row'], relevant):
                return False
    for old,new in rows:
        client.stat_object(settings.minio_bucket,new)
        db.execute('UPDATE objects SET phase="verified",error=NULL WHERE old=?',(old,))
    db.commit();return True


def worker(path, shard, mode, db_gate):
    db,client=journal(path),storage()
    with db_gate:registered=asyncio.run(routes())
    mongo_client=MongoClient(settings.db_url,serverSelectionTimeoutMS=15000)
    mongo=mongo_client[settings.db_name]
    phase='planned' if mode=='apply' else 'source_updated' if mode=='verify' else 'verified'
    db.execute('UPDATE objects SET error=NULL WHERE shard=? AND phase=?',(shard,phase))
    if mode=='apply':db.execute('UPDATE objects SET error=NULL WHERE shard=? AND phase="source_updated"',(shard,))
    db.commit()
    async def run():
        pg=get_pg_client()
        if mode in {'apply','verify'}:
            with db_gate:await connect_pg()
        try:
            phases=('planned','error','source_updated') if mode=='apply' else ('source_updated',) if mode=='verify' else ('verified',)
            last=('', '')
            while True:
                groups=db.execute('SELECT DISTINCT prefix,record_id FROM objects WHERE shard=? AND phase IN ('+','.join('?' for _ in phases)+') AND error IS NULL AND (prefix,record_id)>(?,?) ORDER BY prefix,record_id LIMIT ?', (shard,*phases,*last,BATCH)).fetchall()
                if not groups:
                    if mode=='apply' and last!=('', ''):
                        last=('', '');continue
                    finished=db.execute('SELECT done FROM progress WHERE prefix="__inventory__"').fetchone()
                    if mode=='apply' and (not finished or not finished[0]):
                        import time
                        time.sleep(2);last=('', '');continue
                    break
                for prefix,record_id in groups:
                    assert int(record_id,16)%WORKERS==shard
                    try:
                        if mode=='apply':
                            apply_group(db,client,mongo,registered,prefix,record_id)
                            with db_gate:
                                await repair_references(db,mongo,registered,prefix,record_id)
                                await verify_group(db,client,mongo,registered,prefix,record_id)
                        elif mode=='verify':
                            with db_gate:await verify_group(db,client,mongo,registered,prefix,record_id)
                        else:
                            for old,new in db.execute('SELECT old,new FROM objects WHERE prefix=? AND record_id=? AND phase="verified"',(prefix,record_id)).fetchall():
                                client.stat_object(settings.minio_bucket,new)
                                client.remove_object(settings.minio_bucket,old)
                                db.execute('UPDATE objects SET phase="complete",error=NULL WHERE old=?',(old,));db.commit()
                            db.commit()
                    except Exception as error:
                        db.rollback()
                        db.execute('UPDATE objects SET error=? WHERE prefix=? AND record_id=? AND phase NOT IN ("verified","canonical","unreferenced")',(str(error)[:1000],prefix,record_id));db.commit()
                        print(json.dumps({'shard':shard,'prefix':prefix,'record_id':record_id,'error':str(error)[:500]}),flush=True)
                last=groups[-1]
                print(json.dumps({'shard':shard,'mode':mode,'checkpoint':last}),flush=True)
        finally:
            if mode in {'apply','verify'}:await pg.close()
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
        db_gate=ctx.BoundedSemaphore(1)
        processes=[ctx.Process(target=worker,args=(str(args.journal),n,args.mode,db_gate)) for n in range(WORKERS)]
        for process in processes:process.start()
        for process in processes:process.join()
        if any(p.exitcode for p in processes):raise SystemExit(1)
    db=journal(args.journal)
    print(json.dumps({'objects':dict(db.execute('SELECT phase,count(*) FROM objects GROUP BY phase')),'errors':db.execute('SELECT count(*) FROM objects WHERE error IS NOT NULL').fetchone()[0],'inventory':list(db.execute('SELECT * FROM progress'))}),flush=True)
    errors=db.execute('SELECT count(*) FROM objects WHERE error IS NOT NULL').fetchone()[0]
    db.close()
    if errors:raise SystemExit(2)


if __name__=='__main__':main()
