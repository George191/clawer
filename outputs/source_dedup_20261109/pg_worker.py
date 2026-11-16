"""Six 3000-key readers with bounded 300-row source-rename transactions."""
from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import uuid
from datetime import datetime, timezone

WORKERS = 6
PAGE_SIZE = 3000
BATCH = 300
MAX_ROWS = 3000
MAX_BYTES = 8 * 1024 * 1024
GUARD_TABLE = 'public.source_dedup_20261109_tx'


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw.encode() if isinstance(raw, str) else raw).hexdigest()


def quote(name):
    if not re.fullmatch('[a-z][a-z0-9_]*', name):
        raise ValueError('Unexpected SQL identifier')
    return '"' + name + '"'


def qualified(schema, table):
    return quote(schema) + '.' + quote(table)


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    with temporary.open('wb') as stream:
        stream.write(encoded(value)); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def immutable(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    with temporary.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise RuntimeError('Existing immutable receipt differs')
    finally:
        temporary.unlink()


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def routing_sql(value, layer):
    """Postgres edits routing only; numeric and business JSON remain database typed."""
    result = f"jsonb_set({value},'{{data_source}}',to_jsonb($3::text),false)"
    if layer == 'rds':
        body = f"({value}->'raw_data')"
        for path in ('{_meta,template}', '{_meta,data_source}', '{_meta,source}'):
            body = (f"CASE WHEN {body}#>>'{path}'=$2::text THEN "
                    f"jsonb_set({body},'{path}',to_jsonb($3::text),false) ELSE {body} END")
        body = f"jsonb_set(({body}),'{{data_source}}',to_jsonb($3::text),true)"
        result = f"jsonb_set({result},'{{raw_data}}',({body}),false)"
    return result


def classify(keys, locked, old, new):
    """An existing canonical key wins exactly as authorized; never compare unrelated sources."""
    output = []
    for key in keys:
        rid, kind = key['record_id'], key['data_type']
        before = locked.get((rid, old, kind))
        target = locked.get((rid, new, kind))
        if before is None:
            raise RuntimeError('Old source changed between key read and lock')
        output.append({'record_id': rid, 'data_type': kind, 'old_raw': before,
                       'new_raw': target, 'action': 'dedup' if target is not None else 'rename'})
    return output


def bounded(rows, maximum=BATCH):
    if not 1 <= maximum <= MAX_ROWS:
        raise ValueError('Batch must contain at most 3000 rows')
    result, size = [], 2
    for row in rows[:maximum]:
        count = len(row['old_raw'].encode()) + (len(row['new_raw'].encode()) if row['new_raw'] else 0) + 2
        if count > MAX_BYTES - 2:
            raise RuntimeError('One identity exceeds 8 MiB')
        if result and size + count > MAX_BYTES:
            break
        result.append(row); size += count
    return result


async def discover(connection, scopes):
    units = []
    for scope in scopes:
        if not all(isinstance(scope.get(k), str) and scope[k] for k in ('old', 'new', 'data_type')) or scope['old'] == scope['new']:
            raise ValueError('Invalid source mapping')
        if scope['new'] == 'financial_fact_sec_edgar' and scope['data_type'] != 'financial_fact':
            raise ValueError('SEC financial facts cannot use an organization identity')
        for relation in scope['relations']:
            schema, table, layer = relation['schema'], relation['name'], relation['layer']
            kind = relation.get('row_data_type', relation.get('data_type', scope['data_type']))
            if layer not in ('rds', 'ods') or schema != 'ts_' + layer or table != layer + '_' + kind:
                raise ValueError('Unexpected current-state relation')
            parent = qualified(schema, table)
            layout = await connection.fetchval('SELECT pg_get_partkeydef($1::regclass)', parent)
            if layout != 'HASH (record_id, data_source, data_type)':
                raise RuntimeError('Unexpected partition identity: ' + parent)
            bad = await connection.fetchval('''
                SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid=$1::regclass AND attnum>0
                    AND NOT attisdropped AND (attgenerated<>'' OR attidentity<>''))
                  OR EXISTS(SELECT 1 FROM pg_trigger WHERE NOT tgisinternal AND tgenabled<>'D'
                    AND tgrelid IN(SELECT relid FROM pg_partition_tree($1::regclass)))
                  OR EXISTS(SELECT 1 FROM pg_constraint WHERE contype='f' AND
                    (conrelid IN(SELECT relid FROM pg_partition_tree($1::regclass)) OR
                     confrelid IN(SELECT relid FROM pg_partition_tree($1::regclass))))
            ''', parent)
            if bad:
                raise RuntimeError('Unexpected generated columns, triggers or foreign keys: ' + parent)
            leaves = await connection.fetch('''
                SELECT n.nspname AS schema,c.relname AS name,c.oid::bigint AS oid,
                       pg_get_expr(c.relpartbound,c.oid) AS bound
                FROM pg_partition_tree($1::regclass) p JOIN pg_class c ON c.oid=p.relid
                JOIN pg_namespace n ON n.oid=c.relnamespace WHERE p.isleaf ORDER BY c.oid
            ''', parent)
            ordinals = set()
            for leaf in leaves:
                match = re.fullmatch(r'FOR VALUES WITH \(modulus (\d+), remainder (\d+)\)', leaf['bound'])
                if not match or leaf['schema'] != schema:
                    raise RuntimeError('Unexpected source partition')
                modulus, ordinal = map(int, match.groups()); ordinals.add(ordinal)
                units.append({'old': scope['old'], 'new': scope['new'], 'data_type': kind,
                    'schema': schema, 'table': table, 'layer': layer, 'leaf': leaf['name'],
                    'oid': leaf['oid'], 'ordinal': ordinal, 'modulus': modulus})
            if not leaves or ordinals != set(range(modulus)):
                raise RuntimeError('Incomplete partition coverage')
    units.sort(key=lambda u: (0 if u['layer'] == 'rds' else 1, u['table'], u['old'], u['ordinal']))
    signatures = [(u['schema'], u['table'], u['old'], u['data_type'], u['leaf']) for u in units]
    if len(set(signatures)) != len(signatures):
        raise RuntimeError('Overlapping source units')
    for number, unit in enumerate(units):
        unit.update(unit=number, worker=number % WORKERS)
    return units


async def locked_rows(connection, unit, keys):
    parent = qualified(unit['schema'], unit['table'])
    parameters = [{**key, 'data_source': source} for key in keys for source in (unit['old'], unit['new'])]
    rows = await connection.fetch(f'''
        WITH k AS MATERIALIZED(SELECT * FROM jsonb_to_recordset($1::jsonb)
          AS k(record_id text,data_source text,data_type text) ORDER BY record_id,data_source,data_type)
        SELECT k.record_id,k.data_source,k.data_type,t.raw
        FROM k CROSS JOIN LATERAL(SELECT to_jsonb(t)::text AS raw FROM {parent} t
          WHERE t.record_id=k.record_id AND t.data_source=k.data_source AND t.data_type=k.data_type
          FOR UPDATE OF t OFFSET 0) t
    ''', json.dumps(parameters))
    return {(row['record_id'], row['data_source'], row['data_type']): row['raw'] for row in rows}


async def expected_rows(connection, unit, rows):
    raw = '[' + ','.join(row['old_raw'] if row['action'] == 'rename' else row['new_raw'] for row in rows) + ']'
    values = await connection.fetch(f'''
        SELECT j.value->>'record_id' AS record_id,({routing_sql('j.value', unit['layer'])})::text AS raw
        FROM jsonb_array_elements($1::jsonb) j(value) WHERE $2::text IS NOT NULL AND $3::text IS NOT NULL
    ''', raw, unit['old'], unit['new'])
    return {row['record_id']: row['raw'] for row in values}


async def verify_after(connection, unit, rows):
    keys = [{'record_id': row['record_id'], 'data_type': unit['data_type']} for row in rows]
    actual = await locked_rows(connection, unit, keys)
    return all((row['record_id'], unit['old'], unit['data_type']) not in actual and
        sha(actual.get((row['record_id'], unit['new'], unit['data_type']), '')) == row['expected_new_sha256']
        for row in rows)


async def apply_batch(connection, root, plan, unit, keys, state, state_path):
    parent, leaf = qualified(unit['schema'], unit['table']), qualified(unit['schema'], unit['leaf'])
    pending_path = root / 'transactions' / ('batch-' + str(unit['unit']) + '-' + uuid.uuid4().hex + '.prepared.json')
    async with connection.transaction():
        await connection.execute("SET LOCAL TIME ZONE 'UTC'")
        await connection.execute("SET LOCAL lock_timeout='15s'")
        await connection.execute("SET LOCAL statement_timeout='120s'")
        identities = sorted('|'.join((unit['schema'], unit['table'], unit['new'], unit['data_type'], key['record_id'])) for key in keys)
        await connection.execute('''WITH k AS MATERIALIZED(SELECT value FROM unnest($1::text[]) AS x(value)
            ORDER BY value) SELECT pg_advisory_xact_lock(hashtextextended(value,0)) FROM k''', identities)
        locked = await locked_rows(connection, unit, keys)
        rows = bounded(classify(keys, locked, unit['old'], unit['new']), int(plan.get('batch_size', BATCH)))
        expected = await expected_rows(connection, unit, rows)
        txid_full = int(await connection.fetchval('SELECT txid_current()'))
        records = []
        for row in rows:
            before = json.loads(row['old_raw'])
            fields = {'data_source': before['data_source']}
            if unit['layer'] == 'rds':
                body = before['raw_data']; meta = body.get('_meta') or {}
                fields['raw_data.routing'] = {'data_source_present': 'data_source' in body,
                    'data_source': body.get('data_source'),
                    **{name: {'present': name in meta, 'value': meta.get(name)} for name in ('template', 'data_source', 'source')}}
            records.append({'record_id': row['record_id'], 'action': row['action'],
                'before_old_sha256': sha(row['old_raw']),
                'before_new_sha256': sha(row['new_raw']) if row['new_raw'] is not None else None,
                'expected_new_sha256': sha(expected[row['record_id']]), 'before_fields': fields})
        conflicts = [row['old_raw'] for row in rows if row['action'] == 'dedup']
        archive = None
        if conflicts:
            archive_path = pending_path.with_suffix('.old-conflicts.jsonl.gz')
            raw = gzip.compress(('\n'.join(conflicts) + '\n').encode(), mtime=0)
            immutable(archive_path, raw)
            archive = {'path': str(archive_path.relative_to(root)), 'sha256': sha(raw), 'rows': len(conflicts)}
        batch_sha = sha(encoded(records))
        pending = {'plan_sha256': plan['plan_sha256'], 'unit': unit['unit'], 'txid_full': txid_full,
            'txid': txid_full % 4294967296, 'schema': unit['schema'], 'table': unit['table'],
            'old': unit['old'], 'new': unit['new'], 'data_type': unit['data_type'],
            'rows': len(rows), 'batch_sha256': batch_sha, 'records': records, 'archive': archive,
            'next_cursor': rows[-1]['record_id']}
        immutable(pending_path, encoded(pending))
        atomic(state_path, {**state, 'stage': 'transaction_pending', 'pending': str(pending_path.relative_to(root))})
        if plan.get('guard_table'):
            assert plan['guard_table'] == GUARD_TABLE
            await connection.execute(f'''
                INSERT INTO {GUARD_TABLE}(txid,schema_name,table_name,old_source,new_source,data_type,
                     processed_rows,batch_sha256,created_at)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,now()) ON CONFLICT DO NOTHING
            ''', pending['txid'], unit['schema'], unit['table'], unit['old'], unit['new'],
                unit['data_type'], len(rows), batch_sha)
            guard = await connection.fetchrow(f'''SELECT processed_rows,batch_sha256 FROM {GUARD_TABLE}
                WHERE txid=$1 AND schema_name=$2 AND table_name=$3 AND old_source=$4 AND new_source=$5 AND data_type=$6''',
                pending['txid'], unit['schema'], unit['table'], unit['old'], unit['new'], unit['data_type'])
            if not guard or guard['processed_rows'] != len(rows) or guard['batch_sha256'] != batch_sha:
                raise RuntimeError('Committed transaction guard binding differs')
        after = {row['record_id']: row['new_raw'] for row in rows if row['action'] == 'dedup'}
        rename = [row for row in rows if row['action'] == 'rename']
        if rename:
            payload = '[' + ','.join(row['old_raw'] for row in rename) + ']'
            raw_update = (f",raw_data=({routing_sql('to_jsonb(t)', 'rds')})->'raw_data'" if unit['layer'] == 'rds' else '')
            changed = await connection.fetch(f'''
                WITH b AS MATERIALIZED(SELECT value FROM jsonb_array_elements($1::jsonb))
                  UPDATE {parent} t SET data_source=$3::text {raw_update} FROM b
                  WHERE t.record_id=b.value->>'record_id' AND t.data_source=$2::text
                    AND t.data_type=$4::text AND t.tableoid=$5::oid AND to_jsonb(t)=b.value
                  RETURNING t.record_id,t.data_source,t.data_type,to_jsonb(t)::text AS raw
            ''', payload, unit['old'], unit['new'], unit['data_type'], unit['oid'])
            wanted = {(row['record_id'], unit['new'], unit['data_type']) for row in rename}
            actual = {(row['record_id'], row['data_source'], row['data_type']) for row in changed}
            if len(wanted) != len(rename) or len(changed) != len(rename) or actual != wanted:
                raise RuntimeError('Old-only UPDATE CAS failed')
            after.update((row['record_id'], row['raw']) for row in changed)
        duplicates = [row for row in rows if row['action'] == 'dedup']
        if duplicates:
            if unit['layer'] == 'rds':
                payload = '[' + ','.join(row['new_raw'] for row in duplicates) + ']'
                # Canonical business data wins; normalize only its source-routing metadata.
                changed = await connection.fetch(f'''
                    WITH b AS MATERIALIZED(SELECT value FROM jsonb_array_elements($1::jsonb))
                    UPDATE {parent} t SET raw_data=({routing_sql('to_jsonb(t)', 'rds')})->'raw_data'
                    FROM b WHERE t.record_id=b.value->>'record_id' AND t.data_source=$3::text
                      AND t.data_type=$4::text AND to_jsonb(t)=b.value
                      AND t.raw_data IS DISTINCT FROM ({routing_sql('to_jsonb(t)', 'rds')})->'raw_data'
                    RETURNING t.record_id,t.data_source,t.data_type,to_jsonb(t)::text AS raw
                ''', payload, unit['old'], unit['new'], unit['data_type'])
                wanted = {(row['record_id'], unit['new'], unit['data_type']) for row in duplicates}
                actual = {(row['record_id'], row['data_source'], row['data_type']) for row in changed}
                if len(changed) != len(actual) or not actual <= wanted:
                    raise RuntimeError('Canonical UPDATE returned unexpected identities')
                after.update((row['record_id'], row['raw']) for row in changed)
            payload = '[' + ','.join(row['old_raw'] for row in duplicates) + ']'
            deleted = await connection.fetchval(f'''
                WITH b AS MATERIALIZED(SELECT value FROM jsonb_array_elements($1::jsonb)), removed AS(
                  DELETE FROM ONLY {leaf} t USING b WHERE t.record_id=b.value->>'record_id'
                    AND t.data_source=$2::text AND t.data_type=$3::text AND to_jsonb(t)=b.value
                  RETURNING t.record_id) SELECT count(*) FROM removed
            ''', payload, unit['old'], unit['data_type'])
            if deleted != len(duplicates):
                raise RuntimeError('Conflicting old DELETE CAS failed')
        # Row locks retain unchanged canonical snapshots; DML returns every changed afterimage.
        if any(sha(after.get(row['record_id'], '')) != row['expected_new_sha256'] for row in records):
            raise RuntimeError('Canonical UPDATE afterimages differ')
    pending['upper_lsn'] = int(await connection.fetchval("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(),'0/0')::bigint"))
    pending['committed'] = True
    committed = pending_path.with_suffix('.committed.json')
    immutable(committed, encoded(pending))
    return pending


def checkpoint_after(state, receipt):
    return {**state, 'stage': 'running', 'cursor': receipt['next_cursor'], 'pending': None,
        'processed': state.get('processed', 0) + receipt['rows'],
        'renamed': state.get('renamed', 0) + sum(row['action'] == 'rename' for row in receipt['records']),
        'deduplicated': state.get('deduplicated', 0) + sum(row['action'] == 'dedup' for row in receipt['records']),
        'last_transaction': {'txid': receipt['txid'], 'upper_lsn': receipt['upper_lsn'],
                             'batch_sha256': receipt['batch_sha256']}, 'last_error': None}


async def recover_pending(connection, root, plan, unit, state, state_path):
    if not state.get('pending'):
        return state
    pending_path = root / state['pending']; pending = read(pending_path)
    if pending['plan_sha256'] != plan['plan_sha256'] or pending['unit'] != unit['unit']:
        raise RuntimeError('Pending transaction belongs to a different plan')
    status = await connection.fetchval('SELECT pg_xact_status($1::text::xid8)', str(pending['txid_full']))
    if status == 'committed':
        async with connection.transaction():
            if not await verify_after(connection, unit, pending['records']):
                raise RuntimeError('Lost-ack committed transaction afterimages differ')
        committed_path = pending_path.with_suffix('.committed.json')
        if committed_path.exists():
            previous = read(committed_path)
            if previous['batch_sha256'] != pending['batch_sha256'] or previous['txid_full'] != pending['txid_full']:
                raise RuntimeError('Existing committed receipt belongs to a different transaction')
            pending = previous
        else:
            pending.update(committed=True, recovered_commit=True,
                upper_lsn=int(await connection.fetchval("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(),'0/0')::bigint")))
            immutable(committed_path, encoded(pending))
        state = checkpoint_after(state, pending)
    elif status == 'aborted':
        state = {**state, 'pending': None, 'stage': 'running'}
    else:
        raise RuntimeError('Pending transaction outcome is not yet verified: ' + str(status))
    atomic(state_path, state)
    return state


async def connect(application_name='source-dedup-prepare'):
    import asyncpg
    from app.storage.postgres_client import _resolved_pg_url
    connection = await asyncpg.connect(_resolved_pg_url().replace('postgresql+asyncpg://', 'postgresql://', 1),
                                      timeout=15, command_timeout=150,
                                      server_settings={'application_name': application_name})
    await connection.execute("SET TIME ZONE 'UTC'")
    return connection


async def owned_connection(root, number):
    owner = 'source-dedup-' + sha(str(root))[:12] + '-worker-' + str(number)
    connection = await connect(owner)
    acquired = await connection.fetchval('SELECT pg_try_advisory_lock(hashtextextended($1,0))',
                                        'source-dedup-worker|' + str(root) + '|' + str(number))
    if not acquired:
        await connection.close()
        raise RuntimeError('This run already owns worker ' + str(number))
    return connection


def runtime_layout(root, plan):
    layout = read(root / 'layout.json', {})
    value = {'workers': int(layout.get('workers', WORKERS)),
             'page_size': int(layout.get('page_size', PAGE_SIZE)),
             'sql_batch_size': int(layout.get('sql_batch_size', BATCH)),
             'plan_sha256': layout.get('plan_sha256', plan['plan_sha256'])}
    if (value['workers'] != WORKERS or value['page_size'] != PAGE_SIZE
            or not 1 <= value['sql_batch_size'] <= BATCH or value['plan_sha256'] != plan['plan_sha256']):
        raise RuntimeError('Expected the authorized 6-worker/3000-key layout with SQL batches at most 300')
    scope = read(root / 'scope.json', {})
    if scope.get('protocol') == 'source_safe_facts_full_alias_tail_v1':
        scopes = scope.get('scopes', [])
        relations = {('ts_rds', 'rds_financial_fact', 'rds'), ('ts_ods', 'ods_financial_fact', 'ods')}
        if (scope.get('workers') != WORKERS or scope.get('batch_size') != BATCH
                or scope.get('guard_table') is not None or len(scopes) != 2
                or {s.get('old') for s in scopes} != {'sec_edgar_financial_fact', 'sec_edgar_company'}
                or any(s.get('new') != 'financial_fact_sec_edgar' or s.get('data_type') != 'financial_fact'
                       or len(s.get('relations', [])) != 2
                       or any(set(r) != {'schema', 'name', 'layer'} for r in s['relations'])
                       or {(r['schema'], r['name'], r['layer']) for r in s['relations']} != relations
                       for s in scopes)):
            raise RuntimeError('Unexpected dedicated facts source-tail scope')
        value['scope'] = 'facts_full'
    elif layout.get('scope') is not None:
        raise RuntimeError('A dedicated facts layout requires its immutable scope')
    return value


def unit_owner(unit, layout):
    table = (unit['schema'], unit['table'])
    if table in (('ts_rds', 'rds_financial_fact'), ('ts_ods', 'ods_financial_fact')):
        if layout.get('scope') == 'facts_full':
            if (unit['old'] not in ('sec_edgar_financial_fact', 'sec_edgar_company')
                    or unit['new'] != 'financial_fact_sec_edgar' or unit['data_type'] != 'financial_fact'):
                raise ValueError('Unexpected dedicated facts source identity')
            return unit['unit'] % WORKERS
        return None
    slots = {('ts_rds', 'rds_filing'): 0, ('ts_ods', 'ods_filing'): 2,
             ('ts_ods', 'ods_filing_document'): 4}
    if table in slots:
        return slots[table] + unit['ordinal'] % 2
    if table in (('ts_rds', 'rds_patent'), ('ts_ods', 'ods_patent')):
        return unit['unit'] % WORKERS
    raise ValueError('Unexpected SEC history repair table')


def keyset_page_query(unit, cursor, page_size):
    leaf = qualified(unit['schema'], unit['leaf'])
    parameters = [unit['old'], unit['data_type']]
    lower_bound = ''
    if cursor is not None:
        parameters.append(cursor)
        lower_bound = ' AND record_id>$3::text'
    parameters.append(page_size)
    query = (f'SELECT record_id,data_type FROM ONLY {leaf} '
             f'WHERE data_source=$1 AND data_type=$2{lower_bound} '
             f'ORDER BY record_id LIMIT ${len(parameters)}')
    return query, parameters


async def worker_run(root, number):
    import asyncpg
    plan = read(root / 'plan.json'); source_raw = (root / 'scope.json').read_bytes()
    if plan['plan_sha256'] != sha(source_raw) or plan['workers'] != WORKERS or not 1 <= plan['batch_size'] <= MAX_ROWS:
        raise RuntimeError('Immutable plan/source binding changed')
    layout = runtime_layout(root, plan)
    transaction_plan = {**plan, 'batch_size': layout['sql_batch_size']}
    connection = await owned_connection(root, number)
    print(json.dumps({'observed_at': datetime.now(timezone.utc).isoformat(), 'stage': 'worker_started',
                      'os_pid': os.getpid(), 'worker': number, **layout}), flush=True)
    try:
        for unit in plan['units']:
            if unit_owner(unit, layout) != number:
                continue
            state_path = root / 'checkpoints' / ('unit-' + str(unit['unit']) + '.json')
            state = read(state_path, {'plan_sha256': plan['plan_sha256'], 'unit': unit['unit'],
                'worker': number, 'cursor': None, 'processed': 0, 'renamed': 0, 'deduplicated': 0,
                'pending': None, 'done': False, 'last_error': None})
            if state['plan_sha256'] != plan['plan_sha256']:
                raise RuntimeError('Checkpoint plan changed')
            state = await recover_pending(connection, root, plan, unit, state, state_path)
            state = {**state, 'worker': number, 'layout_workers': layout['workers'],
                     'page_size': layout['page_size'], 'sql_batch_size': layout['sql_batch_size']}
            atomic(state_path, state)
            if state.get('done'):
                continue
            failures = 0
            while True:
                if (root / 'STOP').exists():
                    atomic(state_path, {**state, 'stage': 'paused', 'last_error': None}); return
                try:
                    query, parameters = keyset_page_query(unit, state['cursor'], layout['page_size'])
                    keys = [dict(row) for row in await connection.fetch(query, *parameters, timeout=120)]
                    if not keys:
                        atomic(state_path, {**state, 'stage': 'complete', 'done': True, 'last_error': None}); break
                    position = 0
                    while position < len(keys):
                        if (root / 'STOP').exists():
                            atomic(state_path, {**state, 'stage': 'paused', 'last_error': None}); return
                        batch = keys[position:position + layout['sql_batch_size']]
                        receipt = await apply_batch(connection, root, transaction_plan, unit, batch, state, state_path)
                        state = checkpoint_after(state, receipt); atomic(state_path, state); failures = 0
                        position += receipt['rows']
                        print(json.dumps({'observed_at': datetime.now(timezone.utc).isoformat(),
                            'stage': 'batch_committed', 'os_pid': os.getpid(), 'worker': number,
                            'unit': unit['unit'], 'schema': unit['schema'], 'table': unit['table'],
                            'old': unit['old'], 'new': unit['new'], 'rows': receipt['rows'],
                            'page_size': layout['page_size'], 'sql_batch_size': layout['sql_batch_size'],
                            **{key: state[key] for key in ('processed', 'renamed', 'deduplicated')}}), flush=True)
                except (asyncio.TimeoutError, asyncpg.DeadlockDetectedError, asyncpg.SerializationError,
                        asyncpg.LockNotAvailableError, asyncpg.QueryCanceledError, asyncpg.UniqueViolationError,
                        asyncpg.ConnectionDoesNotExistError, OSError) as error:
                    failures += 1
                    if connection.is_closed():
                        connection = await owned_connection(root, number)
                    state = read(state_path, state)
                    state = await recover_pending(connection, root, plan, unit, state, state_path)
                    if failures >= 5:
                        atomic(state_path, {**state, 'stage': 'failed', 'last_error': type(error).__name__}); raise
                    await asyncio.sleep(min(2 ** failures, 15))
    finally:
        await connection.close()


def worker_entry(root, number):
    try:
        asyncio.run(worker_run(Path(root), number))
    except BaseException as error:
        atomic(Path(root) / ('worker-' + str(number) + '-error.json'), {
            'worker': number, 'stage': 'failed', 'last_error': type(error).__name__,
            'detail': str(error) if isinstance(error, (RuntimeError, ValueError)) else None,
        })
        raise


async def prepare(root):
    raw = (root / 'scope.json').read_bytes(); scope = json.loads(raw)
    batch = int(scope.get('batch_size', BATCH))
    if not 1 <= batch <= MAX_ROWS or scope.get('guard_table') not in (None, GUARD_TABLE):
        raise ValueError('Unexpected batch size or guard relation')
    path = root / 'plan.json'
    if path.exists():
        if read(path)['plan_sha256'] != sha(raw):
            raise RuntimeError('Existing plan cannot be replaced')
        return read(path)
    connection = await connect()
    try:
        units = await discover(connection, scope['scopes'])
    finally:
        await connection.close()
    value = {'workers': WORKERS, 'batch_size': batch, 'plan_sha256': sha(raw), 'units': units,
             'guard_table': scope.get('guard_table')}
    immutable(path, encoded(value))
    return value


def main(args):
    root = Path(args.root).resolve(); root.mkdir(parents=True, exist_ok=True)
    if args.prepare:
        plan = asyncio.run(prepare(root)); layout = runtime_layout(root, plan)
        if not (root / 'layout.json').exists():
            immutable(root / 'layout.json', encoded(layout))
        print(json.dumps({'plan_sha256': plan['plan_sha256'], 'units': len(plan['units']), **layout})); return
    if args.worker is not None:
        if not 0 <= args.worker < WORKERS:
            raise ValueError('Worker must be 0 through 5')
        worker_entry(str(root), args.worker); return
    plan = asyncio.run(prepare(root)); layout = runtime_layout(root, plan)
    if not (root / 'layout.json').exists():
        immutable(root / 'layout.json', encoded(layout))
    context = multiprocessing.get_context('spawn')
    processes = [context.Process(target=worker_entry, args=(str(root), number), name='source-dedup-' + str(number)) for number in range(WORKERS)]
    try:
        for process in processes:
            process.start()
        while True:
            for process in processes:
                process.join(timeout=0.2)
                if process.exitcode not in (None, 0):
                    raise RuntimeError('One or more source-dedup workers failed; preserve checkpoints')
            if all(process.exitcode == 0 for process in processes):
                break
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            if process.pid is not None:
                process.join()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--root', required=True)
    parser.add_argument('--prepare', action='store_true'); parser.add_argument('--worker', type=int, default=None)
    main(parser.parse_args())
