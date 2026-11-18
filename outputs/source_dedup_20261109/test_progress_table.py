"""Read-only progress coverage for the active SEC repair and migration scopes."""
import ast
import asyncio
import copy
import hashlib
import io
import json
import os
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from outputs.source_dedup_20261109 import progress_table as progress


SAMPLED = '2026-11-16T06:00:00+00:00'
BASE = '/app/output/sec_migration_20261104'
HOST_BASE = '/data/clawer/output/sec_migration_20261104'


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()


def snapshot():
    tables = [('ts_rds', 'rds_filing'), ('ts_ods', 'ods_filing'), ('ts_ods', 'ods_filing_document'),
              ('ts_rds', 'rds_financial_fact'), ('ts_ods', 'ods_financial_fact')]
    units = [{'unit': i, 'worker': i % 6, 'ordinal': 0, 'schema': schema, 'table': table,
              'leaf': table + '_p00', 'old': 'old', 'new': 'new', 'data_type': table.split('_', 1)[1]}
             for i, (schema, table) in enumerate(tables)]
    return {'observed_at': SAMPLED, 'run': '/app/output/run', 'plan_sha256': 'history-plan',
            'layout': {'workers': 6, 'page_size': 3000, 'sql_batch_size': 300}, 'units': units,
            'checkpoints': [], 'backends': [], 'worker_errors': [], 'stop_requested': False,
            'facts_repair': {'verified': True, 'processed': 78600, 'stage': 'complete'}}


def migration(kind='patent', **changes):
    return {'verified': True, 'sampled_at': SAMPLED, 'kind': kind, 'plan_sha256': 'a' * 64,
            'worker_processed': [100] * 6, 'processed': 600, 'total': None,
            'stage': 'running', 'pg_workers': list(range(6)), 'pending': 0, 'errors': [],
            'tail': None, 'cdc_replay': False, 'sql_batch_size': 300, **changes}


def reader_fixture(kind='patent', complete=False, bounded=False):
    files = {}

    class MemoryPath(PurePosixPath):
        def read_bytes(self):
            if str(self) not in files:
                raise FileNotFoundError(str(self))
            return files[str(self)]

        def read_text(self, *args, **kwargs):
            return self.read_bytes().decode('utf8')

        def exists(self):
            return str(self) in files

        def resolve(self):
            return self

    functions = [node for node in ast.parse(progress.PROBE).body if isinstance(node, ast.FunctionDef)]
    environment = {'datetime': datetime, 'timezone': timezone, 'json': json, 'hashlib': hashlib,
                   'Path': MemoryPath}
    exec(compile(ast.Module(body=functions, type_ignores=[]), '<migration-probe>', 'exec'), environment)
    plan_kind = 'facts' if bounded else 'facts_full' if kind == 'facts' else 'patent'
    root_name = 'facts-source-safe-20261116' if bounded else 'facts-migration-20261116' if kind == 'facts' else 'patent-source-safe-20261116'
    root = BASE + '/' + root_name
    parts = []
    for number in range(6):
        part = {'worker': number, 'lower': f'{number * 100000 + 1:024x}',
                'upper': f'{(number + 1) * 100000 + 1:024x}' if number < 5 else None}
        if number == 5:
            part['upper_inclusive'] = f'{600000:024x}'
        if bounded:
            part.update(upper_inclusive=f'{number * 100000 + 13100:024x}', actual_count=13100,
                        processed=13100)
        parts.append(part)
    plan = {'protocol': 'source_safe_mongo_migration_v1', 'kind': plan_kind,
            'collection': 'financial_fact_sec_edgar' if kind == 'facts' else 'patent_google',
            'data_type': 'financial_fact' if kind == 'facts' else 'patent',
            'aliases': ['sec_edgar_financial_fact', 'sec_edgar_company'] if kind == 'facts' else ['google_patent'],
            'workers': 6, 'page_size': 3000, 'sql_batch_size': 300,
            'mongo_uuid': '1' * 32, 'parts': parts, 'manifest_path': None, 'manifest_sha256': None}
    if bounded:
        manifest = {'protocol': 'facts_burst_checkpoint_manifest_v1', 'total': 78600,
                    'mongo_uuid_hex': plan['mongo_uuid'], 'parts': parts}
        manifest_path = BASE + '/facts-burst-evidence-20261116/facts-burst-manifest-20261116.json'
        files[manifest_path] = encoded(manifest)
        plan.update(manifest_path=manifest_path, manifest_sha256=hashlib.sha256(files[manifest_path]).hexdigest())
    plan['plan_sha256'] = hashlib.sha256(encoded(plan)).hexdigest()
    files[root + '/plan.json'] = encoded(plan)
    owner = {'root': HOST_BASE + '/' + root_name, 'workers': 6,
             'page_size': 3000, 'sql_batch_size': 300, 'mongo_cdc_replay': False,
             'source_sha256': 'c' * 64, 'plan_sha256': plan['plan_sha256'],
             'container': 'source-safe-facts-full-20261116' if kind == 'facts' and not bounded else 'source-safe-' + kind + '-20261116',
             'image': 'maintenance:commit'}
    files[BASE + '/source-safe-current-owners.json'] = encoded({'facts_history_repair' if bounded else kind: owner})
    count = 13100 if complete and bounded else 2
    for number, part in enumerate(parts):
        rows = min(count, 300)
        ids = [f'private-fixture-{number}-{n}' for n in range(rows)]
        cursor = f'{int(part["lower"], 16) + count - 1:024x}'
        relative = f'transactions/worker-{number}.committed.json'
        receipt = {'protocol': plan['protocol'], 'committed': True, 'plan_sha256': plan['plan_sha256'],
                   'mongo_uuid': plan['mongo_uuid'], 'worker': number, 'part': part, 'receipt': relative,
                   'record_ids': ids, 'last_id': cursor, 'rows': rows, 'previous_processed': count - rows,
                   'after_sha256': {layer: {rid: 'b' * 64 for rid in ids} for layer in ('rds', 'ods')}}
        files[root + '/' + relative] = encoded(receipt)
        files[root + f'/worker-{number}.json'] = encoded({'worker': number,
            'plan_sha256': plan['plan_sha256'], 'processed': count, 'cursor': cursor,
            'done': complete, 'pending': None, 'last_error': None, 'last_receipt': relative})
    summary = {'protocol': plan['protocol'], 'kind': plan_kind, 'plan_sha256': plan['plan_sha256'],
               'mongo_uuid': plan['mongo_uuid'], 'workers': 6, 'page_size': 3000, 'sql_batch_size': 300,
               'processed': count * 6, 'stage': 'complete' if complete else 'running',
               'pg_only_alias_sweep_complete': False, 'cdc_replay': False, 'last_error': None}
    files[root + '/state.json'] = encoded(summary)
    if complete and not bounded:
        add_tail(files, root, plan, complete=True)
    return lambda: environment['read_facts_repair']() if bounded else environment['read_migration'](kind), files, root, plan


def mutate(files, path, **values):
    value = json.loads(files[path]); value.update(values); files[path] = encoded(value)
    return value


def set_runtime_batch(files, root, plan, batch):
    files[root + '/layout.json'] = encoded({'plan_sha256': plan['plan_sha256'],
        'workers': 6, 'page_size': 3000, 'sql_batch_size': batch})
    owner_path = BASE + '/source-safe-current-owners.json'
    owners = json.loads(files[owner_path])
    for owner in owners.values():
        owner['sql_batch_size'] = batch
    files[owner_path] = encoded(owners)
    mutate(files, root + '/state.json', sql_batch_size=batch)


def set_receipt_rows(files, root, plan, rows):
    checkpoint_path = root + '/worker-0.json'
    checkpoint = json.loads(files[checkpoint_path])
    count = max(checkpoint['processed'], rows)
    cursor = f'{int(plan["parts"][0]["lower"], 16) + count - 1:024x}'
    ids = [f'private-large-batch-{n}' for n in range(rows)]
    mutate(files, root + '/' + checkpoint['last_receipt'], rows=rows, record_ids=ids,
        previous_processed=count - rows, last_id=cursor,
        after_sha256={layer: {rid: 'b' * 64 for rid in ids} for layer in ('rds', 'ods')})
    mutate(files, checkpoint_path, processed=count, cursor=cursor)


def add_tail(files, root, parent_plan, complete=False):
    for number in range(6):
        mutate(files, root + f'/worker-{number}.json', done=True)
    scope = {'protocol': 'source_safe_' + parent_plan['kind'] + '_alias_tail_v1',
             'mongo_plan_sha256': parent_plan['plan_sha256']}
    directory = root + '/pg-alias-tail'
    files[directory + '/scope.json'] = encoded(scope)
    sha = hashlib.sha256(files[directory + '/scope.json']).hexdigest()
    units = []
    for old in parent_plan['aliases']:
        for layer in ('rds', 'ods'):
            for ordinal in range(32):
                units.append({'unit': len(units), 'schema': 'ts_' + layer,
                              'table': layer + '_' + parent_plan['data_type'], 'layer': layer, 'ordinal': ordinal,
                              'old': old, 'new': parent_plan['collection'], 'data_type': parent_plan['data_type']})
    files[directory + '/plan.json'] = encoded({'plan_sha256': sha, 'workers': 6,
        'batch_size': 300, 'guard_table': None, 'units': units})
    for unit in units:
        files[directory + f'/checkpoints/unit-{unit["unit"]}.json'] = encoded({
            'unit': unit['unit'], 'plan_sha256': sha, 'processed': 10, 'renamed': 8,
            'deduplicated': 2, 'pending': None, 'done': complete})
    mutate(files, root + '/state.json', stage='complete' if complete else 'pg_alias_tail',
           pg_only_alias_sweep_complete=complete)
    return directory


class ProgressTests(unittest.TestCase):
    def history_errors(self, error_time):
        main = next(node for node in ast.parse(progress.PROBE).body
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == 'main')
        start = next(n for n, node in enumerate(main.body)
                     if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'cutoff'
                                                           for t in node.targets))
        stop = next(n for n, node in enumerate(main.body)
                    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'prefix'
                                                          for t in node.targets))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_start = root / 'started.json'; old_start.write_text('{}')
            os.utime(old_start, (0, 0))
            error = root / 'worker-0-error.json'
            error.write_text(json.dumps({'worker': 0, 'stage': 'failed', 'last_error': 'TimeoutError'}))
            timestamp = datetime.fromisoformat(error_time).timestamp()
            os.utime(error, (timestamp, timestamp))
            environment = {'ROOT': root, 'datetime': datetime, 'json': json, 'errors': [],
                           '__HISTORY_STARTED_AT__': '2026-11-16T22:38:58.807236074Z'}
            exec(compile(ast.Module(body=main.body[start:stop], type_ignores=[]),
                         '<history-error-probe>', 'exec'), environment)
            self.assertTrue(error.exists())
            return environment['errors']

    def test_history_error_before_current_container_restart_is_excluded(self):
        errors = self.history_errors('2026-11-16T22:38:17.663979+00:00')
        self.assertEqual(errors, [])
        value = snapshot(); value['worker_errors'] = errors
        value['backends'] = [{'application_name': 'source-dedup-history-worker-0'}]
        self.assertEqual(progress.summarize(value)['rows'][0]['stage'], '修复中')

    def test_history_error_after_current_container_restart_remains_visible(self):
        errors = self.history_errors('2026-11-16T22:39:00+00:00')
        self.assertEqual(errors, [{'worker': 0, 'unit': None, 'stage': 'failed', 'last_error': 'TimeoutError'}])
        value = snapshot(); value['worker_errors'] = errors
        value['backends'] = [{'application_name': 'source-dedup-history-worker-0'}]
        result = progress.summarize(value)
        self.assertEqual(result['rows'][0]['stage'], '错误')
        self.assertIn('TimeoutError', progress.markdown(result))

    def test_history_container_inspection_failure_is_unverified(self):
        with patch.object(Path, 'read_bytes', return_value=encoded({'new_started': 'current-history'})), \
             patch.object(subprocess, 'run', return_value=SimpleNamespace(returncode=1, stderr='unavailable')) as run:
            with self.assertRaisesRegex(RuntimeError, 'Current history container unavailable'):
                exec(progress.HOST, {'__PROBE__': progress.PROBE})
        self.assertEqual(run.call_count, 1)
        client = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(exit_status=1,
            stdout='', stderr='RuntimeError: Current history container unavailable')),
            close=lambda: None, wait_closed=AsyncMock())
        ssh = SimpleNamespace(connect=AsyncMock(return_value=client), credentials=lambda: ('space', 'unused'))
        with patch.dict(sys.modules, {'ssh_inspect': ssh}):
            with self.assertRaisesRegex(RuntimeError, '只读进度查询未验证.*Current history container unavailable'):
                asyncio.run(progress.collect())

    def test_history_docker_start_time_is_normalized_for_python310(self):
        inside = '/app/output/source_dedup_20261109/sec-history-run01'

        def python310_iso(value):
            if '.' in value and len(value.split('.', 1)[1].split('+', 1)[0]) > 6:
                raise ValueError('Python 3.10 rejects fractional nanoseconds')
            return datetime.fromisoformat(value)

        cases = (('2026-11-16T22:38:58.807236074Z', '2026-11-16T22:38:58.807236Z'),
                 ('2026-11-16T22:38:58.807236Z', '2026-11-16T22:38:58.807236Z'),
                 ('2026-11-16T22:38:58Z', '2026-11-16T22:38:58Z'), ('invalid', None))
        for started, expected in cases:
            with self.subTest(started=started):
                container = {'Id': 'current-id', 'State': {'Running': True, 'Pid': 100, 'StartedAt': started},
                             'Config': {'Cmd': ['-c', "exec(['--root'," + repr(inside) + '])']},
                             'Mounts': [{'Type': 'bind', 'RW': True, 'Source': '/data/clawer/output',
                                         'Destination': '/app/output'}]}
                inspected = SimpleNamespace(returncode=0, stdout=json.dumps([container]))
                sampled = SimpleNamespace(returncode=0, stdout=json.dumps({'migrations': {}}))
                environment = {'__PROBE__': progress.PROBE}
                with patch.object(Path, 'read_bytes', return_value=encoded({'new_started': 'current-history'})), \
                     patch.object(subprocess, 'run', side_effect=[inspected, sampled, inspected]) as run, \
                     patch('datetime.datetime', SimpleNamespace(fromisoformat=python310_iso)), \
                     redirect_stdout(io.StringIO()):
                    if expected is None:
                        with self.assertRaises(ValueError):
                            exec(progress.HOST, environment)
                        self.assertEqual(run.call_count, 1)
                    else:
                        exec(progress.HOST, environment)
                        self.assertEqual(environment['history_started'], started)
                        self.assertEqual(environment['history_started_iso'], expected)
                        source = run.call_args_list[1].kwargs['input']
                        self.assertIn('datetime.fromisoformat(' + repr(expected) + '.replace', source)
                        self.assertNotIn('__HISTORY_STARTED_AT__', source)

    def test_direct_script_and_module_entrypoint_remain_available(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-B', str(Path(progress.__file__).resolve()), '--help'],
                                    cwd=directory, capture_output=True, text=True, check=True, timeout=10)
        self.assertIn('--markdown', result.stdout)
        self.assertIn('--json', result.stdout)

    def test_completed_names_and_tle_are_not_collected_or_rendered(self):
        self.assertNotIn('tle_', progress.PROBE.lower())
        self.assertNotIn('patent-cdc-state.json', progress.PROBE)
        self.assertNotIn('name-worker-', progress.PROBE)
        self.assertNotIn('SELECT count(*)', progress.PROBE)
        self.assertNotIn('MongoClient', progress.PROBE)
        value = progress.summarize({**snapshot(), 'patent': {'name_processed': 999},
                                    'tle_es': {'committed_documents': 111}})
        self.assertNotIn('patent', value)
        self.assertNotIn('tle_es', value)
        for render in (progress.markdown, progress.plain_table):
            report = render(value)
            self.assertNotIn('TLE', report)
            self.assertNotIn('命名', report)
            self.assertIn('patent/RDS+ODS', report)

    def test_only_three_history_tables_keep_their_counters(self):
        raw = snapshot()
        raw['checkpoints'] = [{'unit': 0, 'worker': 0, 'processed': 10, 'renamed': 3,
                               'deduplicated': 7, 'done': False}]
        raw['migrations'] = {'facts': migration('facts'), 'patent': migration()}
        value = progress.summarize(raw)
        self.assertEqual([row['table'] for row in value['rows']],
                         ['ts_rds.rds_filing', 'ts_ods.ods_filing', 'ts_ods.ods_filing_document'])
        self.assertEqual(value['totals']['processed'], 10)
        self.assertEqual(value['migrations']['facts']['processed'], 600)
        self.assertEqual(value['migrations']['patent']['processed'], 600)
        for render in (progress.markdown, progress.plain_table):
            report = render(value)
            self.assertIn('同批双层验证的 record_id 只计一次', report)
            self.assertIn('600/未核定', report)
            self.assertNotIn('600/78,600', report)
            self.assertIn('不计入本次 facts 全量迁移进度', report)
            self.assertNotIn('1,200/', report)

    def test_history_pending_never_advances_committed_counts(self):
        raw = snapshot(); raw['checkpoints'] = [{'unit': 0, 'worker': 0, 'processed': 10,
            'renamed': 3, 'deduplicated': 7, 'pending': 'prepared.json', 'done': False}]
        value = progress.summarize(raw)
        self.assertEqual(value['totals'], {'processed': 10, 'renamed': 3, 'deduplicated': 7, 'pending': 1})
        self.assertIsNone(value['rows'][0]['completion_percent'])
        self.assertIsNone(value['rows'][0]['delta'])

    def test_history_worker_failure_surfaces_in_both_formats(self):
        raw = snapshot(); raw['worker_errors'] = [{'unit': 0, 'worker': 0, 'last_error': 'TimeoutError'}]
        value = progress.summarize(raw)
        self.assertEqual(value['rows'][0]['stage'], '错误')
        for render in (progress.markdown, progress.plain_table):
            self.assertIn('TimeoutError', render(value))

    def test_history_six_slots_use_ordinal_not_old_checkpoint_owner(self):
        raw = snapshot()
        for original in raw['units'][:3]:
            raw['units'].append({**original, 'unit': len(raw['units']), 'ordinal': 1, 'worker': 7,
                                 'leaf': original['table'] + '_p01'})
        raw['checkpoints'] = [{'unit': u['unit'], 'worker': 7, 'processed': 10,
                              'renamed': 3, 'deduplicated': 7, 'done': False} for u in raw['units']]
        raw['backends'] = [{'application_name': f'source-dedup-history-worker-{n}'} for n in range(6)]
        value = progress.summarize(raw)
        self.assertEqual([r['workers'] for r in value['rows']], [[0, 1], [2, 3], [4, 5]])
        self.assertEqual(value['workers_connected'], 6)
        self.assertEqual(value['totals']['processed'], 60)

    def test_history_delta_uses_actual_sample_time_and_rejects_rewind(self):
        raw = snapshot(); raw['checkpoints'] = [{'unit': 0, 'processed': 100, 'renamed': 40, 'deduplicated': 60}]
        before = progress.summarize(raw)
        raw['observed_at'] = '2026-11-16T06:10:00+00:00'
        raw['checkpoints'][0].update(processed=400, renamed=140, deduplicated=260)
        after = progress.summarize(raw, before)
        self.assertEqual((after['rows'][0]['delta'], after['rows'][0]['rows_per_minute']), (300, 30))
        raw['checkpoints'][0].update(processed=50, renamed=20, deduplicated=30)
        row = progress.summarize(raw, before)['rows'][0]
        self.assertIsNone(row['delta'])
        self.assertIn('回退', row['note'])

    def test_history_bad_units_counters_or_duplicate_sessions_are_rejected(self):
        for change in ('bad_count', 'duplicate_unit', 'duplicate_session'):
            raw = snapshot()
            if change == 'bad_count':
                raw['checkpoints'] = [{'unit': 0, 'processed': 20, 'renamed': 3, 'deduplicated': 7}]
            elif change == 'duplicate_unit':
                raw['units'].append(copy.deepcopy(raw['units'][0]))
            else:
                raw['backends'] = [{'application_name': 'source-dedup-run-worker-0'}] * 2
            with self.subTest(change=change), self.assertRaises(ValueError):
                progress.summarize(raw)

    def test_migration_delta_binds_plan_and_individual_worker_counters(self):
        before = progress.summarize_migration(migration(), None, SAMPLED)
        raw = migration(sampled_at='2026-11-16T06:10:00+00:00', processed=660, worker_processed=[110] * 6)
        after = progress.summarize_migration(raw, before, raw['sampled_at'])
        self.assertEqual((after['delta'], after['rows_per_minute']), (60, 6))
        raw['worker_processed'] = [99, 120, 110, 110, 110, 111]
        self.assertIsNone(progress.summarize_migration(raw, before, raw['sampled_at'])['delta'])
        raw['plan_sha256'] = 'c' * 64
        self.assertIsNone(progress.summarize_migration(raw, before, raw['sampled_at'])['delta'])

    def test_read_failure_drops_previous_migration_counters(self):
        before = progress.summarize_migration(migration(), None, SAMPLED)
        raw = {'verified': False, 'sampled_at': '2026-11-16T06:10:00+00:00', 'error_class': 'FileNotFoundError'}
        value = progress.summarize_migration(raw, before, raw['sampled_at'])
        self.assertNotIn('processed', value)
        self.assertIsNone(value['delta'])
        report = progress.migration_report({'facts': value, 'patent': value}, True)
        self.assertIn('未验证', report)
        self.assertIn('FileNotFoundError', report)
        self.assertNotIn('600/', report)

    def test_first_sample_has_no_invented_rate_or_total(self):
        value = progress.summarize_migration(migration(), None, SAMPLED)
        self.assertIsNone(value['delta'])
        self.assertIsNone(value['rows_per_minute'])
        self.assertIsNone(value['total'])
        self.assertNotIn('completion_percent', value)

    def test_tail_rows_are_separate_from_both_layer_identity_progress(self):
        raw = {**snapshot(), 'migrations': {'facts': migration('facts'),
            'patent': migration(stage='pg_alias_tail', tail={'rds': 23, 'ods': 45, 'done': 2, 'pending': 1, 'units': 64})}}
        value = progress.summarize(raw)
        for render in (progress.markdown, progress.plain_table):
            report = render(value)
            self.assertIn('PG 旧源尾扫', report)
            self.assertIn('RDS 23 行，ODS 45 行', report)
            self.assertIn('不与双层 record_id 进度相加', report)
            self.assertIn('600/未核定', report)
            self.assertNotIn('668/未核定', report)

    def test_completed_facts_zero_live_sessions_still_report_complete(self):
        probe, files, root, plan = reader_fixture('facts', complete=True)
        raw = probe(); self.assertTrue(raw['verified'], raw)
        raw['pg_workers'] = []
        value = progress.summarize_migration(raw, None, raw['sampled_at'])
        for markdown in (False, True):
            report = progress.migration_report({'facts': value, 'patent': progress.summarize_migration(None, None, SAMPLED)}, markdown)
            self.assertIn('facts/RDS+ODS', report)
            self.assertIn('完成', report)
            self.assertIn('12/未核定', report)
            self.assertIn('0/6', report)

    def test_facts_full_progress_does_not_reuse_completed_burst_count_or_rate(self):
        old = migration('facts', plan_sha256='b' * 64, processed=78600,
                        worker_processed=[13100] * 6, stage='complete')
        raw = migration('facts', sampled_at='2026-11-16T06:10:00+00:00')
        current = progress.summarize_migration(raw, old, raw['sampled_at'])
        self.assertEqual(current['processed'], 600)
        self.assertIsNone(current['delta'])
        self.assertIsNone(current['total'])
        report = progress.migration_report({'facts': current, 'patent': current}, True,
            {'verified': True, 'processed': 78600, 'stage': 'complete'})
        self.assertIn('600/未核定', report)
        self.assertNotIn('600/78,600', report)
        self.assertNotIn('79,200/', report)
        self.assertIn('前置历史修复：78,600', report)

    def test_facts_full_rejects_owner_still_pointing_to_completed_repair(self):
        probe, files, root, _ = reader_fixture('facts')
        path = BASE + '/source-safe-current-owners.json'
        owners = json.loads(files[path]); owners['facts']['root'] = HOST_BASE + '/facts-source-safe-20261116'
        files[path] = encoded(owners)
        value = probe()
        self.assertFalse(value['verified'])
        self.assertNotIn('processed', value)

    def test_completed_burst_evidence_is_independent_of_full_migration(self):
        repair, files, root, plan = reader_fixture('facts', complete=True, bounded=True)
        before = dict(files)
        value = repair()
        self.assertTrue(value['verified'], value)
        self.assertEqual(value['processed'], 78600)
        self.assertEqual(files, before)
        mutate(files, root + '/worker-0.json', pending='transactions/not-committed.json')
        self.assertFalse(repair()['verified'])
        full, _, _, _ = reader_fixture('facts')
        self.assertEqual(full()['processed'], 12)

    def test_facts_tail_requires_both_aliases_for_both_layers_all_128_units(self):
        probe, files, root, plan = reader_fixture('facts')
        directory = add_tail(files, root, plan, complete=True)
        value = probe(); self.assertTrue(value['verified'], value)
        self.assertEqual(value['processed'], 12)
        self.assertEqual(value['tail'], {'rds': 640, 'ods': 640, 'done': 128, 'pending': 0, 'units': 128})
        value['pg_workers'] = []
        rendered = progress.migration_report({'facts': progress.summarize_migration(value, None, value['sampled_at']),
            'patent': progress.summarize_migration(None, None, SAMPLED)}, True)
        self.assertIn('facts旧源尾扫：RDS 640 行，ODS 640 行；完成单元 128/128', rendered)
        self.assertIn('12/未核定', rendered)
        self.assertNotIn('1,292/未核定', rendered)
        tail_plan_path = directory + '/plan.json';tail_plan = json.loads(files[tail_plan_path])
        tail_plan['units'][-1]['old'] = 'foreign_source'
        files[tail_plan_path] = encoded(tail_plan)
        self.assertFalse(probe()['verified'])

    def test_facts_full_complete_requires_tail_acceptance(self):
        probe, files, root, plan = reader_fixture('facts')
        for number in range(6):
            mutate(files, root + f'/worker-{number}.json', done=True)
        mutate(files, root + '/state.json', stage='mongo_coverage_complete')
        self.assertTrue(probe()['verified'])
        mutate(files, root + '/state.json', stage='complete')
        self.assertFalse(probe()['verified'])

    def test_abnormal_container_exit_is_visible_without_losing_verified_commits(self):
        raw = migration(container_running=False, exit_code=137, restart_count=3)
        value = progress.summarize_migration(raw, None, SAMPLED)
        self.assertEqual(value['processed'], 600)
        self.assertEqual(value['stage'], 'failed')
        self.assertTrue(any('137' in error for error in value['errors']))

    def test_completed_container_exit_zero_does_not_mark_failure(self):
        raw = migration('facts', stage='complete', processed=78600, worker_processed=[13100] * 6,
                        container_running=False, exit_code=0, pg_workers=[])
        value = progress.summarize_migration(raw, None, SAMPLED)
        self.assertEqual((value['stage'], value['errors']), ('complete', []))

    def test_pg_session_query_error_is_explicit_without_inventing_worker_count(self):
        value = progress.summarize_migration(migration(pg_workers=None, pg_workers_error='QueryCanceledError'), None, SAMPLED)
        report = progress.migration_report({'facts': value, 'patent': value}, True)
        self.assertIn('PG 会话未验证: QueryCanceledError', report)
        self.assertNotIn('| 0/6 |', report)

    def test_probe_reads_both_verified_layers_and_exposes_no_business_ids(self):
        for kind in ('facts', 'patent'):
            probe, files, root, plan = reader_fixture(kind)
            raw = probe(); self.assertTrue(raw['verified'], raw)
            self.assertEqual((raw['processed'], raw['worker_processed'], raw['pending']), (12, [2] * 6, 0))
            self.assertNotIn('private-fixture', json.dumps(raw))
            self.assertNotIn('record_ids', json.dumps(raw))
            self.assertIsNone(raw['total'])

    def test_probe_pending_does_not_add_future_transaction_rows(self):
        probe, files, root, _ = reader_fixture()
        mutate(files, root + '/worker-0.json', pending='transactions/future.prepared.json')
        raw = probe(); self.assertTrue(raw['verified'], raw)
        self.assertEqual((raw['processed'], raw['pending']), (12, 1))

    def test_large_runtime_batches_keep_immutable_plan_and_verify_both_layer_receipts(self):
        for kind in ('facts', 'patent'):
            for batch in (600, 1000, 3000):
                with self.subTest(kind=kind, batch=batch):
                    probe, files, root, plan = reader_fixture(kind)
                    original_plan = files[root + '/plan.json']
                    set_runtime_batch(files, root, plan, batch)
                    set_receipt_rows(files, root, plan, batch)
                    before = dict(files)
                    value = probe()
                    self.assertTrue(value['verified'], value)
                    self.assertEqual((value['processed'], value['sql_batch_size']), (batch + 10, batch))
                    self.assertEqual(files[root + '/plan.json'], original_plan)
                    self.assertEqual(files, before)
                    current = progress.summarize_migration(value, None, value['sampled_at'])
                    current['pg_workers'] = list(range(6))
                    for markdown in (False, True):
                        report = progress.migration_report({'facts': current, 'patent': current}, markdown)
                        self.assertIn('SQL 子批', report)
                        self.assertIn(f'{batch:,}', report)
                    receipt_path = root + '/transactions/worker-0.committed.json'
                    receipt = json.loads(files[receipt_path]); del receipt['after_sha256']['ods'][receipt['record_ids'][-1]]
                    files[receipt_path] = encoded(receipt)
                    self.assertFalse(probe()['verified'])

    def test_runtime_batch_requires_matching_layout_owner_summary_and_original_plan(self):
        cases = ('owner', 'summary', 'plan_sha256', 'workers', 'page_size', 'zero', 'too_large', 'boolean', 'missing')
        for case in cases:
            with self.subTest(case=case):
                probe, files, root, plan = reader_fixture()
                set_runtime_batch(files, root, plan, 600)
                layout_path = root + '/layout.json'
                if case == 'owner':
                    owner_path = BASE + '/source-safe-current-owners.json'; owners = json.loads(files[owner_path])
                    owners['patent']['sql_batch_size'] = 300; files[owner_path] = encoded(owners)
                elif case == 'summary':
                    mutate(files, root + '/state.json', sql_batch_size=300)
                elif case == 'missing':
                    layout = json.loads(files[layout_path]); del layout['workers']; files[layout_path] = encoded(layout)
                else:
                    key, value = {'plan_sha256': ('plan_sha256', 'f' * 64), 'workers': ('workers', 8),
                        'page_size': ('page_size', 5000), 'zero': ('sql_batch_size', 0),
                        'too_large': ('sql_batch_size', 3001), 'boolean': ('sql_batch_size', True)}[case]
                    mutate(files, layout_path, **{key: value})
                value = probe()
                self.assertFalse(value['verified'], value)
                self.assertNotIn('processed', value)

    def test_reduced_runtime_batch_still_accepts_committed_larger_batch(self):
        probe, files, root, plan = reader_fixture()
        set_receipt_rows(files, root, plan, 1000)
        set_runtime_batch(files, root, plan, 300)
        value = probe()
        self.assertTrue(value['verified'], value)
        self.assertEqual((value['processed'], value['sql_batch_size']), (1010, 300))
        del files[root + '/layout.json']
        self.assertTrue(probe()['verified'])
        set_receipt_rows(files, root, plan, 3001)
        self.assertFalse(probe()['verified'])

    def test_bounded_facts_history_still_rejects_large_receipts(self):
        probe, files, root, plan = reader_fixture('facts', complete=True, bounded=True)
        set_receipt_rows(files, root, plan, 600)
        value = probe()
        self.assertFalse(value['verified'], value)
        self.assertNotIn('processed', value)

    def test_alias_tail_reports_its_unchanged_300_row_batch(self):
        probe, files, root, plan = reader_fixture()
        add_tail(files, root, plan)
        set_runtime_batch(files, root, plan, 600)
        value = probe()
        self.assertTrue(value['verified'], value)
        self.assertEqual(value['sql_batch_size'], 300)

    def test_probe_verifies_conflict_archive_hash_before_reusing_receipt(self):
        probe, files, root, _ = reader_fixture()
        path = root + '/transactions/worker-0.committed.json'
        receipt = json.loads(files[path]); archive = b'lossless dummy archived rows'
        files[root + '/transactions/conflicts.jsonl.gz'] = archive
        receipt['archive'] = {'path': 'transactions/conflicts.jsonl.gz', 'rows': 2,
                              'sha256': hashlib.sha256(archive).hexdigest()}
        files[path] = encoded(receipt)
        self.assertTrue(probe()['verified'])
        files[root + '/transactions/conflicts.jsonl.gz'] = b'changed'
        value = probe()
        self.assertFalse(value['verified'])
        self.assertNotIn('processed', value)

    def test_probe_tail_reads_bound_units_without_mixing_layer_row_counts(self):
        probe, files, root, plan = reader_fixture()
        add_tail(files, root, plan)
        value = probe(); self.assertTrue(value['verified'], value)
        self.assertEqual(value['processed'], 12)
        self.assertEqual(value['tail'], {'rds': 320, 'ods': 320, 'done': 0, 'pending': 0, 'units': 64})

    def test_patent_complete_requires_successful_all_unit_tail(self):
        probe, files, root, plan = reader_fixture()
        directory = add_tail(files, root, plan, complete=True)
        self.assertTrue(probe()['verified'])
        mutate(files, directory + '/checkpoints/unit-0.json', pending='transaction.pending')
        value = probe(); self.assertFalse(value['verified'])
        self.assertNotIn('processed', value)

    def test_probe_reader_failure_isolated_from_other_scope(self):
        facts, facts_files, _, _ = reader_fixture('facts', complete=True)
        patent, patent_files, root, _ = reader_fixture()
        del patent_files[root + '/state.json']
        self.assertTrue(facts()['verified'])
        self.assertEqual(facts()['processed'], 12)
        self.assertFalse(patent()['verified'])

    def test_host_inspection_reads_source_of_completed_containers_without_exec(self):
        self.assertIn('/archive?path=', progress.HOST)
        self.assertIn("['docker','inspect',owner['container']]", progress.HOST)
        self.assertNotIn("['docker','exec',owner['container']", progress.HOST)

    def test_probe_catches_bad_owner_plan_checkpoint_and_each_layer_receipt(self):
        cases = ('foreign_owner', 'bad_source_hash', 'bad_plan', 'wrong_worker', 'bool_count',
                 'cursor_outside', 'missing_receipt', 'rds_hash', 'missing_ods', 'count_advance', 'state_ahead')
        for case in cases:
            with self.subTest(case=case):
                probe, files, root, plan = reader_fixture()
                owner_path = BASE + '/source-safe-current-owners.json'
                if case in ('foreign_owner', 'bad_source_hash'):
                    owners = json.loads(files[owner_path]); owners['patent']['root' if case == 'foreign_owner' else 'source_sha256'] = 'foreign'
                    files[owner_path] = encoded(owners)
                elif case == 'bad_plan':
                    mutate(files, root + '/plan.json', workers=8)
                elif case == 'wrong_worker':
                    mutate(files, root + '/worker-0.json', worker=1)
                elif case == 'bool_count':
                    mutate(files, root + '/worker-0.json', processed=True)
                elif case == 'cursor_outside':
                    mutate(files, root + '/worker-0.json', cursor='f' * 24)
                elif case == 'missing_receipt':
                    del files[root + '/transactions/worker-0.committed.json']
                elif case == 'count_advance':
                    mutate(files, root + '/worker-0.json', processed=3)
                elif case == 'state_ahead':
                    mutate(files, root + '/state.json', processed=13)
                else:
                    path = root + '/transactions/worker-0.committed.json'; receipt = json.loads(files[path])
                    if case == 'missing_ods':
                        del receipt['after_sha256']['ods']
                    else:
                        receipt['after_sha256']['rds'][receipt['record_ids'][0]] = 'not-a-hash'
                    files[path] = encoded(receipt)
                value = probe()
                self.assertFalse(value['verified'], value)
                self.assertNotIn('processed', value)

    def test_actual_worker_error_is_reported_without_hiding_committed_progress(self):
        probe, files, root, _ = reader_fixture()
        mutate(files, root + '/worker-1.json', last_error='QueryCanceledError')
        mutate(files, root + '/state.json', stage='failed', last_error='RuntimeError')
        value = probe(); self.assertTrue(value['verified'], value)
        self.assertEqual(value['processed'], 12)
        self.assertIn('QueryCanceledError', value['errors'])

    def test_probe_accepts_lagging_aggregate_but_uses_fresh_worker_commits(self):
        probe, files, root, _ = reader_fixture()
        mutate(files, root + '/state.json', processed=6)
        value = probe(); self.assertTrue(value['verified'], value)
        self.assertEqual(value['processed'], 12)

    def test_main_atomically_updates_files_only_for_newer_samples(self):
        raw = snapshot(); raw['migrations'] = {'facts': migration('facts'), 'patent': migration()}
        with tempfile.TemporaryDirectory() as directory, patch.object(progress, 'HERE', Path(directory)):
            with patch.object(progress, 'collect', AsyncMock(return_value=raw)), redirect_stdout(io.StringIO()):
                asyncio.run(progress.main(as_json=True))
            latest = Path(directory) / 'progress-table-latest.md'; baseline = Path(directory) / 'progress-table-last.json'
            saved = json.loads(baseline.read_text(encoding='utf8'))
            self.assertEqual(latest.read_text(encoding='utf8'), progress.markdown(saved))
            original = latest.read_bytes(), baseline.read_bytes()
            stale = copy.deepcopy(raw); stale['observed_at'] = '2026-11-16T05:00:00+00:00'
            with patch.object(progress, 'collect', AsyncMock(return_value=stale)), redirect_stdout(io.StringIO()):
                asyncio.run(progress.main(as_markdown=True))
            self.assertEqual((latest.read_bytes(), baseline.read_bytes()), original)
            newer = copy.deepcopy(raw); newer['observed_at'] = '2026-11-16T06:01:00+00:00'
            with patch.object(progress, 'collect', AsyncMock(return_value=newer)), redirect_stdout(io.StringIO()):
                asyncio.run(progress.main())
            self.assertEqual(latest.read_text(encoding='utf8'), progress.markdown(json.loads(baseline.read_text(encoding='utf8'))))
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
