"""Checkpointed inspection of stored downloads and targeted re-download repair."""
import argparse
import asyncio
import json
import multiprocessing
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bson.json_util import dumps, loads
from pymongo import MongoClient
from minio.error import S3Error
from app.downloader.adapters.base import ZSCALER_MARKERS
from app.records.download_resources import decode_manifest, encode_manifest
from app.utils.path import get_nested_value
from tools.migrate_download_paths import routes, settings, storage, strings

WORKERS = 6
BATCH = 3000
BLOCK_PAGE = re.compile(rb'<title[^>]*>\s*(?:access denied|request blocked|attention required!\s*\|\s*cloudflare)|the requested url was rejected', re.I)


def invalid_reason(key, content):
    if ZSCALER_MARKERS.search(content):
        return 'Zscaler authentication page'
    html = content.lstrip().lower().startswith((b'<!doctype html', b'<html'))
    if html and BLOCK_PAGE.search(content):
        return 'Access control block page'
    if html and Path(key).suffix.lower() in {'.pdf','.jpg','.jpeg','.png','.gif','.webp','.mp4','.mp3','.zip'}:
        return 'HTML saved as a binary resource'
    return None


def object_key(value):
    if value.startswith(('http://','https://','s3://','minio://')):
        path = urlsplit(value).path
        prefix = '/' + settings.minio_bucket + '/'
        return path[len(prefix):] if path.startswith(prefix) else None
    parts = value.split('/')
    return value if len(parts) >= 3 and all(parts) and not any(c.isspace() for c in value) else None


def inspect(client, key):
    try:
        response = client.get_object(settings.minio_bucket, key, offset=0, length=32768)
    except S3Error as error:
        if error.code == 'NoSuchKey':return 'Stored resource is missing'
        if error.code == 'InvalidRange' and client.stat_object(settings.minio_bucket,key).size == 0:
            return 'Stored resource is empty'
        raise
    try:
        content=response.read()
        return invalid_reason(key, content) if content else 'Stored resource is empty'
    finally:
        response.close(); response.release_conn()


def reset_operation(doc, invalid):
    """Compare the complete download generation, preserving unrelated resources."""
    meta = doc.get('_meta') or {}
    if meta.get('download_status') == 'downloading':
        raise RuntimeError('Active download claim; resume audit after this owner completes')
    completed = decode_manifest(meta.get('downloaded_inputs'))
    affected = {key for key in completed if any(path == key or path == key+'.url' for path in invalid)}
    remaining = {key:value for key,value in completed.items() if key not in affected}
    query = {'_id':doc['_id'], '_meta.download_status':{'$ne':'downloading'}}
    for field in ('download_version','downloaded_inputs','write_token'):
        query['_meta.'+field] = meta[field] if field in meta else {'$exists':False}
    query.update({path:get_nested_value(doc,path) for path in invalid})
    update = {'$set':{'_meta.download_status':'pending', '_meta.sync_status':'pending',
                     '_meta.downloaded_inputs':encode_manifest(remaining),
                     '_meta.updated_at':datetime.now(timezone.utc),
                     '_meta.download_error':'Stored resource rejected: '+ '; '.join(sorted(set(invalid.values())))},
              '$inc':{'_meta.download_version':1},
              '$unset':dict.fromkeys([*invalid,'_meta.download_retry_at','_meta.download_claim_token','_meta.download_claimed_at'],'')}
    return query, update


def verify_repairs(db, mongo, client):
    for collection, identity, paths, state in db.execute("SELECT collection,identity,paths,state FROM repairs WHERE state IN ('planned','reset')"):
        doc = mongo[collection].find_one({'_id':loads(identity)})
        if not doc: raise RuntimeError('Repaired source disappeared: '+collection+'/'+identity)
        previous=json.loads(paths)
        if state=='planned':
            if any(get_nested_value(doc,path)==value for path,value in previous.items()):continue
            db.execute("UPDATE repairs SET state='reset' WHERE collection=? AND identity=?",(collection,identity));db.commit()
        if doc.get('_meta',{}).get('download_status') != 'downloaded': continue
        for path in previous:
            value = get_nested_value(doc,path)
            key = object_key(value) if isinstance(value,str) else None
            if not key: raise RuntimeError('Re-download completed without the expected resource: '+path)
            reason = inspect(client,key)
            if reason: raise RuntimeError('Re-download is still invalid: '+reason)
        db.execute("UPDATE repairs SET state='verified' WHERE collection=? AND identity=?",(collection,identity));db.commit()


def worker(directory, shard, sources):
    path=Path(directory)/f'worker-{shard}.sqlite'
    db=sqlite3.connect(path);db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS progress(collection TEXT PRIMARY KEY,last_id TEXT,done INTEGER,inspected INTEGER)')
    db.execute('CREATE TABLE IF NOT EXISTS repairs(collection TEXT,identity TEXT,paths TEXT,reasons TEXT,state TEXT,PRIMARY KEY(collection,identity))');db.commit()
    client=storage(read_timeout=30);connection=MongoClient(settings.db_url,serverSelectionTimeoutMS=15000)
    mongo=connection[settings.db_name]
    for source in sources:
        name=source['collection_name'];collection=mongo[name]
        while True:
            verify_repairs(db,mongo,client)
            checkpoint=db.execute('SELECT last_id,done,inspected FROM progress WHERE collection=?',(name,)).fetchone()
            if checkpoint and checkpoint[1]:break
            query={'assets':{'$exists':True}}
            if checkpoint and checkpoint[0]:query['_id']={'$gt':loads(checkpoint[0])}
            docs=list(collection.find(query,{'assets':1,'_meta':1}).sort('_id',1).limit(-100).batch_size(100))
            count=checkpoint[2] if checkpoint else 0
            for doc in docs:
                while doc.get('_meta',{}).get('download_status')=='downloading':
                    time.sleep(5)
                    doc=collection.find_one({'_id':doc['_id']})
                    if not doc:raise RuntimeError('Source disappeared while waiting for its download owner')
                invalid={}
                for field,value in strings(doc.get('assets',{})):
                    key=object_key(value)
                    if key:
                        reason=inspect(client,key)
                        if reason:invalid['assets.'+field]=reason
                if invalid:
                    identity=dumps(doc['_id'])
                    db.execute('INSERT OR REPLACE INTO repairs VALUES(?,?,?,?,?)',(name,identity,json.dumps({p:get_nested_value(doc,p) for p in invalid}),json.dumps(invalid),'planned'));db.commit()
                    query,update=reset_operation(doc,invalid)
                    if collection.update_one(query,update).matched_count != 1:
                        raise RuntimeError('Concurrent source/download change; resume from committed checkpoint')
                    db.execute("UPDATE repairs SET state='reset' WHERE collection=? AND identity=?",(name,identity));db.commit()
                    print(json.dumps({'worker':shard,'collection':name,'record_id':doc['_meta'].get('record_id'),'reset_resources':len(invalid)}),flush=True)
                count+=1
                db.execute('INSERT OR REPLACE INTO progress VALUES(?,?,0,?)',(name,dumps(doc['_id']),count));db.commit()
            if not docs:
                db.execute('INSERT OR REPLACE INTO progress VALUES(?,?,1,?)',(name,checkpoint[0] if checkpoint else None,count));db.commit();break
            print(json.dumps({'worker':shard,'collection':name,'inspected_records':count,'repairs':dict(db.execute('SELECT state,count(*) FROM repairs GROUP BY state'))}),flush=True)
    while True:
        verify_repairs(db,mongo,client)
        if not db.execute("SELECT 1 FROM repairs WHERE state IN ('reset','planned') LIMIT 1").fetchone():break
        time.sleep(10)
    connection.close();db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--journal-dir',type=Path,required=True)
    args=parser.parse_args();args.journal_dir.mkdir(parents=True,exist_ok=True)
    registered=sorted(asyncio.run(routes()),key=lambda r:r['collection_name'])
    names=[r['collection_name'] for r in registered]
    if len(names)!=len(set(names)):raise RuntimeError('Duplicate collection routes would overlap workers')
    plan=args.journal_dir/'sources.json'
    if plan.exists():registered=json.loads(plan.read_text())
    else:plan.write_text(json.dumps(registered))
    ctx=multiprocessing.get_context('spawn')
    children=[ctx.Process(target=worker,args=(str(args.journal_dir),n,registered[n::WORKERS])) for n in range(WORKERS)]
    for child in children:child.start()
    while any(child.exitcode is None for child in children):
        if any(child.exitcode not in (None,0) for child in children):
            for child in children:
                if child.is_alive():child.terminate()
            break
        time.sleep(1)
    for child in children:child.join()
    if any(child.exitcode for child in children):raise SystemExit(1)


if __name__=='__main__':main()
