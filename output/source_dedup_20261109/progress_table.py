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
BEIJING = timezone(timedelta(hours=8))
TABLES = (
    ('ts_rds', 'rds_filing', 'filing', 'RDS'),
    ('ts_ods', 'ods_filing', 'filing', 'ODS'),
    ('ts_ods', 'ods_filing_document', 'document', 'ODS'),
)

PROBE = r'''

import asyncio,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
import asyncpg
from app.storage.postgres_client import _resolved_pg_url

ROOT=Path('/app/output/source_dedup_20261109/sec-history-run01')

def encoded(value):
 return (json.dumps(value,sort_keys=True,ensure_ascii=False,indent=2)+'\n').encode()
def need(value):
 if not value:raise ValueError('Migration checkpoint binding differs')
def fingerprint(value):
 return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)
def local_file(root,relative,folder):
 path=(root/relative).resolve();need(path.parent==(root/folder).resolve());return path
def read_committed_worker(root,plan,number,part,expected=None):
 sha=plan['plan_sha256'];cp=json.loads((root/('worker-'+str(number)+'.json')).read_bytes());count=cp['processed'];cursor=cp['cursor']
 need(cp['plan_sha256']==sha and type(cp['worker']) is int and cp['worker']==number
   and type(count) is int and count>=0 and type(cp['done']) is bool and (count==0)==(cursor is None))
 if cursor:need(part['lower']<=cursor<=(part.get('upper_inclusive') or 'f'*24)
   and (not part.get('upper') or cursor<part['upper']))
 if expected is not None:need(count<=expected and (not cp['done'] or (count==expected and cursor==part['upper_inclusive'])))
 if count:
  receipt=json.loads(local_file(root,cp['last_receipt'],'transactions').read_bytes());ids=receipt['record_ids']
  need(receipt['committed'] is True and receipt['protocol']==plan['protocol'] and receipt['plan_sha256']==sha
    and receipt['mongo_uuid']==plan['mongo_uuid'] and receipt['worker']==number and receipt['part']==part
    and receipt['receipt']==cp['last_receipt'] and receipt['last_id']==cursor
    and type(receipt['rows']) is int and 0<receipt['rows']<=(300 if plan['kind']=='facts' else 3000)
    and type(receipt['previous_processed']) is int and receipt['previous_processed']>=0
    and receipt['previous_processed']+receipt['rows']==count
    and len(ids)==len(set(ids))==receipt['rows'])
  for layer in ('rds','ods'):
   values=receipt['after_sha256'][layer];need(set(values)==set(ids) and all(fingerprint(v) for v in values.values()))
  if receipt.get('archive'):
   archive=receipt['archive'];need(fingerprint(archive['sha256']) and type(archive['rows']) is int and archive['rows']>0)
   need(hashlib.sha256(local_file(root,archive['path'],'transactions').read_bytes()).hexdigest()==archive['sha256'])
 return cp
def read_facts_repair():
 try:
  base=Path('/app/output/sec_migration_20261104');root=base/'facts-source-safe-20261116'
  plan=json.loads((root/'plan.json').read_bytes());sha=plan['plan_sha256']
  owner=json.loads((base/'source-safe-current-owners.json').read_bytes())['facts_history_repair']
  need(owner['root']=='/data/clawer/output/sec_migration_20261104/'+root.name
    and owner['plan_sha256']==sha and fingerprint(owner['source_sha256']))
  need(fingerprint(sha) and hashlib.sha256(encoded({k:v for k,v in plan.items() if k!='plan_sha256'})).hexdigest()==sha
    and plan['protocol']=='source_safe_mongo_migration_v1' and plan['kind']=='facts'
    and plan['collection']=='financial_fact_sec_edgar' and plan['data_type']=='financial_fact'
    and len(plan['parts'])==6 and [p['worker'] for p in plan['parts']]==list(range(6)))
  manifest_path=Path(plan['manifest_path']);raw=manifest_path.read_bytes()
  need(manifest_path.parent==base/'facts-burst-evidence-20261116' and hashlib.sha256(raw).hexdigest()==plan['manifest_sha256'])
  manifest=json.loads(raw);need(manifest['protocol']=='facts_burst_checkpoint_manifest_v1' and manifest['total']==78600
    and manifest['mongo_uuid_hex']==plan['mongo_uuid'] and sorted(manifest['parts'],key=lambda p:p['worker'])==plan['parts'])
  summary=json.loads((root/'state.json').read_bytes())
  need(summary['protocol']==plan['protocol'] and summary['kind']=='facts' and summary['plan_sha256']==sha
    and summary['mongo_uuid']==plan['mongo_uuid'] and summary['stage']=='complete' and summary['processed']==78600
    and summary['cdc_replay'] is False and not summary.get('last_error'))
  states=[read_committed_worker(root,plan,n,p,p['actual_count']) for n,p in enumerate(plan['parts'])]
  need(sum(s['processed'] for s in states)==78600 and all(s['done'] and not s.get('pending') and not s.get('last_error') for s in states))
  return {'verified':True,'processed':78600,'stage':'complete','plan_sha256':sha}
 except Exception as error:
  return {'verified':False,'error_class':type(error).__name__}
def read_migration(kind):
 sampled=datetime.now(timezone.utc).isoformat()
 try:
  base=Path('/app/output/sec_migration_20261104');root=base/('facts-migration-20261116' if kind=='facts' else 'patent-source-safe-20261116')
  plan_kind='facts_full' if kind=='facts' else 'patent'
  owner=json.loads((base/'source-safe-current-owners.json').read_bytes())[kind]
  root_name=Path(owner['root']).name
  need(root_name.startswith('facts-migration-20261116' if kind=='facts' else 'patent-source-safe-20261116'))
  root=base/root_name
  need(owner['root']=='/data/clawer/output/sec_migration_20261104/'+root.name
    and (owner['workers'],owner['page_size']) in ((6,3000),(5,8000))
    and owner['mongo_cdc_replay'] is False and fingerprint(owner['source_sha256'])
    and owner['container'].startswith(('source-safe-facts-full-20261116' if kind=='facts' else 'source-safe-patent-20261116'))
    and isinstance(owner['image'],str))
  plan=json.loads((root/'plan.json').read_bytes());sha=plan['plan_sha256']
  need(fingerprint(sha) and sha==owner['plan_sha256']
    and hashlib.sha256(encoded({k:v for k,v in plan.items() if k!='plan_sha256'})).hexdigest()==sha
    and plan['protocol']=='source_safe_mongo_migration_v1' and plan['kind']==plan_kind
    and plan['collection']==('financial_fact_sec_edgar' if kind=='facts' else 'patent_google')
    and plan['data_type']==('financial_fact' if kind=='facts' else 'patent')
    and plan['aliases']==(['sec_edgar_financial_fact','sec_edgar_company'] if kind=='facts' else ['google_patent'])
    and plan['workers']==6 and plan['page_size']==3000 and plan['sql_batch_size']==300
    and len(plan['parts'])==6 and [p['worker'] for p in plan['parts']]==list(range(6)))
  if kind=='facts':need(plan.get('manifest_path') is None and plan.get('manifest_sha256') is None)
  layout_path=root/'layout.json'
  layout=json.loads(layout_path.read_bytes()) if layout_path.exists() else {
   'plan_sha256':sha,'workers':6,'page_size':3000,'sql_batch_size':300}
  batch=layout['sql_batch_size']
  need(layout['plan_sha256']==sha and type(layout['workers']) is int and layout['workers']==owner['workers']
    and type(layout['page_size']) is int and layout['page_size']==owner['page_size']
    and type(batch) is int and 1<=batch<=3000
    and type(owner['sql_batch_size']) is int and owner['sql_batch_size']==batch)
  summary=json.loads((root/'state.json').read_bytes())
  need(summary['protocol']==plan['protocol'] and summary['kind']==plan_kind and summary['plan_sha256']==sha
    and summary['mongo_uuid']==plan['mongo_uuid'] and summary['cdc_replay'] is False
    and summary['workers']==layout['workers'] and summary['page_size']==layout['page_size']
    and type(summary['sql_batch_size']) is int and summary['sql_batch_size']==batch
    and summary['stage'] in ('running','complete','failed','paused','canary_complete','pg_alias_tail','mongo_coverage_complete'))
  states=[read_committed_worker(root,plan,n,p) for n,p in enumerate(plan['parts'])]
  count=sum(s['processed'] for s in states);done=sum(s['done'] for s in states)
  ancestor=plan;seen={str(root)}
  while ancestor.get('continuation'):
   lineage=ancestor['continuation'];parent=Path(lineage['parent_root']);parent_plan=lineage['parent_plan']
   need(parent.parent==base and parent.name.startswith('facts-migration-20261116' if kind=='facts' else 'patent-source-safe-20261116')
     and str(parent) not in seen and (parent/'STOP').is_file()
     and json.loads((parent/'plan.json').read_bytes())==parent_plan
     and parent_plan['kind']==plan_kind
     and all(parent_plan[k]==plan[k] for k in ('protocol','collection','data_type','aliases','mongo_uuid'))
     and hashlib.sha256(encoded({k:v for k,v in parent_plan.items() if k!='plan_sha256'})).hexdigest()==parent_plan['plan_sha256'])
   previous=[read_committed_worker(parent,parent_plan,n,p) for n,p in enumerate(parent_plan['parts'])]
   offset=sum(s['processed'] for s in previous)
   need(previous==lineage['checkpoints']
     and offset+parent_plan.get('continuation',{}).get('processed_offset',0)==lineage['processed_offset']
     and all(not s.get('pending') and not s.get('last_error') for s in previous))
   count+=offset
   seen.add(str(parent));ancestor=parent_plan
  need(type(summary['processed']) is int and 0<=summary['processed']<=count)
  if summary['stage']=='complete':need(done==6)
  tail=None
  if (summary['stage']=='pg_alias_tail' or summary.get('pg_only_alias_sweep_complete') is True
    or (done==6 and summary['stage'] in ('paused','failed') and (root/'pg-alias-tail'/'plan.json').exists())):
   need(done==6);directory=root/'pg-alias-tail';raw=(directory/'scope.json').read_bytes()
   scope=json.loads(raw);tail_plan=json.loads((directory/'plan.json').read_bytes());tail_sha=hashlib.sha256(raw).hexdigest()
   expected={(old,'ts_'+layer,layer+'_'+plan['data_type'],n) for old in plan['aliases'] for layer in ('rds','ods') for n in range(32)}
   need(scope['protocol']=='source_safe_'+plan_kind+'_alias_tail_v1' and scope['mongo_plan_sha256']==sha
      and tail_plan['plan_sha256']==tail_sha and tail_plan['workers']==6 and tail_plan['batch_size']==300
      and tail_plan.get('guard_table') is None and len(tail_plan['units'])==len(expected)
      and {u['unit'] for u in tail_plan['units']}==set(range(len(expected)))
      and {(u['old'],u['schema'],u['table'],u['ordinal']) for u in tail_plan['units']}==expected)
   tail={'rds':0,'ods':0,'done':0,'pending':0,'units':len(expected)}
   for unit in tail_plan['units']:
    need(unit['old'] in plan['aliases'] and unit['new']==plan['collection'] and unit['data_type']==plan['data_type']
      and unit['schema']=='ts_'+unit['layer'] and unit['table']==unit['layer']+'_'+plan['data_type'])
    path=directory/'checkpoints'/('unit-'+str(unit['unit'])+'.json')
    if not path.exists():continue
    cp=json.loads(path.read_bytes());n=cp['processed']
    need(cp['unit']==unit['unit'] and cp['plan_sha256']==tail_sha and type(n) is int and n>=0
      and n==cp['renamed']+cp['deduplicated'])
    tail[unit['layer']]+=n;tail['done']+=cp.get('done') is True;tail['pending']+=bool(cp.get('pending'))
   if summary.get('pg_only_alias_sweep_complete') is True:need(tail['done']==len(expected) and not tail['pending'])
  if summary['stage']=='complete':need(summary.get('pg_only_alias_sweep_complete') is True)
  return {'verified':True,'sampled_at':sampled,'kind':kind,'owner':owner,'root':str(root),'plan_sha256':sha,
    'mongo_uuid':plan['mongo_uuid'],'processed':count,'total':None,'workers_done':done,'workers':layout['workers'],
    'worker_processed':[s['processed'] for s in states],'pending':sum(bool(s.get('pending')) for s in states),
    'errors':list(dict.fromkeys([s['last_error'] for s in states if s.get('last_error')]+([summary['last_error']] if summary.get('last_error') else []))),
    'stage':summary['stage'],'sql_batch_size':300 if summary['stage']=='pg_alias_tail' else batch,
    'cdc_replay':False,'tail':tail}
 except Exception as error:
  return {'verified':False,'kind':kind,'sampled_at':sampled,'error_class':type(error).__name__}

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
 cutoff=datetime.fromisoformat(__HISTORY_STARTED_AT__.replace('Z','+00:00')).timestamp()
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
  migrations={kind:read_migration(kind) for kind in ('facts','patent')}
  facts_repair=read_facts_repair()
  for value in migrations.values():
   if not value.get('verified'):continue
   directory=value['root']+('/pg-alias-tail' if value['stage']=='pg_alias_tail' else '')
   name='source-dedup-'+hashlib.sha256(directory.encode()).hexdigest()[:12]+'-worker-'
   try:
    rows=await connection.fetch('SELECT application_name FROM pg_stat_activity WHERE application_name LIKE $1',name+'%')
    workers=[int(row['application_name'].rsplit('-worker-',1)[1]) for row in rows]
    if len(set(workers))!=len(workers) or any(not 0<=n<6 for n in workers):raise ValueError('Invalid migration sessions')
    value['pg_workers']=sorted(workers)
   except Exception as error:
    value['pg_workers']=None;value['pg_workers_error']=type(error).__name__
 finally:await connection.close()
 units=[{key:value[key] for key in ('unit','worker','ordinal','schema','table','leaf','old','new','data_type')}
        for value in plan['units']]
 print(json.dumps({'observed_at':datetime.now(timezone.utc).isoformat(),'run':str(ROOT),
  'plan_sha256':plan['plan_sha256'],'layout':layout,'stop_requested':(ROOT/'STOP').exists(),
  'units':units,'checkpoints':states,'worker_errors':errors,'backends':backends,
  'migrations':migrations,'facts_repair':facts_repair}))
asyncio.run(main())

'''

def parse_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def summarize_migration(raw, previous, sampled_at):
    empty = {'verified': False, 'sampled_at': sampled_at, 'error_class': 'NotSampled',
             'delta': None, 'rows_per_minute': None, 'note': None}
    if not raw or not raw.get('verified'):
        return {**empty, **{key: raw[key] for key in ('error_class', 'sampled_at') if raw and key in raw}}
    value = {**raw, 'errors': list(raw['errors']), 'delta': None, 'rows_per_minute': None, 'note': None}
    if value.get('container_running') is False:
        if value.get('exit_code') != 0:
            value['stage'] = 'failed'; value['errors'].append('ContainerExit' + str(value['exit_code']))
        elif value['stage'] not in ('complete', 'paused', 'canary_complete'):
            value['stage'] = 'stopped'; value['errors'].append('进程退出但迁移阶段未完成')
    if previous and previous.get('verified') and previous.get('plan_sha256') == value['plan_sha256']:
        elapsed = (parse_time(value['sampled_at']) - parse_time(previous['sampled_at'])).total_seconds()
        differences = [now - before for now, before in zip(value['worker_processed'], previous['worker_processed'])]
        if elapsed > 0 and len(differences) == 6 and all(n >= 0 for n in differences):
            value.update(delta=sum(differences), rows_per_minute=round(sum(differences) * 60 / elapsed, 2))
        else:
            value['note'] = '采样时间或检查点回退，本轮新增与速度未验证'
    return value


def migration_report(values, markdown_format=False, facts_repair=None):
    lines = ['', 'facts / 专利 RDS+ODS 迁移（同批双层验证的 record_id 只计一次）']
    headers = ('范围', '阶段', '双层已验证 ID / 目标', '本轮新增', 'ID/分钟', 'PG 会话', 'SQL 子批', '待确认事务', '错误')
    rows = []
    names = {'running': '迁移中', 'complete': '完成', 'failed': '错误', 'paused': '暂停',
             'canary_complete': '小批完成', 'pg_alias_tail': 'PG 旧源尾扫', 'mongo_coverage_complete': '待旧源尾扫', 'stopped': '进程已退出'}
    for kind in ('facts', 'patent'):
        value = values[kind];label = kind + '/RDS+ODS'
        if not value.get('verified'):
            rows.append((label, '未验证', '—', '—', '—', '未验证', '—', '—', value.get('error_class') or 'UnknownError'))
            continue
        total = f"{value['total']:,}" if value['total'] is not None else '未核定'
        delta = f"{value['delta']:,}" if value['delta'] is not None else '—'
        rate = f"{value['rows_per_minute']:,.1f}" if value['rows_per_minute'] is not None else '—'
        workers = str(len(value['pg_workers'])) + '/' + str(value.get('workers',6)) if value.get('pg_workers') is not None else '未验证'
        errors = '; '.join(value['errors']) or '无'
        if value.get('pg_workers_error'):errors += '; PG 会话未验证: ' + value['pg_workers_error']
        if value.get('note'):errors += '; ' + value['note']
        stage = names.get(value['stage'], value['stage'])
        rows.append((label, stage, f"{value['processed']:,}/{total}",
                     delta, rate, workers, f"{value['sql_batch_size']:,}", str(value['pending']), errors))
        if value.get('tail'):
            tail = value['tail']
            label = 'facts' if kind == 'facts' else '专利'
            lines.append(f"{label}旧源尾扫：RDS {tail['rds']:,} 行，ODS {tail['ods']:,} 行；完成单元 {tail['done']}/{tail['units']}；不与双层 record_id 进度相加。")
    if markdown_format:
        lines += ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |']
        lines.extend('| ' + ' | '.join(str(cell).replace('|', '\\|').replace('\n', ' ') for cell in row) + ' |' for row in rows)
    else:
        lines += [' | '.join(headers), *(' | '.join(row) for row in rows)]
    if facts_repair and facts_repair.get('verified'):
        lines.append('前置历史修复：78,600 个目标已完成定点去重；这是独立已完成阶段，不计入本次 facts 全量迁移进度。')
    else:
        lines.append('前置历史修复证据：未验证（' + ((facts_repair or {}).get('error_class') or 'NotSampled') + '）；不复用历史计数作为本次 facts 全量迁移进度。')
    lines.append('facts / 专利本次全量迁移未做业务库全表计数，完成率与 ETA 未核定。迁移不触发 Mongo CDC 全量回放。')
    return '\n'.join(lines)

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
            'facts_repair': snapshot.get('facts_repair'),
            'migrations': {kind: summarize_migration((snapshot.get('migrations') or {}).get(kind),
                (previous.get('migrations') or {}).get(kind) if previous else None, snapshot['observed_at'])
                for kind in ('facts', 'patent')}}

def markdown(value):
    time = parse_time(value['observed_at']).astimezone(BEIJING).strftime('%Y-%m-%d %H:%M:%S')
    layout = value['layout']
    interval = value.get('interval_seconds')
    lines = ['财报 / 专利迁移进度', '',
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
    lines.append(migration_report(value['migrations'], markdown_format=True, facts_repair=value.get('facts_repair')))
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
        '财报 / 专利迁移进度', '',
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
    lines.append(migration_report(value['migrations'], facts_repair=value.get('facts_repair')))
    return '\n'.join(lines)


HOST = r"""
import hashlib,http.client,io,json,re,socket,subprocess,tarfile
from datetime import datetime
from urllib.parse import quote
from pathlib import Path
class Docker(http.client.HTTPConnection):
 def __init__(self):super().__init__('localhost',timeout=10)
 def connect(self):
  self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
  self.sock.connect('/var/run/docker.sock')
history_root=Path('/data/clawer/output/source_dedup_20261109/sec-history-run01')
history_inside='/app/output/source_dedup_20261109/sec-history-run01'
history_owner=json.loads((history_root/'git-deployed-20261116.json').read_bytes())['new_started']
info=subprocess.run(['docker','inspect',history_owner],text=True,capture_output=True,timeout=10)
if info.returncode:raise RuntimeError('Current history container unavailable')
history=json.loads(info.stdout)[0]
if not history['State']['Running'] or history['State']['Pid']<=0:raise ValueError('Current history container is not running')
if not any(arg==history_inside or repr(history_inside) in arg or json.dumps(history_inside) in arg
 for arg in history['Config']['Cmd']):raise ValueError('Current history command root differs')
if not any(m['Type']=='bind' and m['RW'] and Path(history_inside).is_relative_to(Path(m['Destination']))
 and Path(m['Source'])/Path(history_inside).relative_to(Path(m['Destination']))==history_root
 for m in history['Mounts']):raise ValueError('Current history output mount differs')
history_started=history['State']['StartedAt']
history_started_iso=re.sub(r'(\.\d{6})\d+(?=Z|[+-]|$)',r'\1',history_started)
if datetime.fromisoformat(history_started_iso.replace('Z','+00:00')).timestamp()<=0:raise ValueError('Invalid history start time')
p=subprocess.run(['docker','exec','-i','-u','0','-e','PYTHONPATH=/app','web','python','-B','-'],
 input=__PROBE__.replace('__HISTORY_STARTED_AT__',repr(history_started_iso)),text=True,capture_output=True,timeout=45)
if p.returncode:raise RuntimeError(p.stderr[-1800:])
result=json.loads(p.stdout)
info=subprocess.run(['docker','inspect',history_owner],text=True,capture_output=True,timeout=10)
if info.returncode:raise RuntimeError('Current history container unavailable')
latest_history=json.loads(info.stdout)[0]
if latest_history['Id']!=history['Id'] or latest_history['State']['StartedAt']!=history_started or not latest_history['State']['Running']:
 raise ValueError('Current history container changed during sampling')
for kind,value in result['migrations'].items():
 if not value.get('verified'):continue
 try:
  owner=value['owner'];info=subprocess.run(['docker','inspect',owner['container']],text=True,capture_output=True,timeout=10)
  if info.returncode:raise RuntimeError('Current migration container unavailable')
  current=json.loads(info.stdout)[0]
  if current['Config']['Image']!=owner['image']:raise ValueError('Current owner image differs')
  client=Docker()
  try:
   client.request('GET','/containers/'+quote(owner['container'],safe='')+'/archive?path=%2Fopt%2Fsec-migration%2Fsource_safe_migration.py')
   response=client.getresponse();raw=response.read(524289)
   if response.status!=200 or len(raw)>524288:raise ValueError('Current owner source unavailable')
  finally:client.close()
  with tarfile.open(fileobj=io.BytesIO(raw),mode='r:*') as archive:
   members=[m for m in archive.getmembers() if m.isfile()]
   if len(members)!=1 or members[0].name.rsplit('/',1)[-1]!='source_safe_migration.py':raise ValueError('Unexpected source archive')
   source=archive.extractfile(members[0]).read()
  if hashlib.sha256(source).hexdigest()!=owner['source_sha256']:raise ValueError('Current owner source SHA differs')
  latest=json.loads(Path('/data/clawer/output/sec_migration_20261104/source-safe-current-owners.json').read_bytes())
  if latest.get(kind)!=owner:raise ValueError('Current owner changed during sampling')
  value.update(container_running=current['State']['Running'],exit_code=current['State']['ExitCode'],restart_count=current['RestartCount'])
 except Exception as error:
  result['migrations'][kind]={'verified':False,'sampled_at':value['sampled_at'],'error_class':type(error).__name__}
print(json.dumps(result))
"""

async def collect():
    sys.path.insert(0, str(HERE.parent / 'twitter_schedule_20261027'))
    from ssh_inspect import connect, credentials
    host = HOST.replace('__PROBE__', repr(PROBE))
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
