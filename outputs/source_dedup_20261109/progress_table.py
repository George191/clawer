"""Read committed historical-repair checkpoints and print a compact progress table."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import unicodedata

if __package__:
    from .pg_worker import unit_owner
else:
    from pg_worker import unit_owner

HERE = Path(__file__).resolve().parent
RUN = 'source_dedup_20261109/sec-history-run01'
PATENT_RUN_ID = 'b1750edb-6390-479b-90be-077fcb08e61c'
BEIJING = timezone(timedelta(hours=8))
TABLES = (
    ('ts_rds', 'rds_filing', 'filing', 'RDS'),
    ('ts_ods', 'ods_filing', 'filing', 'ODS'),
    ('ts_ods', 'ods_filing_document', 'document', 'ODS'),
    ('ts_rds', 'rds_financial_fact', 'facts', 'RDS'),
    ('ts_ods', 'ods_financial_fact', 'facts', 'ODS'),
)

PROBE = r'''
import asyncio,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
import asyncpg
from app.storage.postgres_client import _resolved_pg_url

ROOT=Path('/app/output/source_dedup_20261109/sec-history-run01')
def read_tle_archive():
 base={'verified':False,'sampled_at':datetime.now(timezone.utc).isoformat(),'planned_documents':38}
 root=Path('/app/output/tle_archive_history_20261112')
 try:
  gap_path=root/'gap_audit.json';gap=None
  if gap_path.exists():
   gap=json.loads(gap_path.read_bytes())
   target=gap['target_days'];available=gap['target_days_with_evidence'];missing=gap['target_days_without_evidence']
   if (gap.get('offline') is not True or gap.get('six_disjoint_worker_manifests_verified') is not True
    or type(gap.get('recoverable_tle_records')) is not int or gap['recoverable_tle_records']!=38
    or len(target)!=54 or len(set(target))!=54 or set(available)!={'2025-01-22'}
    or len(missing)!=53 or len(set(missing))!=53 or set(available)&set(missing)
    or set(target)!=set(available)|set(missing)):raise ValueError('Archive gap audit differs')
   base.update(recoverable_days=1,days_without_evidence=53)
  path=root/'manifest.json'
  if not path.exists():return {**base,'error_class':'NotStarted'}
  if gap is None:raise ValueError('Archive gap audit missing')
  raw=path.read_bytes();manifest=json.loads(raw);fingerprint=hashlib.sha256(raw).hexdigest()
  run_id=manifest['run_id']
  if (not isinstance(run_id,str) or not run_id or type(manifest['workers']) is not int or manifest['workers']!=6
   or type(manifest['batch_size']) is not int or not 1<=manifest['batch_size']<=3000
   or type(manifest['total_documents']) is not int or manifest['total_documents']!=38
   or manifest['source_kind']!='original_mike_archive' or manifest['data_source']!='orbital_mike_mccants_archive'
   or manifest['target_epoch_day']!='2025-01-22'):raise ValueError('Archive manifest differs')
  counts=dict.fromkeys(('committed_documents','ods_inserted','ods_existing'),0)
  completed=0;pids=[];errors=[]
  for number in range(6):
   path=root/'checkpoints'/('worker-'+str(number)+'.json')
   if not path.exists():continue
   cp=json.loads(path.read_bytes());expected=7 if number<2 else 6
   if (cp['run_id']!=run_id or cp['manifest_sha256']!=fingerprint or type(cp['worker']) is not int
    or cp['worker']!=number or type(cp['source_read']) is not int or cp['source_read']!=expected
    or any(type(cp[k]) is not int or cp[k]<0 for k in counts)
    or cp['committed_documents']!=cp['ods_inserted']+cp['ods_existing'] or cp['committed_documents']>expected
    or type(cp['done']) is not bool or (cp['done'] and cp['committed_documents']!=expected)
    or cp.get('rolled_back') is True or (cp['done'] and cp.get('rolled_back') is not False)
    or type(cp['pid']) is not int or cp['pid']<=0 or cp['pid'] in pids
    or datetime.fromisoformat(cp['updated_at'].replace('Z','+00:00')).tzinfo is None):
    raise ValueError('Archive committed checkpoint differs')
   pids.append(cp['pid']);completed+=cp['done']
   for key in counts:counts[key]+=cp[key]
   if cp.get('last_error'):
    error=cp['last_error'];kind=error.get('type') if isinstance(error,dict) else None
    errors.append(kind if isinstance(kind,str) and kind else 'WorkerError')
  stage='错误' if errors else '待验收' if completed==6 else '待执行' if not pids else '已有检查点'
  result_path=root/'result.json';complete=False
  if result_path.exists():
   result=json.loads(result_path.read_bytes())
   if (result['run_id']!=run_id or result['manifest_sha256']!=fingerprint or type(result['complete']) is not bool
    or any(type(result[k]) is not int or result[k]!=counts[k] for k in counts)):
    raise ValueError('Archive result binding or counters differ')
   if result['complete']:
    if completed!=6 or counts['committed_documents']!=38 or errors or result['worker_pids']!=pids:
     raise ValueError('Archive completion coverage differs')
    complete=True;stage='已完成'
  return {**base,'verified':True,'run_id':run_id,'manifest_sha256':fingerprint,**counts,
   'completed_workers':completed,'restored_days':int(complete),'stage':stage,'errors':errors}
 except Exception as error:
  return {'verified':False,'sampled_at':base['sampled_at'],'planned_documents':38,'error_class':type(error).__name__}
def read_tle_es():
 sampled_at=datetime.now(timezone.utc).isoformat()
 root=Path('/app/output/tle_es_history_20261112')
 try:
  manifest_path=root/'manifest.json'
  if not manifest_path.exists():
   return {'verified':False,'sampled_at':sampled_at,'error_class':'NotStarted'}
  manifest_bytes=manifest_path.read_bytes()
  manifest=json.loads(manifest_bytes)
  states=[]
  for number in range(6):
   path=root/'checkpoints'/('worker-'+str(number)+'.json')
   if not path.exists():continue
   body=path.read_bytes();state=json.loads(body)
   if state.get('worker')!=number:raise ValueError('ES worker filename binding differs')
   states.append({**{key:state.get(key) for key in ('run_id','manifest_sha256','worker','updated_at',
    'es_read','committed_documents','ods_inserted','ods_existing','done','last_error','pass_number')},
    **{key:state[key] for key in ('scan_cursor_seq_no','scan_documents','scan_global_checkpoint',
      'scroll_restarts','scroll_consecutive_failures','last_scroll_error') if key in state},
    'last_error':{key:state['last_error'].get(key) for key in ('type','at')}
     if isinstance(state.get('last_error'),dict) else state.get('last_error'),
    'file_sha256':hashlib.sha256(body).hexdigest()})
  result_path=root/'result.json';result=None
  if result_path.exists():
   proof=json.loads(result_path.read_text())
   result={key:proof.get(key) for key in ('run_id','manifest_sha256','pass_number','complete','exitcodes',
    'source_count_before','source_count_after','source_count_after_archive_audit','unique_committed_documents',
    'ods_inserted','ods_existing','full_scan_documents','worker_checkpoint_sha256','source_consistency')}
  return {'verified':True,'sampled_at':sampled_at,'manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest(),
   'manifest':{key:manifest.get(key) for key in ('run_id','workers','batch_size','total_documents','initial_documents_exact')},
   'checkpoints':states,'result':result}
 except Exception as error:
  return {'verified':False,'sampled_at':sampled_at,'error_class':type(error).__name__}
def read_patent():
 sampled_at=datetime.now(timezone.utc).isoformat()
 expected='b1750edb-6390-479b-90be-077fcb08e61c'
 try:
  state=json.loads(Path('/app/output/sec_migration_20261104/patent-cdc-state.json').read_text())
  if state.get('run_id')!=expected:raise ValueError('Patent run binding differs')
  value={key:state.get(key) for key in ('observed_at','run_id','stage','name_processed','changed',
     'total','active_workers','workers','logical_partitions','batch_size','cdc_active','last_error')}
  directory=Path('/app/output/sec_migration_20261104/patent-cdc-20261105T105239Z-b1750edb6390/journals')
  completed=0
  for number in range(8):
   checkpoint=json.loads((directory/('name-worker-'+str(number)+'.json')).read_text())
   if checkpoint.get('run_id')!=expected or checkpoint.get('phase')!='name':
    raise ValueError('Patent naming checkpoint binding differs')
   completed+=checkpoint.get('complete') is True
  return {**value,'verified':True,'sampled_at':sampled_at,'completed_workers':completed}
 except Exception as error:
  return {'verified':False,'sampled_at':sampled_at,'run_id':expected,'error_class':type(error).__name__}
async def main():
 plan=json.loads((ROOT/'plan.json').read_text())
 scope_bytes=(ROOT/'scope.json').read_bytes()
 if hashlib.sha256(scope_bytes).hexdigest()!=plan['plan_sha256']:
  raise RuntimeError('Repair scope and plan differ')
 layout_path=ROOT/'layout.json'
 layout=json.loads(layout_path.read_text()) if layout_path.exists() else {
  'workers':plan['workers'],'page_size':plan['batch_size'],'sql_batch_size':plan['batch_size']}
 states=[];errors=[]
 for path in (ROOT/'checkpoints').glob('unit-*.json'):
  value=json.loads(path.read_text())
  states.append({key:value.get(key) for key in (
   'unit','worker','stage','processed','renamed','deduplicated','done','pending','last_error')})
 started=ROOT/'started.json';cutoff=started.stat().st_mtime if started.exists() else 0
 for path in ROOT.glob('worker-*-error.json'):
  if path.stat().st_mtime<cutoff:continue
  value=json.loads(path.read_text())
  errors.append({key:value.get(key) for key in ('worker','unit','stage','last_error')})
 prefix='source-dedup-'+hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]+'-worker-'
 connection=await asyncpg.connect(
  _resolved_pg_url().replace('postgresql+asyncpg://','postgresql://',1),timeout=10,command_timeout=10,
  server_settings={'application_name':'source-dedup-progress-readonly',
                   'default_transaction_read_only':'on','statement_timeout':'8s','lock_timeout':'2s'})
 try:
  backends=[dict(row) for row in await connection.fetch(
   'SELECT pid,application_name,state,wait_event_type,wait_event FROM pg_stat_activity '
   'WHERE application_name LIKE $1 ORDER BY application_name',prefix+'%')]
  try:
   tle_sessions={'verified':True,'rows':[dict(row) for row in await connection.fetch(
    'SELECT pid,application_name,state,wait_event_type,wait_event FROM pg_stat_activity '
    "WHERE application_name ~ '^tle-es-history-[0-5]$' ORDER BY application_name")]}
  except Exception as error:
   tle_sessions={'verified':False,'error_class':type(error).__name__}
 finally:await connection.close()
 tle_es=read_tle_es();tle_es['pg_sessions']=tle_sessions
 units=[{key:value[key] for key in ('unit','worker','ordinal','schema','table','leaf','old','new','data_type')}
        for value in plan['units']]
 print(json.dumps({'observed_at':datetime.now(timezone.utc).isoformat(),'run':str(ROOT),
  'plan_sha256':plan['plan_sha256'],'layout':layout,'stop_requested':(ROOT/'STOP').exists(),
  'units':units,'checkpoints':states,'worker_errors':errors,'backends':backends,'patent':read_patent(),
  'tle_es':tle_es,'tle_archive':read_tle_archive()}))
asyncio.run(main())
'''


def parse_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def tle_es_diagnostics(states, current_pass):
    value = {'diagnostics': None, 'diagnostics_error': None}
    fields = ('scan_cursor_seq_no', 'scan_documents', 'scan_global_checkpoint',
              'scroll_restarts', 'scroll_consecutive_failures', 'last_scroll_error')
    try:
        selected = []; events = []
        for state in states:
            if state['pass_number'] != current_pass or not any(key in state for key in fields[:3]):
                continue
            for key, minimum in zip(fields[:-1], (-1, 0, -1, 0, 0)):
                if type(state[key]) is not int or state[key] < minimum:
                    raise ValueError('Invalid sequence diagnostics')
            if ((state['scan_cursor_seq_no'] == -1) != (state['scan_documents'] == 0)
                    or state['scan_global_checkpoint'] < state['scan_cursor_seq_no']
                    or state['scan_documents'] > state['es_read']
                    or state['scroll_consecutive_failures'] > state['scroll_restarts']):
                raise ValueError('Inconsistent sequence diagnostics')
            error = state['last_scroll_error']
            if error is not None:
                if (not isinstance(error, dict) or error.get('type') != 'ESFailure'
                        or not isinstance(error.get('reason'), str) or not 1 <= len(error['reason']) <= 200
                        or any(ord(char) < 32 for char in error['reason'])
                        or parse_time(error['at']).tzinfo is None):
                    raise ValueError('Invalid sequence error event')
                events.append({'worker': state['worker'], **{key: error[key] for key in ('type', 'reason', 'at')}})
            selected.append(state)
        if selected:
            selected.sort(key=lambda state: state['worker'])
            value['diagnostics'] = {'workers': len(selected),
                'scan_documents': sum(state['scan_documents'] for state in selected),
                'worker_cursors': [{'worker': state['worker'], 'seq_no': state['scan_cursor_seq_no'],
                                    'global_checkpoint': state['scan_global_checkpoint']} for state in selected],
                'scroll_restarts': sum(state['scroll_restarts'] for state in selected),
                'retrying_workers': [{'worker': state['worker'], 'consecutive_failures': state['scroll_consecutive_failures']}
                                     for state in selected if state['scroll_consecutive_failures'] > 0],
                'last_scroll_error': max(events, key=lambda event: parse_time(event['at'])) if events else None}
        return value
    except (KeyError, TypeError, ValueError, AttributeError):
        return {**value, 'diagnostics': None, 'diagnostics_error': 'InvalidTleEsDiagnostics'}


def summarize_tle_es(raw, previous, sampled_at):
    value = {'verified': False, 'sampled_at': sampled_at, 'error_class': 'NotSampled',
             'delta': None, 'rows_per_minute': None, 'es_read_delta': None,
             'es_reads_per_minute': None, 'note': None}
    if not raw or not raw.get('verified'):
        return {**value, **{key: raw[key] for key in ('sampled_at', 'error_class') if raw and key in raw}}
    try:
        manifest = raw['manifest']
        run_id, workers, batch_size, total = (manifest[key] for key in (
            'run_id', 'workers', 'batch_size', 'total_documents'))
        initial = manifest.get('initial_documents_exact')
        fingerprint = raw['manifest_sha256']
        if (not isinstance(run_id, str) or not run_id or type(workers) is not int or workers != 6
                or type(batch_size) is not int or not 1 <= batch_size <= 3000
                or (total is not None and (type(total) is not int or total < 0))
                or (initial is not None and (type(initial) is not int or initial < 0))
                or not isinstance(fingerprint, str) or len(fingerprint) != 64
                or any(char not in '0123456789abcdef' for char in fingerprint)):
            raise ValueError('Invalid ES manifest')
        observed = parse_time(raw['sampled_at'])
        if observed.tzinfo is None:
            raise ValueError('ES sample timestamp has no timezone')
        counters = dict.fromkeys(('es_read', 'committed_documents', 'ods_inserted', 'ods_existing'), 0)
        seen = set(); errors = []; updates = []; worker_committed = [0] * workers
        passes = []
        for state in raw['checkpoints']:
            number = state['worker']
            counts = [state[key] for key in counters]
            updated = parse_time(state['updated_at'])
            if (type(number) is not int or not 0 <= number < workers or number in seen
                    or state['run_id'] != run_id or state['manifest_sha256'] != fingerprint
                    or any(type(count) is not int or count < 0 for count in counts)
                    or counts[0] < counts[1] or counts[1] != counts[2] + counts[3]
                    or type(state['pass_number']) is not int or state['pass_number'] < 1
                    or type(state['done']) is not bool or updated.tzinfo is None
                    or (state['last_error'] is not None and not isinstance(state['last_error'], (str, dict)))):
                raise ValueError('Invalid ES committed checkpoint')
            seen.add(number); passes.append(state['pass_number']); updates.append(updated)
            worker_committed[number] = state['committed_documents']
            for key in counters:
                counters[key] += state[key]
            error = state['last_error']
            if isinstance(error, dict):
                if not isinstance(error.get('type'), str) or not error['type'] or parse_time(error['at']).tzinfo is None:
                    raise ValueError('Invalid ES worker error metadata')
                error = f"{error['type']} @ {error['at']}"
            if error:
                errors.append((state['pass_number'], f"worker-{number}: {error}"))
        current_pass = max(passes, default=None)
        completed = sum(state['done'] and state['pass_number'] == current_pass for state in raw['checkpoints'])
        errors = [message for number, message in errors if number == current_pass]
        count = counters['committed_documents']
        if total is not None and (count > total or (completed == workers and count != total)):
            raise ValueError('ES committed coverage differs from manifest')
        stage = '错误' if errors else '待覆盖核对/补扫' if completed == workers else '待执行' if not seen else '已有检查点'
        result = raw.get('result')
        if result is not None:
            if (result['run_id'] != run_id or result['manifest_sha256'] != fingerprint
                    or type(result['pass_number']) is not int or result['pass_number'] < 1):
                raise ValueError('ES result binding differs')
            if result['pass_number'] == current_pass:
                exits = result['exitcodes']
                if (type(result['complete']) is not bool or not isinstance(exits, list) or len(exits) != workers
                        or any(type(code) is not int for code in exits)):
                    raise ValueError('Invalid ES result status')
                if any(code != 0 for code in exits):
                    stage = '错误'; errors.append('本轮 worker 退出失败')
                if result['complete']:
                    counts = [result[key] for key in ('source_count_before', 'source_count_after',
                              'source_count_after_archive_audit', 'unique_committed_documents', 'full_scan_documents')]
                    hashes = [state.get('file_sha256') for state in sorted(raw['checkpoints'], key=lambda row: row['worker'])]
                    if (completed != workers or errors or any(type(n) is not int or n != count for n in counts)
                            or any(type(result[key]) is not int or result[key] != counters[key]
                                   for key in ('ods_inserted', 'ods_existing'))
                            or result['worker_checkpoint_sha256'] != hashes or any(not digest for digest in hashes)
                            or result['source_consistency'] != 'live_index_bounded_query_reconciled_not_global_snapshot'):
                        raise ValueError('ES completion proof differs from checkpoints')
                    stage = '完成'
        value.update(verified=True, error_class=None, sampled_at=raw['sampled_at'], run_id=run_id,
                     manifest_sha256=fingerprint, workers=workers, batch_size=batch_size, total_documents=total,
                     initial_documents_exact=initial,
                     completed_workers=completed, checkpoint_workers=len(seen), worker_committed=worker_committed,
                     pass_number=current_pass,
                     latest_checkpoint_at=max(updates).isoformat() if updates else None, errors=errors, stage=stage,
                     completion_percent=round(count * 100 / total, 2) if total else 100 if total == 0 and completed == workers else None,
                     **counters)
        value.update(tle_es_diagnostics(raw['checkpoints'], current_pass))
        value.update(pg_sessions=None, pg_sessions_error='NotSampled')
        sessions = raw.get('pg_sessions') or {}
        if sessions.get('verified'):
            try:
                names = [f'tle-es-history-{number}' for number in range(workers)]
                selected = []; pids = set(); members = set()
                for row in sessions['rows']:
                    number = names.index(row['application_name'])
                    if (number in members or type(row['pid']) is not int or row['pid'] <= 0
                            or row['pid'] in pids or any(row[key] is not None and not isinstance(row[key], str)
                                                      for key in ('state', 'wait_event_type', 'wait_event'))):
                        raise ValueError('Invalid ES PG worker sessions')
                    members.add(number); pids.add(row['pid'])
                    selected.append({'worker': number, **{key: row[key] for key in
                                     ('pid', 'state', 'wait_event_type', 'wait_event')}})
                value.update(pg_sessions=sorted(selected, key=lambda row: row['worker']), pg_sessions_error=None)
            except (KeyError, TypeError, ValueError):
                value['pg_sessions_error'] = 'InvalidPgSessions'
        else:
            value['pg_sessions_error'] = sessions.get('error_class') or 'NotSampled'
        if (previous and previous.get('verified') and previous.get('run_id') == run_id
                and previous.get('manifest_sha256') == fingerprint):
            elapsed = (observed - parse_time(previous['sampled_at'])).total_seconds()
            if elapsed <= 0 or any(now < old for now, old in zip(worker_committed, previous['worker_committed'])):
                value['note'] = '采样时间未前进或提交计数回退，增量与速度未验证'
            else:
                difference = count - previous['committed_documents']
                value.update(delta=difference, rows_per_minute=round(difference * 60 / elapsed, 2))
                old_read = previous.get('es_read')
                if type(old_read) is int and 0 <= old_read <= counters['es_read']:
                    read_difference = counters['es_read'] - old_read
                    value.update(es_read_delta=read_difference,
                                 es_reads_per_minute=round(read_difference * 60 / elapsed, 2))
                if (value['stage'] == '已有检查点' and current_pass > 1 and difference == 0
                        and value['es_read_delta'] is not None and value['es_read_delta'] > 0):
                    value['stage'] = '恢复读取/核对（本轮无新增提交）'
        return value
    except (KeyError, TypeError, ValueError, AttributeError):
        return {'verified': False, 'sampled_at': sampled_at, 'error_class': 'InvalidTleEsState',
                'delta': None, 'rows_per_minute': None, 'es_read_delta': None,
                'es_reads_per_minute': None, 'note': None}


def summarize_patent(raw, previous, sampled_at):
    value = {'verified': False, 'sampled_at': sampled_at, 'run_id': PATENT_RUN_ID,
             'error_class': 'NotSampled', 'delta': None, 'rows_per_minute': None,
             'interval_seconds': None, 'note': None}
    if not raw or not raw.get('verified'):
        return {**value, **{key: raw[key] for key in ('sampled_at', 'error_class') if raw and key in raw}}
    try:
        if raw.get('run_id') != PATENT_RUN_ID:
            raise ValueError('Patent run binding differs')
        count, total, workers, completed, partitions, batch_size = (raw[key] for key in (
            'name_processed', 'total', 'workers', 'completed_workers', 'logical_partitions', 'batch_size'))
        if (any(type(number) is not int for number in (count, total, workers, completed, partitions, batch_size))
                or not 0 <= count <= total or total <= 0 or workers != 6 or partitions != 8
                or batch_size != 3000 or not 0 <= completed <= partitions):
            raise ValueError('Invalid patent naming counters')
        if type(raw.get('changed')) is not int or raw['changed'] < 0:
            raise ValueError('Invalid patent changed counter')
        if raw.get('active_workers') is not None and (
                type(raw['active_workers']) is not int or not 0 <= raw['active_workers'] <= workers):
            raise ValueError('Invalid patent active worker count')
        if not isinstance(raw.get('observed_at'), str) or not isinstance(raw.get('sampled_at'), str):
            raise ValueError('Invalid patent timestamp')
        observed = parse_time(raw['observed_at'])
        if observed.tzinfo is None:
            raise ValueError('Patent state timestamp has no timezone')
        value.update({key: raw.get(key) for key in ('sampled_at', 'observed_at', 'run_id', 'stage',
                     'name_processed', 'changed', 'total', 'active_workers', 'workers', 'logical_partitions',
                     'batch_size', 'cdc_active', 'last_error', 'completed_workers')})
        value.update(verified=True, error_class=None, completion_percent=round(count * 100 / total, 2))
        if previous and previous.get('verified') and previous.get('run_id') == value['run_id']:
            elapsed = (observed - parse_time(previous['observed_at'])).total_seconds()
            difference = count - previous['name_processed']
            if elapsed <= 0:
                value['note'] = '状态时间未前进，本轮新增与速度未验证'
            elif difference < 0:
                value['note'] = '命名计数回退，本轮新增与速度未验证'
            else:
                value.update(interval_seconds=round(elapsed, 2), delta=difference,
                             rows_per_minute=round(difference * 60 / elapsed, 2))
        return value
    except (KeyError, TypeError, ValueError):
        return {'verified': False, 'sampled_at': sampled_at, 'run_id': PATENT_RUN_ID,
                'error_class': 'InvalidPatentState', 'delta': None, 'rows_per_minute': None,
                'interval_seconds': None, 'note': None}


def summarize(snapshot, previous=None):
    """Count only committed checkpoints; never treat Kafka/Mongo progress as PG coverage."""
    units = snapshot['units']
    by_unit = {unit['unit']: unit for unit in units}
    if len(by_unit) != len(units):
        raise ValueError('Duplicate repair units')
    states = {}
    for state in snapshot['checkpoints']:
        number = state['unit']
        if number not in by_unit or number in states:
            raise ValueError('Unknown or duplicate checkpoint unit')
        counts = [state.get(key) or 0 for key in ('processed', 'renamed', 'deduplicated')]
        if any(type(value) is not int or value < 0 for value in counts) or counts[0] != sum(counts[1:]):
            raise ValueError('Checkpoint committed counters differ')
        states[number] = state
    configured_workers = int(snapshot['layout']['workers'])
    active_workers = set()
    for backend in snapshot['backends']:
        name = backend['application_name']
        number = int(name.rsplit('-worker-', 1)[1])
        if not 0 <= number < configured_workers or number in active_workers:
            raise ValueError('Worker sessions disagree with the current layout')
        active_workers.add(number)
    # Layout changes reassign immutable units; old checkpoint.worker is historical.
    current = {}
    for unit in sorted(units, key=lambda value: value['unit']):
        owner = unit_owner(unit, snapshot['layout'])
        if owner is not None and owner in active_workers and owner not in current and not states.get(unit['unit'], {}).get('done'):
            current[owner] = unit
    elapsed = None
    old_rows = {}
    if (previous and previous.get('run') == snapshot['run'] and
            previous.get('plan_sha256') == snapshot['plan_sha256']):
        elapsed = (parse_time(snapshot['observed_at']) - parse_time(previous['observed_at'])).total_seconds()
        if elapsed <= 0:
            elapsed = None
        else:
            old_rows = {row['table']: row for row in previous['rows']}
    rows = []
    current_errors = list(snapshot.get('worker_errors') or [])
    for schema, table, scope, layer in TABLES:
        selected = [unit for unit in units if (unit['schema'], unit['table']) == (schema, table)]
        selected_states = [states[unit['unit']] for unit in selected if unit['unit'] in states]
        counts = {key: sum(state.get(key) or 0 for state in selected_states)
                  for key in ('processed', 'renamed', 'deduplicated')}
        partitions = {}
        for unit in selected:
            partitions.setdefault(unit['leaf'], []).append(unit['unit'])
        partitions_done = sum(all(states.get(number, {}).get('done') for number in numbers)
                              for numbers in partitions.values())
        workers = sorted(owner for owner, unit in current.items()
                         if (unit['schema'], unit['table']) == (schema, table))
        unit_ids = {unit['unit'] for unit in selected}
        failures = [{'unit': state['unit'], 'worker': state.get('worker'), 'type': state['last_error']}
                    for state in selected_states if state.get('last_error')]
        failures += [{'unit': error.get('unit'), 'worker': error.get('worker'),
                      'type': error.get('last_error') or 'WorkerError'}
                     for error in current_errors if error.get('unit') in unit_ids or error.get('worker') in workers]
        if not selected:
            stage = '范围未验证'
        elif partitions_done == len(partitions):
            stage = '完成'
        elif failures:
            stage = '错误'
        elif workers:
            stage = '暂停中' if snapshot.get('stop_requested') else '修复中'
        elif snapshot.get('stop_requested'):
            stage = '暂停' if selected_states else '待执行'
        else:
            stage = '待执行' if not selected_states else '无运行会话'
        full_name = schema + '.' + table
        delta = None
        note = None
        if elapsed and full_name in old_rows:
            difference = counts['processed'] - old_rows[full_name]['processed']
            if difference >= 0:
                delta = difference
            else:
                note = '计数回退，增量未验证'
        rows.append({'scope': scope, 'layer': layer, 'table': full_name, 'stage': stage, **counts,
                     'delta': delta, 'rows_per_minute': round(delta * 60 / elapsed, 2) if delta is not None else None,
                     'partitions_done': partitions_done, 'partitions': len(partitions),
                     'units_done': sum(bool(state.get('done')) for state in selected_states), 'units': len(selected),
                     'workers': workers, 'errors': failures, 'pending': sum(bool(state.get('pending')) for state in selected_states),
                     'old_rows_total': None, 'completion_percent': None, 'eta': None, 'note': note})
    return {'observed_at': snapshot['observed_at'], 'run': snapshot['run'],
            'plan_sha256': snapshot['plan_sha256'], 'layout': snapshot['layout'],
            'stop_requested': snapshot.get('stop_requested', False),
            'interval_seconds': round(elapsed, 2) if elapsed else None,
            'workers_connected': len(active_workers), 'rows': rows,
            'totals': {key: sum(row[key] for row in rows)
                       for key in ('processed', 'renamed', 'deduplicated', 'pending')},
            'worker_errors': current_errors,
            'patent': summarize_patent(snapshot.get('patent'), previous.get('patent') if previous else None,
                                       snapshot['observed_at']),
            'tle_es': summarize_tle_es(snapshot.get('tle_es'), previous.get('tle_es') if previous else None,
                                      snapshot['observed_at']),
            'tle_archive': snapshot.get('tle_archive') or {'verified': False, 'error_class': 'NotSampled'}}


def tle_archive_report(value, markdown_format=False):
    lines = ['', 'TLE / Mike 原始归档补缺（附加补录验收，不代表主迁移进度）']
    if not value.get('verified'):
        status = '待执行' if value.get('error_class') == 'NotStarted' else '未验证'
        lines.append(f"状态：{status}；计划 38 条；{value.get('error_class') or 'UnknownError'}。")
    else:
        headers = ('状态', '计划', '已提交', 'ODS 新插入', 'ODS 幂等命中', '完成 worker', '验收补回日期', '错误')
        cells = (value['stage'], '38', str(value['committed_documents']), str(value['ods_inserted']),
                 str(value['ods_existing']), f"{value['completed_workers']}/6", str(value['restored_days']),
                 ', '.join(value['errors']) or '无')
        if markdown_format:
            lines += ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |',
                      '| ' + ' | '.join(cells) + ' |']
        else:
            lines.append('；'.join(f'{key}：{cell}' for key, cell in zip(headers, cells)) + '。')
    if value.get('recoverable_days') == 1 and value.get('days_without_evidence') == 53:
        lines.append('原归档可补 1 日（2025-01-22）；其余 53 日在已审计目录中无归档证据，保留缺口。')
    else:
        lines.append('归档缺口审计未验证。')
    return '\n'.join(lines)


def tle_es_report(value, markdown_format=False):
    lines = ['', 'TLE / ES 历史回灌 → ODS（独立于 PG 修复计数）']
    sampled = parse_time(value['sampled_at']).astimezone(BEIJING).strftime('%Y-%m-%d %H:%M:%S')
    if not value.get('verified'):
        status = '待执行（尚无 manifest）' if value.get('error_class') == 'NotStarted' else '未验证'
        return '\n'.join(lines + [f"采样时间：{sampled}；{status}：{value.get('error_class') or 'UnknownError'}。"])
    total = f"{value['total_documents']:,}" if value['total_documents'] is not None else '未核定'
    percent = f"{value['completion_percent']:.2f}%" if value['completion_percent'] is not None else '未核定'
    delta = f"{value['delta']:,}" if value['delta'] is not None else '—'
    rate = f"{value['rows_per_minute']:,.1f}" if value['rows_per_minute'] is not None else '—'
    headers = ('阶段', 'ES 物理读取', 'ODS 已提交文档/总量', '完成率', 'ODS 新插入', 'ODS 幂等命中',
               '本轮提交', '提交/分钟', '完成 worker', '错误')
    cells = (value['stage'], f"{value['es_read']:,}", f"{value['committed_documents']:,}/{total}", percent,
             f"{value['ods_inserted']:,}", f"{value['ods_existing']:,}", delta, rate,
             f"{value['completed_workers']}/{value['workers']}", '; '.join(value['errors']) or '无')
    lines.append(f"采样时间：{sampled}；run_id：{value['run_id']}；每批 ≤{value['batch_size']:,} 条。")
    if value.get('initial_documents_exact') is not None:
        lines.append(f"初始精确基准：{value['initial_documents_exact']:,} 文档；活动索引非同一全局快照，基准不作为总完成率分母。")
    if markdown_format:
        escaped = [str(cell).replace('|', '\\|').replace('\n', ' ') for cell in cells]
        lines += ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |',
                  '| ' + ' | '.join(escaped) + ' |']
    else:
        width_of = lambda text: sum(2 if unicodedata.east_asian_width(char) in 'WF' else 1 for char in text)
        widths = [max(width_of(header), width_of(cell)) for header, cell in zip(headers, cells)]
        border = '+' + '+'.join('-' * (width + 2) for width in widths) + '+'
        line = lambda values: '| ' + ' | '.join(cell + ' ' * (width - width_of(cell)) for cell, width in zip(values, widths)) + ' |'
        lines += [border, line(headers), border, line(cells), border]
    lines.append(f"已读取 {value['checkpoint_workers']}/6 份检查点；运行进程数未核验。"
                 '物理读取含重扫；进度只计 PG 提交后的文档覆盖，ODS 新插入与幂等命中之和等于已提交文档。')
    read_delta = f"{value['es_read_delta']:,}" if value.get('es_read_delta') is not None else '—'
    read_rate = f"{value['es_reads_per_minute']:,.1f}" if value.get('es_reads_per_minute') is not None else '—'
    lines.append(f'本轮 ES 读取计数新增 {read_delta}；读取 {read_rate} 条/分钟（含重扫，不计入 ODS 已提交量）。')
    diagnostics = value.get('diagnostics')
    if value.get('diagnostics_error'):
        lines.append('ES 游标诊断未验证：' + value['diagnostics_error'] + '；ODS 已提交量仍独立验证。')
    elif diagnostics:
        cursors = '; '.join(f"worker-{row['worker']}={row['seq_no']}/{row['global_checkpoint']}" for row in diagnostics['worker_cursors'])
        retrying = '; '.join(f"worker-{row['worker']}={row['consecutive_failures']} 次" for row in diagnostics['retrying_workers']) or '无'
        event = diagnostics['last_scroll_error']
        history = f"worker-{event['worker']}: {event['reason']} @ {event['at']}（历史保留）" if event else '无'
        lines.append(f"当前 pass {value['pass_number']} 已完整核对读取 {diagnostics['scan_documents']:,} 条（{diagnostics['workers']}/6 worker，不计入 ODS 提交量）；游标/ES 确认序号：{cursors}（序号不是条数）。")
        lines.append(f"扫描上下文累计重建 {diagnostics['scroll_restarts']:,} 次；当前连续失败：{retrying}；最近重建记录：{history}。")
    sessions = value.get('pg_sessions')
    if sessions is None:
        lines.append('PG 工作会话 未核验/6：' + (value.get('pg_sessions_error') or 'UnknownError') + '。')
    else:
        waits = '; '.join(f"worker-{row['worker']}={row['wait_event_type'] or '无等待'}/{row['wait_event'] or '—'}"
                          for row in sessions) or '无会话'
        lines.append(f'PG 工作会话 {len(sessions)}/6；等待：{waits}。此数仅代表 PG 会话。')
    if value.get('latest_checkpoint_at'):
        lines.append('最近检查点：' + value['latest_checkpoint_at'] + '；检查点时间不代表进程仍在运行。')
    if value.get('note'):
        lines.append(value['note'] + '。')
    lines.append('本轮 ES→ODS 历史导入完成即退出；后续采集走 collector→RDS→ODS，不依赖 ES。')
    return '\n'.join(lines)


def patent_report(patent, markdown_format=False):
    lines = ['', 'patent / Mongo 命名修正（独立于 PG 修复计数）']
    sampled = parse_time(patent['sampled_at']).astimezone(BEIJING).strftime('%Y-%m-%d %H:%M:%S')
    if not patent.get('verified'):
        return '\n'.join(lines + [f"采样时间：{sampled}；未验证：{patent.get('error_class') or 'UnknownError'}。"])
    observed = parse_time(patent['observed_at']).astimezone(BEIJING).strftime('%Y-%m-%d %H:%M:%S')
    delta = f"{patent['delta']:,}" if patent['delta'] is not None else '—'
    rate = f"{patent['rows_per_minute']:,.1f}" if patent['rows_per_minute'] is not None else '—'
    active = str(patent['active_workers']) if patent['active_workers'] is not None else '未验证'
    cdc = '是' if patent['cdc_active'] is True else '否' if patent['cdc_active'] is False else '未验证'
    headers = ('阶段', '命名已处理/总量', '完成率', '本轮新增', '条/分钟', '活跃/配置 worker', '完成命名分区', 'CDC', '错误')
    cells = (str(patent['stage']), f"{patent['name_processed']:,}/{patent['total']:,}",
             f"{patent['completion_percent']:.2f}%", delta, rate, f"{active}/{patent['workers']}",
             f"{patent['completed_workers']}/{patent['logical_partitions']}", cdc, str(patent['last_error'] or '无'))
    lines.append(f"采样时间：{sampled}；patent 状态时间：{observed}（活跃 worker 数取自该状态）；"
                 f"每批 {patent['batch_size']:,} 条，{patent['logical_partitions']} 个逻辑分区。")
    if markdown_format:
        lines += ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |',
                  '| ' + ' | '.join(cells) + ' |']
    else:
        width_of = lambda text: sum(2 if unicodedata.east_asian_width(char) in 'WF' else 1 for char in text)
        widths = [max(width_of(header), width_of(cell)) for header, cell in zip(headers, cells)]
        border = '+' + '+'.join('-' * (width + 2) for width in widths) + '+'
        line = lambda values: '| ' + ' | '.join(cell + ' ' * (width - width_of(cell)) for cell, width in zip(values, widths)) + ' |'
        lines += [border, line(headers), border, line(cells), border]
    lines.append(f"state.changed（当前阶段实际修改）：{patent['changed']:,}；命名进度始终使用 name_processed。")
    if patent.get('interval_seconds'):
        lines.append(f"命名增量按两次 patent 状态时间计算，间隔 {patent['interval_seconds'] / 60:.1f} 分钟。")
    if patent.get('note'):
        lines.append(patent['note'] + '。')
    return '\n'.join(lines)


def tle_summary(value, markdown_format=False):
    time = parse_time(value['observed_at']).astimezone(BEIJING).strftime('%Y-%m-%d %H:%M:%S')
    lines = [f'TLE→ODS 导入进度（北京时间 {time}，主迁移与附加补录分别统计）']
    headers = ('任务', '来源→目标', 'ODS 已提交', '状态')
    rows = []
    for key, label, source in (('tle_es', '主迁移', 'ES→ODS'),
                               ('tle_archive', '附加归档补录', '原始归档→ODS')):
        state = value.get(key) or {}
        verified = state.get('verified') is True
        count = f"{state['committed_documents']:,}" if verified else '未验证'
        stage = state['stage'] if verified else '待执行' if state.get('error_class') == 'NotStarted' else '未验证'
        rows.append((label, source, count, stage))
    if markdown_format:
        lines += ['| ' + ' | '.join(headers) + ' |', '|---|---|---:|---|']
        lines.extend('| ' + ' | '.join(row) + ' |' for row in rows)
    else:
        lines.append(' | '.join(headers))
        lines.extend(' | '.join(row) for row in rows)
    return '\n'.join(lines)


def markdown(value):
    time = parse_time(value['observed_at']).astimezone(BEIJING).strftime('%Y-%m-%d %H:%M:%S')
    layout = value['layout']
    interval = value.get('interval_seconds')
    lines = [tle_summary(value, markdown_format=True), '',
             f"北京时间 {time}；实际 worker 会话 {value['workers_connected']}/{layout['workers']}，"
             f"每轮读取 {layout['page_size']:,} 键，SQL 子批 {layout['sql_batch_size']:,} 条。"]
    lines.append(f'本轮与上次成功采样相隔 {interval / 60:.1f} 分钟。' if interval else '首次采样，本轮新增与速度尚无基线。')
    lines += ['', '| 范围/层 | 状态 | 已改源 | 已去重 | 累计处理 | 本轮新增 | 条/分钟 | 完成分区 | worker 会话 | 错误 |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|---|']
    for row in value['rows']:
        delta = f"{row['delta']:,}" if row['delta'] is not None else '—'
        rate = f"{row['rows_per_minute']:,.1f}" if row['rows_per_minute'] is not None else '—'
        errors = ', '.join(sorted({str(error['type']) for error in row['errors']})) or '无'
        if row.get('note'):
            errors = row['note']
        lines.append(f"| {row['scope']}/{row['layer']} | {row['stage']} | {row['renamed']:,} | "
                     f"{row['deduplicated']:,} | {row['processed']:,} | {delta} | {rate} | "
                     f"{row['partitions_done']}/{row['partitions']} | {len(row['workers'])} | {errors} |")
    total = value['totals']
    lines += ['', f"合计：已处理 {total['processed']:,} 行，改源 {total['renamed']:,} 行，"
              f"去重 {total['deduplicated']:,} 行，待确认事务 {total['pending']}。",
              '旧数据总量：未核定；全量完成率：未核定；ETA：—。',
              '计数来自已提交 PG 修复断点，跨 RDS/ODS 的合计为处理行数，不是唯一业务记录数。'
              '完成分区要求该分区下全部旧来源单元均已完成；worker 会话按本次运行的 PG 连接核对。']
    if value.get('worker_errors'):
        lines.append('运行目录存在本次启动后的 worker 错误回执，请核对日志。')
    if value.get('patent'):
        lines.append(patent_report(value['patent'], markdown_format=True))
    if value.get('tle_archive'):
        lines.append(tle_archive_report(value['tle_archive'], markdown_format=True))
    if value.get('tle_es'):
        lines.append(tle_es_report(value['tle_es'], markdown_format=True))
    return '\n'.join(lines)


def plain_table(value):
    """Render a table that remains readable when the caller strips Markdown."""
    def display_width(text):
        return sum(2 if unicodedata.east_asian_width(char) in 'WF' else 1 for char in text)

    def pad(text, width, right=False):
        spaces = ' ' * max(0, width - display_width(text))
        return spaces + text if right else text + spaces

    time = parse_time(value['observed_at']).astimezone(BEIJING).strftime('%Y-%m-%d %H:%M:%S')
    layout = value['layout']
    lines = [
        tle_summary(value), '',
        f"北京时间 {time}；实际 worker 会话 {value['workers_connected']}/{layout['workers']}；"
        f"每轮读取 {layout['page_size']:,} 键；SQL 子批 {layout['sql_batch_size']:,} 条。",
        "",
    ]
    headers = ('范围/层', '状态', '已改源', '已删旧重复', '累计处理', '本轮新增', '条/分钟', '完成分区/总分区')
    rows = []
    for row in value['rows']:
        rows.append((
            f"{row['scope']}/{row['layer']}", row['stage'], f"{row['renamed']:,}",
            f"{row['deduplicated']:,}", f"{row['processed']:,}",
            f"{row['delta']:,}" if row['delta'] is not None else '—',
            f"{row['rows_per_minute']:,.1f}" if row['rows_per_minute'] is not None else '—',
            f"{row['partitions_done']}/{row['partitions']}",
        ))
    widths = [max(display_width(header), *(display_width(row[index]) for row in rows))
              for index, header in enumerate(headers)]
    border = '+' + '+'.join('-' * (width + 2) for width in widths) + '+'
    def render(row):
        return '| ' + ' | '.join(pad(cell, width, right=index >= 2)
                                  for index, (cell, width) in enumerate(zip(row, widths))) + ' |'
    lines.extend([border, render(headers), border])
    lines.extend(render(row) for row in rows)
    lines.extend([border, '', '旧数据总量：未核定；全量完成率：未核定；ETA：—。'])
    errors = sorted({str(error.get('last_error') or 'WorkerError') for error in value.get('worker_errors') or []})
    lines.append('当前错误/瓶颈：' + (', '.join(errors) if errors else '无。'))
    if value.get('patent'):
        lines.append(patent_report(value['patent']))
    if value.get('tle_archive'):
        lines.append(tle_archive_report(value['tle_archive']))
    if value.get('tle_es'):
        lines.append(tle_es_report(value['tle_es']))
    return '\n'.join(lines)


async def collect():
    sys.path.insert(0, str(HERE.parent / 'twitter_schedule_20261027'))
    from ssh_inspect import connect, credentials
    host = ('import subprocess\n'
            "p=subprocess.run(['docker','exec','-i','-u','0','-e','PYTHONPATH=/app','web','python','-B','-'],"
            f'input={PROBE!r},text=True,capture_output=True,timeout=45)\n'
            "if p.returncode:raise RuntimeError(p.stderr[-1800:])\nprint(p.stdout,end='')\n")
    client = await connect()
    try:
        result = await client.run("sudo -S -p '' python3 -B -", input=credentials()[1] + '\n' + host,
                                  check=False, timeout=60)
    finally:
        client.close(); await client.wait_closed()
    if result.exit_status:
        raise RuntimeError('只读进度查询未验证：' + result.stderr[-1800:])
    return json.loads(result.stdout)


def save(path, value):
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    os.replace(temporary, path)


async def main(as_json=False, as_markdown=False):
    last = HERE / 'progress-table-last.json'
    previous = json.loads(last.read_text(encoding='utf8')) if last.exists() else None
    value = summarize(await collect(), previous)
    stamp = parse_time(value['observed_at']).strftime('%Y%m%dT%H%M%SZ')
    receipt = HERE / ('progress-table-' + stamp + '.json')
    value['receipt'] = str(receipt)
    save(receipt, value)
    # Never replace a newer baseline with an older concurrent observation.
    current = json.loads(last.read_text(encoding='utf8')) if last.exists() else None
    if not current or parse_time(value['observed_at']) > parse_time(current['observed_at']):
        latest = HERE / 'progress-table-latest.md'
        temporary = latest.with_name(latest.name + f'.{os.getpid()}.tmp')
        temporary.write_text(markdown(value), encoding='utf8')
        save(last, value)
        os.replace(temporary, latest)
    print(json.dumps(value, ensure_ascii=False, separators=(',', ':')) if as_json
          else markdown(value) if as_markdown else plain_table(value))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf8')
    parser = argparse.ArgumentParser(description=__doc__)
    output = parser.add_mutually_exclusive_group()
    output.add_argument('--json', action='store_true', help='Print compact JSON.')
    output.add_argument('--markdown', action='store_true', help='Print Markdown tables.')
    args = parser.parse_args()
    asyncio.run(main(args.json, args.markdown))
