"""Rebuild approved facts ID evidence inside the source server; print summaries only."""
from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

BASE = Path('/data/clawer/output/sec_migration_20261104')
BASELINE = BASE / 'facts-direct-baseline-20261106T061813Z'
NAMING = BASE / 'facts-cdc-wave-20261106T061106Z'
OUTPUT = BASE / 'facts-burst-evidence-20261116'
PROTOCOL = 'facts_burst_checkpoint_manifest_v1'
UUID = '1413753fb501422a881376e08c7ae8a6'
PLAN_SHA256 = '2c035d2c0733732483c2c2bb835a6f7be93d1e8cf96379661856c7f40dcea55b'
TOTAL = 78600
# Approved boundaries/counts and hashes only. No business record IDs are deployed.
APPROVED = (
    ('6aca1416e41cb87606f256cc', '6aca1422e41cb87606f28f0b', 14400,
     '6bd54ddeb5fb058e22fbc95a44c078702c1bd87d9393f710a468f93d7b3905c5',
     '70046b1c0c24d2bd72d44f004c83e76c51861fdc0869a6142b8a3e4126af1e52'),
    ('6aceccef36338c3040af2ae7', '6acecdd4e41cb8760664872d', 11700,
     'd86aecb3413d3a087bc2cb51a86bc7bab2ae339da550c76ecb2814a7a5034e69',
     '6a354b30233b64ef5213845880f34036538766360533c1e76752fd6696dbc8ad'),
    ('6ad385c7884a5fea7a6bff02', '6ad591f6d0a5aed361c648c3', 14400,
     '160cbaf4fce11031b09c813c027e643ab29cbd1d9d18fb6f703c047adf21f3ca',
     '51709c14f6c6368c2cd2dc78db96f0c17c1e9f3b630ab0355a09e7788b7abce7'),
    ('6ad83e9fda6133a4b428d31e', '6ad90350d0a5aed361238068', 11700,
     '25c3406a09489c4700490740fc23b8901e04d1d7c4b75122ba8699d37bfc260e',
     '9e467c362fa6db87d36779d120a6e74ae8abba4fe89fcc716be65a4aff5810fc'),
    ('6adcf7782c78075eede5a739', '6adcfb2fd0a5aed3618c16cb', 12900,
     '8358b2536d5eb986db4587558e6cbd5f31004c6ed7eea637011099f4cd1749f5',
     'e9696e9516a96dc06256cff9ad0924b91c2fea2b0ad5341877433875970d8975'),
    ('6ae1b0507e8edb1927a27b54', '6ae23b88d0a5aed361f7b545', 13500,
     'd73c57ab2b2bba9303711a461d5a7c195c261e6f7cb004c33060f0607666be56',
     '2e4b6884bc433073bc836ca5cd8e17354153401ccdc2e67b41b3525cfbed875d'),
)

WORKER = r'''
import json,sys,time
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from app.config.settings import settings

def read_rows(collection, part):
    lower, upper = ObjectId(part['lower']), ObjectId(part['upper_inclusive'])
    cursor = None
    count = 0
    while True:
        bounds = {'$gte': lower, '$lte': upper} if cursor is None else {'$gt': cursor, '$lte': upper}
        for attempt in range(3):
            try:
                docs = list(collection.find({'_id': bounds}, {'_id': 1, '_meta.record_id': 1})
                            .sort('_id', 1).hint('_id_').limit(3000).batch_size(3000).max_time_ms(30000))
                break
            except PyMongoError:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
        if not docs:
            break
        for doc in docs:
            oid, rid = doc['_id'], (doc.get('_meta') or {}).get('record_id')
            if (not lower <= oid <= upper or (cursor is not None and oid <= cursor)
                    or not isinstance(rid, str) or not rid.strip() or '\n' in rid or '\r' in rid):
                raise RuntimeError('Invalid bounded facts identity')
            count += 1
            if count > part['processed']:
                raise RuntimeError('Facts count exceeds approved prefix')
            yield {'mongo_id': str(oid), 'record_id': rid}
            cursor = oid
    if count != part['processed'] or str(cursor) != part['upper_inclusive']:
        raise RuntimeError('Facts count or endpoint differs from approved prefix')

payload = json.loads(sys.stdin.read())
client = MongoClient(settings.db_url, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000,
                     socketTimeoutMS=60000, retryReads=False, retryWrites=False,
                     appname='facts-burst-server-evidence-' + str(payload['part']['worker']))
try:
    db = client[settings.db_name]
    namespaces = list(db.list_collections(filter={'name': 'financial_fact_sec_edgar'}, maxTimeMS=10000))
    if len(namespaces) != 1 or bytes(namespaces[0]['info']['uuid']).hex() != payload['mongo_uuid_hex']:
        raise RuntimeError('Facts namespace UUID differs')
    collection = db['financial_fact_sec_edgar']
    index = collection.index_information().get('_meta.record_id_1', {})
    if index.get('unique') is not True or index.get('key') != [('_meta.record_id', 1)]:
        raise RuntimeError('Facts record ID index differs')
    for row in read_rows(collection, payload['part']):
        sys.stdout.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
finally:
    client.close()
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def need(value, message):
    if not value:
        raise RuntimeError('Facts burst evidence: ' + message)


def snapshot():
    paths = [BASELINE / 'plan.json', BASELINE / 'state.json', BASELINE / 'STOP', NAMING / 'facts-cdc-state.json']
    paths += [BASELINE / f'worker-{number}.json' for number in range(6)]
    raw = {path: path.read_bytes() for path in paths}
    plan = json.loads(raw[paths[0]])
    need(sha(raw[paths[0]]) == PLAN_SHA256 and plan['workers'] == 6 and plan['batch_size'] == 3000,
         'original plan changed')
    need(plan['mongo_uuid'] == str(bytes.fromhex(UUID)), 'original plan UUID changed')
    state, gate = json.loads(raw[paths[1]]), json.loads(raw[paths[3]])
    need(state['stage'] == 'paused' and state['workers'] == 0 and state['processed'] == TOTAL,
         'baseline is not paused at the approved prefix')
    need(gate['stage'] == 'naming_complete_waiting_backfill' and gate['cdc_active'] is False
         and not gate.get('last_error') and not (NAMING / 'baseline-ready.json').exists(), 'naming gate changed')
    parts, checkpoints = [], []
    need(len(plan['parts']) == len(APPROVED) == 6, 'worker coverage changed')
    for number, (planned, approved) in enumerate(zip(plan['parts'], APPROVED)):
        lower, last, count, rows_sha, ids_sha = approved
        checkpoint = json.loads(raw[BASELINE / f'worker-{number}.json'])
        need(planned['lower'] == lower and checkpoint['worker'] == number and checkpoint['last_id'] == last
             and checkpoint['processed'] == count and checkpoint.get('last_error') is None
             and (planned['upper'] is None or last < planned['upper']), 'committed worker prefix changed')
        parts.append({'worker': number, 'lower': lower, 'upper_exclusive': planned['upper'],
                      'upper_inclusive': last, 'last_id': last, 'processed': count,
                      'records_path': f'facts-burst-records-worker-{number}-20261116.jsonl',
                      'records_sha256': rows_sha, 'rows_sha256': rows_sha, 'record_ids_sha256': ids_sha})
        checkpoints.append({'worker': number, 'value': checkpoint,
                            'sha256': sha(raw[BASELINE / f'worker-{number}.json'])})
    need(sum(part['processed'] for part in parts) == TOTAL, 'approved count changed')
    evidence = {'plan': plan, 'baseline_state': state, 'naming_state': gate,
                'worker_checkpoints': checkpoints, 'baseline_ready_exists': False,
                'stop': {'path': str(paths[2]), 'sha256': sha(raw[paths[2]]), 'bytes': len(raw[paths[2]])}}
    # The naming heartbeat changes while waiting; retain its evidence, but fence stable controls below.
    stable = {path: sha(value) for path, value in raw.items() if path != paths[3]}
    return parts, evidence, stable


def native_syncer():
    ids = subprocess.check_output(['docker', 'ps', '-q', '--no-trunc', '--filter',
        'label=com.docker.compose.project=clawer', '--filter', 'label=com.docker.compose.service=syncer'], text=True).split()
    need(len(ids) == 1, 'expected one native syncer')
    row = json.loads(subprocess.check_output(['docker', 'inspect', ids[0]], text=True))[0]
    hosts = row['HostConfig'].get('ExtraHosts') or []
    need(row['State']['Running'] and any(host.startswith('mongo.spider.internal:') for host in hosts),
         'native syncer Mongo host mapping is absent')
    return {'id': ids[0], 'image': row['Image'], 'extra_hosts': hosts}


def validate_records(path, part, seen=None):
    rows_hash, ids_hash = hashlib.sha256(), hashlib.sha256()
    count, previous, first = 0, None, None
    with path.open('rb') as stream:
        for line in stream:
            row = json.loads(line)
            need(set(row) == {'mongo_id', 'record_id'} and isinstance(row['record_id'], str)
                 and row['record_id'].strip() and '\n' not in row['record_id'] and '\r' not in row['record_id'],
                 'invalid record file identity')
            oid = row['mongo_id']
            need(isinstance(oid, str) and len(oid) == 24 and all(c in '0123456789abcdef' for c in oid)
                 and part['lower'] <= oid <= part['upper_inclusive'] and (previous is None or previous < oid),
                 'record file is outside its ordered prefix')
            need(line == (json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n').encode(),
                 'record file encoding changed')
            if seen is not None:
                need(row['record_id'] not in seen, 'duplicate record ID across prefixes')
                seen.add(row['record_id'])
            rows_hash.update(line); ids_hash.update((row['record_id'] + '\n').encode())
            previous = oid; first = first or oid; count += 1
    need(count == part['processed'] and previous == part['upper_inclusive'], 'record file coverage differs')
    need(rows_hash.hexdigest() == part['records_sha256'] == part['rows_sha256']
         and ids_hash.hexdigest() == part['record_ids_sha256'], 'record file hashes differ')
    return {**part, 'actual_count': count, 'first_mongo_id': first, 'last_mongo_id': previous, 'mongo_uuid_hex': UUID}


def collect_part(syncer, output, part):
    target = output / part['records_path']
    temporary = target.with_name(target.name + '.partial-' + str(os.getpid()))
    payload = json.dumps({'part': part, 'mongo_uuid_hex': UUID})
    # Docker stdout remains on the source host: it is a private pipe into this file.
    code = WORKER.replace('payload = json.loads(sys.stdin.read())', 'payload = json.loads(' + repr(payload) + ')')
    try:
        with temporary.open('xb') as stream:
            os.chmod(temporary, 0o600)
            result = subprocess.run(['docker', 'exec', '-i', '-e', 'PYTHONPATH=/app', syncer['id'],
                                     'python', '-B', '-'], input=code, text=True, stdout=stream,
                                    stderr=subprocess.PIPE, timeout=220)
            stream.flush(); os.fsync(stream.fileno())
        need(result.returncode == 0, 'bounded reader failed for worker ' + str(part['worker']))
        value = validate_records(temporary, part)
        if target.exists():
            validate_records(target, part)
        else:
            os.replace(temporary, target)
        return value
    finally:
        temporary.unlink(missing_ok=True)


def build(output=None):
    output = OUTPUT if output is None else output
    parts, evidence, stable = snapshot()
    syncer = native_syncer()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        collected = list(pool.map(lambda part: collect_part(syncer, output, part), parts))
    seen = set()
    for part in collected:
        validate_records(output / part['records_path'], part, seen)
    need(len(seen) == TOTAL and all(sha(path.read_bytes()) == value for path, value in stable.items()),
         'source checkpoints or STOP changed during collection')
    _, _, after = snapshot()
    need(after == stable, 'source controls changed during collection')
    evidence['reference_syncer'] = syncer
    evidence['scope_statement'] = ('Exactly the six committed direct-baseline prefixes; not proof of every historical '
                                   'CDC-triggered record. No CDC is triggered by this collector.')
    manifest = {'protocol': PROTOCOL, 'scope': 'financial_fact', 'collection': 'financial_fact_sec_edgar',
        'data_source': 'financial_fact_sec_edgar', 'data_type': 'financial_fact',
        'created_at': datetime.now(timezone.utc).isoformat(), 'baseline_root': str(BASELINE),
        'naming_root': str(NAMING), 'plan_sha256': PLAN_SHA256, 'mongo_uuid_hex': UUID,
        'workers': 6, 'batch_size': 3000, 'sql_batch_size': 300, 'total': TOTAL, 'parts': collected,
        'evidence': evidence, 'collector_source_sha256': sha(Path(__file__).read_bytes()),
        'enumeration': {'read_only': True, 'separate_os_workers': 6, 'mongo_hint': '_id_',
            'range_semantics': 'lower <= _id <= upper_inclusive', 'page_size_limit': 3000,
            'per_page_max_time_ms': 30000, 'read_attempts_per_page': 3, 'record_ids_unique_across_ranges': True,
            'record_data_location': 'source_server_only', 'connection_mode': 'native_syncer_existing_config'}}
    path = output / 'facts-burst-manifest-20261116.json'
    raw = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    if path.exists():
        previous = json.loads(path.read_bytes())
        need(all(previous.get(key) == manifest[key] for key in manifest if key not in ('created_at', 'evidence'))
             and previous['evidence']['plan'] == evidence['plan']
             and previous['evidence']['stop'] == evidence['stop']
             and previous['evidence']['worker_checkpoints'] == evidence['worker_checkpoints'],
             'existing evidence manifest differs')
    else:
        temporary = path.with_name(path.name + '.partial-' + str(os.getpid()))
        try:
            with temporary.open('xb') as stream:
                os.chmod(temporary, 0o600); stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return {'manifest': str(path), 'manifest_sha256': sha(path.read_bytes()), 'total': TOTAL,
            'parts': [{'worker': part['worker'], 'actual_count': part['actual_count'],
                       'records_sha256': part['records_sha256']} for part in collected]}


def main():
    print(json.dumps(build(), sort_keys=True))


if __name__ == '__main__':
    main()
