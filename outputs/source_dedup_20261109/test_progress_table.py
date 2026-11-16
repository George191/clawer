import copy
import ast
import asyncio
import hashlib
import io
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from outputs.source_dedup_20261109 import progress_table as progress
from outputs.source_dedup_20261109.progress_table import PATENT_RUN_ID, PROBE, markdown, plain_table, summarize


def snapshot():
    tables = [('ts_rds', 'rds_filing'), ('ts_ods', 'ods_filing'), ('ts_ods', 'ods_filing_document'),
              ('ts_rds', 'rds_financial_fact'), ('ts_ods', 'ods_financial_fact')]
    units = [{'unit': i, 'worker': i % 6, 'ordinal': 0, 'schema': schema, 'table': table, 'leaf': table + '_p00',
              'old': 'old', 'new': 'new', 'data_type': table.split('_', 1)[1]}
             for i, (schema, table) in enumerate(tables)]
    # A facts partition has two source aliases, so it completes only when both units do.
    units.append({**units[3], 'unit': 5, 'worker': 5, 'old': 'other_old'})
    return {'observed_at': '2026-11-09T10:00:00+00:00', 'run': '/app/output/run', 'plan_sha256': 'plan',
            'layout': {'workers': 6, 'page_size': 3000, 'sql_batch_size': 300}, 'units': units,
            'checkpoints': [], 'backends': [], 'worker_errors': [], 'stop_requested': False}


def patent(**changes):
    return {'verified': True, 'run_id': PATENT_RUN_ID, 'sampled_at': '2026-11-09T10:00:00+00:00',
            'observed_at': '2026-11-09T10:00:00+00:00', 'stage': 'name', 'name_processed': 1000,
            'changed': 999, 'total': 2000, 'active_workers': 6, 'workers': 6,
            'logical_partitions': 8, 'batch_size': 3000,
            'completed_workers': 2, 'cdc_active': False, 'last_error': None, **changes}


def tle_es():
    manifest = {'run_id': 'tle-es-run01', 'workers': 6, 'batch_size': 3000, 'total_documents': 100}
    fingerprint = hashlib.sha256(json.dumps(manifest).encode()).hexdigest()
    return {'verified': True, 'sampled_at': '2026-11-12T10:00:00+00:00',
            'manifest': manifest, 'manifest_sha256': fingerprint,
            'checkpoints': [{'run_id': manifest['run_id'], 'manifest_sha256': fingerprint, 'worker': 0,
                             'updated_at': '2026-11-12T09:59:00+00:00', 'es_read': 150,
                             'committed_documents': 50, 'ods_inserted': 30, 'ods_existing': 20,
                             'done': False, 'last_error': None, 'pass_number': 1, 'file_sha256': 'a' * 64}]}


def cursor_tle_es():
    raw = tle_es()
    raw['manifest']['total_documents'] = 1000
    fingerprint = hashlib.sha256(json.dumps(raw['manifest']).encode()).hexdigest()
    raw['manifest_sha256'] = fingerprint
    template = raw['checkpoints'][0]
    raw['checkpoints'] = [{**template, 'worker': number, 'manifest_sha256': fingerprint, 'pass_number': 4,
        'scan_documents': 100, 'scan_cursor_seq_no': 1000 + number, 'scan_global_checkpoint': 2000,
        'scroll_restarts': 0, 'scroll_consecutive_failures': 0, 'last_scroll_error': None} for number in range(6)]
    return raw


def archive_probe():
    function = next(node for node in ast.parse(PROBE).body if isinstance(node, ast.FunctionDef)
                    and node.name == 'read_tle_archive')
    files = {}
    class MemoryPath:
        def __init__(self, value): self.value = value
        def __truediv__(self, value): return MemoryPath(self.value + '/' + value)
        def exists(self): return self.value in files
        def read_bytes(self): return files[self.value].encode()
    environment = {'datetime': datetime, 'timezone': timezone, 'json': json,
                   'hashlib': hashlib, 'Path': MemoryPath}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<archive-probe>', 'exec'), environment)
    root = '/app/output/tle_archive_history_20261112'
    missing = [(datetime(2024, 1, 1) + timedelta(days=n)).date().isoformat() for n in range(53)]
    files[root + '/gap_audit.json'] = json.dumps({'offline': True, 'six_disjoint_worker_manifests_verified': True,
        'recoverable_tle_records': 38, 'target_days': missing + ['2025-01-22'],
        'target_days_with_evidence': {'2025-01-22': ['original.zip']}, 'target_days_without_evidence': missing})
    manifest = {'run_id': 'archive-run01', 'workers': 6, 'batch_size': 3000, 'total_documents': 38,
        'source_kind': 'original_mike_archive', 'data_source': 'orbital_mike_mccants_archive', 'target_epoch_day': '2025-01-22'}
    files[root + '/manifest.json'] = json.dumps(manifest)
    fingerprint = hashlib.sha256(files[root + '/manifest.json'].encode()).hexdigest()
    for number in range(6):
        count = 7 if number < 2 else 6
        files[root + f'/checkpoints/worker-{number}.json'] = json.dumps({'run_id': manifest['run_id'],
            'manifest_sha256': fingerprint, 'worker': number, 'pid': 200 + number, 'source_read': count,
            'committed_documents': count, 'ods_inserted': count, 'ods_existing': 0, 'done': True,
            'rolled_back': False, 'last_error': None, 'updated_at': '2026-11-12T06:00:00Z'})
    result = {'run_id': manifest['run_id'], 'manifest_sha256': fingerprint, 'complete': True,
        'committed_documents': 38, 'ods_inserted': 38, 'ods_existing': 0, 'worker_pids': list(range(200, 206))}
    return environment['read_tle_archive'], files, root, result


class ProgressTests(unittest.TestCase):
    def test_direct_report_script_keeps_shared_owner_import_and_help_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-B', str(Path(progress.__file__).resolve()), '--help'],
                                    cwd=directory, capture_output=True, text=True, timeout=10, check=True)
        self.assertIn('--markdown', result.stdout)
        self.assertIn('--json', result.stdout)

    def test_tle_cursor_diagnostics_aggregate_separately_from_ods_coverage(self):
        raw = {**snapshot(), 'tle_es': cursor_tle_es()}
        value = summarize(raw)
        state = value['tle_es']; diagnostics = state['diagnostics']
        self.assertTrue(state['verified'])
        self.assertEqual((state['committed_documents'], state['completion_percent']), (300, 30))
        self.assertIsNone(state['diagnostics_error'])
        self.assertEqual((diagnostics['workers'], diagnostics['scan_documents'], diagnostics['scroll_restarts']), (6, 600, 0))
        self.assertEqual(diagnostics['worker_cursors'][4], {'worker': 4, 'seq_no': 1004, 'global_checkpoint': 2000})
        for render in (markdown, plain_table):
            report = render(value)
            self.assertIn('已完整核对读取 600 条（6/6 worker，不计入 ODS 提交量）', report)
            self.assertIn('worker-4=1004/2000', report)
            self.assertIn('序号不是条数', report)

    def test_tle_cursor_recovered_historical_error_does_not_mark_current_failure(self):
        raw = {**snapshot(), 'tle_es': cursor_tle_es()}
        raw['tle_es']['checkpoints'][4].update(scroll_restarts=3, last_scroll_error={
            'type': 'ESFailure', 'reason': 'ES request failed with HTTP 502', 'at': '2026-11-12T09:58:00Z'})
        value = summarize(raw); state = value['tle_es']
        self.assertEqual((state['stage'], state['errors'], state['diagnostics']['retrying_workers']), ('已有检查点', [], []))
        for render in (markdown, plain_table):
            report = render(value)
            self.assertIn('累计重建 3 次；当前连续失败：无', report)
            self.assertIn('worker-4: ES request failed with HTTP 502 @ 2026-11-12T09:58:00Z（历史保留）', report)

    def test_tle_cursor_current_continuous_failures_do_not_override_main_stage(self):
        raw = {**snapshot(), 'tle_es': cursor_tle_es()}
        state = raw['tle_es']['checkpoints'][4]
        state.update(scroll_restarts=4, scroll_consecutive_failures=2, last_scroll_error={
            'type': 'ESFailure', 'reason': 'ES scroll context expired', 'at': '2026-11-12T09:59:00Z'})
        value = summarize(raw)
        self.assertEqual(value['tle_es']['stage'], '已有检查点')
        self.assertEqual(value['tle_es']['diagnostics']['retrying_workers'], [{'worker': 4, 'consecutive_failures': 2}])
        for render in (markdown, plain_table):
            self.assertIn('当前连续失败：worker-4=2 次', render(value))
        state['last_error'] = {'type': 'ESFailure', 'at': '2026-11-12T09:59:00Z'}
        self.assertEqual(summarize(raw)['tle_es']['stage'], '错误')

    def test_tle_cursor_diagnostics_are_optional_for_legacy_checkpoints(self):
        raw = {**snapshot(), 'tle_es': tle_es()}
        raw['tle_es']['checkpoints'][0]['scroll_restarts'] = 2
        value = summarize(raw)
        self.assertEqual(value['tle_es']['committed_documents'], 50)
        self.assertIsNone(value['tle_es']['diagnostics'])
        self.assertIsNone(value['tle_es']['diagnostics_error'])
        for render in (markdown, plain_table):
            report = render(value)
            self.assertNotIn('游标诊断', report)
            self.assertNotIn('已完整核对读取', report)

    def test_tle_malformed_diagnostics_never_hide_verified_commits_or_delta(self):
        for changes in ({'scan_documents': 'bad'}, {'scan_cursor_seq_no': True}, {'scan_global_checkpoint': 1},
                        {'scroll_consecutive_failures': 1}, {'last_scroll_error': {'type': 'ESFailure',
                        'reason': 'failure', 'at': '2026-11-12T10:00:00'}},
                        {'last_scroll_error': {'type': 'ESFailure', 'reason': 'private\nvalue', 'at': '2026-11-12T10:00:00Z'}}):
            with self.subTest(changes=changes):
                raw = {**snapshot(), 'tle_es': cursor_tle_es()}
                previous = summarize(raw)
                raw['tle_es']['sampled_at'] = '2026-11-12T10:01:00Z'
                raw['tle_es']['checkpoints'][4].update(changes)
                value = summarize(raw, previous); state = value['tle_es']
                self.assertTrue(state['verified'])
                self.assertEqual((state['committed_documents'], state['delta'], state['stage']), (300, 0, '已有检查点'))
                self.assertEqual(state['diagnostics_error'], 'InvalidTleEsDiagnostics')
                for render in (markdown, plain_table):
                    report = render(value)
                    self.assertIn('主迁移 | ES→ODS | 300 |', report)
                    self.assertIn('ES 游标诊断未验证', report)
                    self.assertNotIn('private', report)

    def test_tle_probe_keeps_optional_cursor_fields_without_reading_business_payloads(self):
        function = next(node for node in ast.parse(PROBE).body if isinstance(node, ast.FunctionDef)
                        and node.name == 'read_tle_es')
        root = '/app/output/tle_es_history_20261112'
        raw = cursor_tle_es()
        files = {root + '/manifest.json': json.dumps(raw['manifest']).encode()}
        for state in raw['checkpoints']:
            files[root + f"/checkpoints/worker-{state['worker']}.json"] = json.dumps({**state, 'business_payload': 'private'}).encode()
        class MemoryPath:
            def __init__(self, value): self.value = value
            def __truediv__(self, value): return MemoryPath(self.value + '/' + value)
            def exists(self): return self.value in files
            def read_bytes(self): return files[self.value]
        environment = {'datetime': datetime, 'timezone': timezone, 'json': json,
                       'hashlib': hashlib, 'Path': MemoryPath}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<tle-es-probe>', 'exec'), environment)
        result = environment['read_tle_es']()
        self.assertTrue(result['verified'])
        self.assertEqual(result['checkpoints'][4]['scan_cursor_seq_no'], 1004)
        self.assertNotIn('private', json.dumps(result))

    def test_real_writer_structured_error_preserves_current_committed_progress_and_reports_failure(self):
        path = Path(__file__).resolve().parents[1] / 'progress_display_20261113/raw-progress-snapshot.json'
        raw = json.loads(path.read_text(encoding='utf8'))
        for unit in raw['units']:
            unit['ordinal'] = int(unit['leaf'].rsplit('_p', 1)[1])
        value = summarize(raw)
        state = value['tle_es']
        self.assertTrue(state['verified'])
        self.assertEqual(state['committed_documents'], 44727000)
        self.assertEqual(state['ods_inserted'] + state['ods_existing'], 44727000)
        self.assertEqual(state['stage'], '错误')
        self.assertEqual(len(state['pg_sessions']), 5)
        self.assertIsNone(state['delta'])
        for render in (markdown, plain_table):
            report = render(value)
            self.assertIn('主迁移 | ES→ODS | 44,727,000 | 错误', report)
            self.assertIn('worker-4: ESFailure', report)
            self.assertIn('2026-11-12T13:12:25.001622+00:00', report)
            self.assertIn('PG 工作会话 5/6', report)

    def test_tle_summary_leads_with_distinct_main_and_archive_counts_and_never_reuses_unverified(self):
        probe, files, root, result = archive_probe(); files[root + '/result.json'] = json.dumps(result)
        raw = snapshot(); raw.update(tle_es=tle_es(), tle_archive=probe())
        value = summarize(raw)
        for render in (markdown, plain_table):
            report = render(value)
            self.assertLess(report.index('主迁移'), report.index('附加归档补录'))
            self.assertLess(report.index('附加归档补录'), report.index('filing/RDS'))
            self.assertIn('主迁移 | ES→ODS | 50 |', report)
            self.assertIn('附加归档补录 | 原始归档→ODS | 38 | 已完成', report)
        raw['tle_es'] = {'verified': False, 'error_class': 'JSONDecodeError'}
        current = summarize(raw, value)
        for render in (markdown, plain_table):
            report = render(current)
            self.assertIn('主迁移 | ES→ODS | 未验证 | 未验证', report)
            self.assertNotIn('主迁移 | ES→ODS | 50 |', report)
            self.assertIn('附加归档补录 | 原始归档→ODS | 38 | 已完成', report)

    def test_main_atomically_refreshes_canonical_markdown_only_with_newer_json_sample(self):
        raw = snapshot(); raw['tle_es'] = tle_es()
        with tempfile.TemporaryDirectory() as directory, patch.object(progress, 'HERE', Path(directory)):
            with patch.object(progress, 'collect', AsyncMock(return_value=raw)), redirect_stdout(io.StringIO()):
                asyncio.run(progress.main(as_json=True))
            latest = Path(directory) / 'progress-table-latest.md'
            baseline = Path(directory) / 'progress-table-last.json'
            saved = json.loads(baseline.read_text(encoding='utf8'))
            self.assertEqual(latest.read_text(encoding='utf8'), markdown(saved))
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])
            original = (latest.read_bytes(), baseline.read_bytes())
            stale = copy.deepcopy(raw); stale['observed_at'] = '2026-11-09T09:00:00+00:00'
            with patch.object(progress, 'collect', AsyncMock(return_value=stale)), redirect_stdout(io.StringIO()):
                asyncio.run(progress.main(as_markdown=True))
            self.assertEqual((latest.read_bytes(), baseline.read_bytes()), original)
            newer = copy.deepcopy(raw); newer['observed_at'] = '2026-11-09T10:01:00+00:00'
            newer['tle_es']['checkpoints'][0].update(committed_documents=60, ods_inserted=40)
            with patch.object(progress, 'collect', AsyncMock(return_value=newer)), redirect_stdout(io.StringIO()):
                asyncio.run(progress.main())
            self.assertIn('主迁移 | ES→ODS | 60 |', latest.read_text(encoding='utf8'))
            self.assertEqual(latest.read_text(encoding='utf8'), markdown(json.loads(baseline.read_text(encoding='utf8'))))

    def test_archive_requires_six_bound_committed_checkpoints_and_result_without_changing_other_progress(self):
        probe, files, root, result = archive_probe()
        raw = snapshot(); raw['tle_es'] = tle_es(); baseline = summarize(raw)
        raw['tle_archive'] = probe()
        self.assertEqual(raw['tle_archive']['stage'], '待验收')
        self.assertEqual(raw['tle_archive']['restored_days'], 0)
        files[root + '/result.json'] = json.dumps(result)
        raw['tle_archive'] = probe(); value = summarize(raw)
        self.assertEqual((value['tle_archive']['stage'], value['tle_archive']['committed_documents']), ('已完成', 38))
        self.assertEqual(value['rows'], baseline['rows'])
        self.assertEqual(value['totals'], baseline['totals'])
        self.assertEqual(value['tle_es'], baseline['tle_es'])
        for render in (markdown, plain_table):
            report = render(value)
            for text in ('filing/RDS', 'facts/ODS', 'patent / Mongo', 'TLE / ES', '原始归档补缺',
                         '已完成', '6/6', '2025-01-22', '其余 53 日', '保留缺口'):
                self.assertIn(text, report)
        del files[root + '/manifest.json']
        raw['tle_archive'] = probe()
        self.assertEqual(raw['tle_archive']['error_class'], 'NotStarted')
        self.assertNotIn('committed_documents', raw['tle_archive'])
        self.assertIn('状态：待执行；计划 38 条', markdown(summarize(raw)))

    def test_archive_corrupt_or_foreign_proofs_drop_counters_instead_of_reusing_success(self):
        probe, files, root, result = archive_probe(); files[root + '/result.json'] = json.dumps(result)
        previous = summarize({**snapshot(), 'tle_archive': probe()})
        for section, changes in (('result', {'run_id': 'other'}), ('result', {'ods_existing': 1}),
                                 ('result', {'committed_documents': 37}),
                                 ('cp', {'worker': 1}), ('cp', {'manifest_sha256': '0' * 64}),
                                 ('cp', {'committed_documents': 6}), ('cp', {'rolled_back': True}),
                                 ('cp', {'done': False}), ('cp', {'worker': True})):
            with self.subTest(section=section, changes=changes):
                probe, files, root, result = archive_probe()
                files[root + '/result.json'] = json.dumps(result)
                path = root + ('/result.json' if section == 'result' else '/checkpoints/worker-0.json')
                files[path] = json.dumps({**json.loads(files[path]), **changes})
                current = summarize({**snapshot(), 'tle_archive': probe()}, previous)['tle_archive']
                self.assertFalse(current['verified'])
                self.assertNotIn('committed_documents', current)
        for filename in ('manifest.json', 'checkpoints/worker-0.json', 'result.json', 'gap_audit.json'):
            probe, files, root, result = archive_probe(); files[root + '/result.json'] = json.dumps(result)
            files[root + '/' + filename] = '{'
            current = probe()
            self.assertFalse(current['verified'])
            self.assertNotIn('committed_documents', current)

    def test_tle_es_pg_sessions_are_independent_of_filing_and_report_wait_types(self):
        raw = snapshot(); raw['tle_es'] = tle_es()
        raw['backends'] = [{'application_name': 'source-dedup-plan-worker-0'}]
        baseline = summarize(raw)
        raw['tle_es']['pg_sessions'] = {'verified': True, 'rows': [
            {'pid': 100 + number, 'application_name': f'tle-es-history-{number}', 'state': 'active',
             'wait_event_type': 'IO' if number == 0 else None,
             'wait_event': 'WALSync' if number == 0 else None} for number in range(6)]}
        value = summarize(raw)
        self.assertEqual(value['workers_connected'], 1)
        self.assertEqual(value['rows'], baseline['rows'])
        self.assertEqual(value['totals'], baseline['totals'])
        self.assertEqual([row['worker'] for row in value['tle_es']['pg_sessions']], list(range(6)))
        for render in (markdown, plain_table):
            self.assertIn('PG 工作会话 6/6', render(value))
            self.assertIn('worker-0=IO/WALSync', render(value))
            self.assertIn('运行进程数未核验', render(value))
        self.assertIn("WHERE application_name ~ '^tle-es-history-[0-5]$'", PROBE)

    def test_tle_es_pg_query_failure_and_invalid_sessions_never_guess_running_or_reuse_old(self):
        raw = snapshot(); raw['tle_es'] = tle_es()
        row = {'pid': 100, 'application_name': 'tle-es-history-0', 'state': 'idle',
               'wait_event_type': 'Client', 'wait_event': 'ClientRead'}
        raw['tle_es']['pg_sessions'] = {'verified': True, 'rows': [row]}
        previous = summarize(raw)
        for sessions in ({'verified': False, 'error_class': 'QueryCanceledError'},
                         {'verified': True, 'rows': [row, row]},
                         {'verified': True, 'rows': [{**row, 'application_name': 'tle-es-history-6'}]},
                         {'verified': True, 'rows': [{**row, 'pid': True}]}):
            raw['tle_es']['pg_sessions'] = sessions
            value = summarize(raw, previous)
            self.assertTrue(value['tle_es']['verified'])
            self.assertEqual(value['tle_es']['committed_documents'], 50)
            self.assertIsNone(value['tle_es']['pg_sessions'])
            for render in (markdown, plain_table):
                self.assertIn('PG 工作会话 未核验/6', render(value))
                self.assertNotIn('PG 工作会话 1/6', render(value))
        raw['tle_es']['pg_sessions'] = {'verified': True, 'rows': []}
        self.assertIn('PG 工作会话 0/6', markdown(summarize(raw, previous)))

    def test_tle_es_committed_progress_is_independent_and_preserves_all_tables(self):
        raw = snapshot(); before = summarize(raw)
        raw['tle_es'] = tle_es()
        value = summarize(raw)
        progress = value['tle_es']
        self.assertTrue(progress['verified'])
        self.assertEqual(progress['completion_percent'], 50)
        self.assertEqual(progress['es_read'], 150)
        self.assertEqual(value['totals'], before['totals'])
        self.assertEqual(value['rows'], before['rows'])
        self.assertNotIn('active_workers', progress)
        for render in (markdown, plain_table):
            report = render(value)
            for scope in ('filing/RDS', 'filing/ODS', 'document/ODS', 'facts/RDS', 'facts/ODS', 'patent / Mongo', 'TLE / ES'):
                self.assertIn(scope, report)
            for text in ('50/100', '50.00%', 'ODS 已提交文档/总量', 'ODS 新插入', 'ODS 幂等命中',
                         '运行进程数未核验', '0/6', '完成即退出', 'collector→RDS→ODS，不依赖 ES'):
                self.assertIn(text, report)

    def test_tle_es_rejects_invalid_binding_counts_layout_and_torn_state_without_stale_values(self):
        raw = snapshot(); raw['tle_es'] = tle_es(); previous = summarize(raw)
        for mutate in (
                lambda p: p['manifest'].update(workers=8),
                lambda p: p['manifest'].update(batch_size=3001),
                lambda p: p['manifest'].update(total_documents=True),
                lambda p: p['manifest'].update(initial_documents_exact=True),
                lambda p: p['checkpoints'][0].update(manifest_sha256='0' * 64),
                lambda p: p['checkpoints'][0].update(run_id='other'),
                lambda p: p['checkpoints'][0].update(worker=True),
                lambda p: p['checkpoints'][0].update(committed_documents=51),
                lambda p: p['checkpoints'][0].update(es_read=49),
                lambda p: p['checkpoints'][0].update(ods_inserted=-1),
                lambda p: p['checkpoints'][0].update(done=1),
                lambda p: p['checkpoints'][0].update(last_error={'type': True, 'at': '2026-11-12T10:00:00Z'}),
                lambda p: p['checkpoints'][0].update(last_error={'type': 'ESFailure', 'at': '2026-11-12T10:00:00'}),
                lambda p: p['checkpoints'][0].update(last_error={'type': 'ESFailure'}),
                lambda p: p['checkpoints'][0].update(updated_at='2026-11-12T10:00:00'),
                lambda p: p['checkpoints'].append(copy.deepcopy(p['checkpoints'][0]))):
            with self.subTest(mutate=mutate):
                raw['tle_es'] = tle_es(); mutate(raw['tle_es'])
                value = summarize(raw, previous)['tle_es']
                self.assertFalse(value['verified'])
                self.assertEqual(value['error_class'], 'InvalidTleEsState')
                self.assertNotIn('committed_documents', value)
                self.assertIsNone(value['delta'])
        raw['tle_es'] = {'verified': False, 'error_class': 'JSONDecodeError'}
        self.assertNotIn('committed_documents', summarize(raw, previous)['tle_es'])

    def test_tle_es_missing_checkpoints_unknown_total_and_six_worker_completion(self):
        raw = snapshot(); raw['tle_es'] = tle_es()
        raw['tle_es']['checkpoints'] = []
        value = summarize(raw)['tle_es']
        self.assertEqual((value['stage'], value['committed_documents'], value['completed_workers']), ('待执行', 0, 0))
        raw['tle_es']['manifest']['total_documents'] = None
        raw['tle_es']['manifest']['initial_documents_exact'] = 107106838
        unknown = summarize(raw)
        self.assertIsNone(unknown['tle_es']['completion_percent'])
        for render in (markdown, plain_table):
            self.assertIn('初始精确基准：107,106,838', render(unknown))
            self.assertIn('0/未核定', render(unknown))
        raw['tle_es'] = tle_es()
        template = raw['tle_es']['checkpoints'][0]
        raw['tle_es']['checkpoints'] = [{**template, 'worker': n, 'es_read': 20,
            'committed_documents': 20 if n < 5 else 0, 'ods_inserted': 20 if n < 5 else 0,
            'ods_existing': 0, 'done': True} for n in range(6)]
        self.assertEqual(summarize(raw)['tle_es']['stage'], '待覆盖核对/补扫')
        raw['tle_es']['result'] = {'run_id': template['run_id'], 'manifest_sha256': template['manifest_sha256'],
            'pass_number': 1, 'complete': True, 'exitcodes': [0] * 6, 'source_count_before': 100,
            'ods_inserted': 100, 'ods_existing': 0,
            'source_count_after': 100, 'source_count_after_archive_audit': 100, 'unique_committed_documents': 100,
            'full_scan_documents': 100, 'worker_checkpoint_sha256': ['a' * 64] * 6,
            'source_consistency': 'live_index_bounded_query_reconciled_not_global_snapshot'}
        self.assertEqual(summarize(raw)['tle_es']['stage'], '完成')
        proof = copy.deepcopy(raw['tle_es']['result'])
        for changes in ({'source_count_after_archive_audit': 99}, {'ods_existing': 1}, {'manifest_sha256': '0' * 64},
                        {'worker_checkpoint_sha256': ['b' * 64] * 6}):
            raw['tle_es']['result'] = {**proof, **changes}
            self.assertFalse(summarize(raw)['tle_es']['verified'])
        raw['tle_es']['result'] = {**proof, 'complete': False, 'exitcodes': [1, 0, 0, 0, 0, 0]}
        self.assertEqual(summarize(raw)['tle_es']['stage'], '错误')
        for state in raw['tle_es']['checkpoints']:
            state.update(pass_number=2, done=False)
        self.assertEqual(summarize(raw)['tle_es']['stage'], '已有检查点')
        raw['tle_es']['result'] = proof
        for state in raw['tle_es']['checkpoints']:
            state.update(pass_number=1, done=True)
        raw['tle_es']['manifest']['total_documents'] = 101
        self.assertFalse(summarize(raw)['tle_es']['verified'])

    def test_tle_es_resumed_reading_has_separate_increment_without_inventing_commits(self):
        raw = snapshot(); raw['tle_es'] = tle_es()
        raw['tle_es']['checkpoints'][0]['pass_number'] = 2
        previous = summarize(raw)
        raw['tle_es']['sampled_at'] = '2026-11-12T10:02:00+00:00'
        raw['tle_es']['checkpoints'][0]['es_read'] = 210
        value = summarize(raw, previous)
        state = value['tle_es']
        self.assertEqual((state['es_read_delta'], state['es_reads_per_minute']), (60, 30))
        self.assertEqual((state['committed_documents'], state['delta'], state['rows_per_minute']), (50, 0, 0))
        self.assertEqual(state['stage'], '恢复读取/核对（本轮无新增提交）')
        self.assertEqual(value['totals'], previous['totals'])
        for render in (markdown, plain_table):
            report = render(value)
            self.assertIn('主迁移 | ES→ODS | 50 | 恢复读取/核对（本轮无新增提交）', report)
            self.assertIn('本轮 ES 读取计数新增 60；读取 30.0 条/分钟', report)
            self.assertIn('含重扫，不计入 ODS 已提交量', report)

    def test_tle_es_reading_counter_requires_progress_and_never_hides_errors(self):
        raw = snapshot(); raw['tle_es'] = tle_es()
        raw['tle_es']['checkpoints'][0]['pass_number'] = 2
        previous = summarize(raw)
        raw['tle_es']['sampled_at'] = '2026-11-12T10:02:00+00:00'
        for count, expected in ((150, 0), (149, None)):
            raw['tle_es']['checkpoints'][0]['es_read'] = count
            state = summarize(raw, previous)['tle_es']
            self.assertEqual(state['stage'], '已有检查点')
            self.assertEqual(state['es_read_delta'], expected)
        raw['tle_es']['checkpoints'][0].update(es_read=210,
            last_error={'type': 'ESFailure', 'at': '2026-11-12T10:01:00+00:00'})
        state = summarize(raw, previous)['tle_es']
        self.assertEqual(state['stage'], '错误')
        self.assertEqual(state['es_read_delta'], 60)
        self.assertIn('worker-0: ESFailure', state['errors'][0])
        raw['tle_es']['checkpoints'][0]['last_error'] = None
        raw['tle_es']['sampled_at'] = '2026-11-12T10:00:00+00:00'
        self.assertIsNone(summarize(raw, previous)['tle_es']['es_read_delta'])
        raw['tle_es']['sampled_at'] = '2026-11-12T10:02:00+00:00'
        raw['tle_es']['manifest']['run_id'] = 'another-run'
        raw['tle_es']['checkpoints'][0]['run_id'] = 'another-run'
        self.assertIsNone(summarize(raw, previous)['tle_es']['es_read_delta'])

    def test_tle_es_delta_binds_manifest_run_and_rejects_per_worker_rewind(self):
        raw = snapshot(); raw['tle_es'] = tle_es(); previous = summarize(raw)
        raw['tle_es']['sampled_at'] = '2026-11-12T10:02:00+00:00'
        raw['tle_es']['checkpoints'][0].update(committed_documents=60, ods_inserted=40)
        value = summarize(raw, previous)['tle_es']
        self.assertEqual((value['delta'], value['rows_per_minute']), (10, 5))
        raw['tle_es']['checkpoints'][0].update(committed_documents=40, ods_inserted=20)
        raw['tle_es']['checkpoints'].append({**raw['tle_es']['checkpoints'][0], 'worker': 1})
        self.assertIsNone(summarize(raw, previous)['tle_es']['delta'])
        raw['tle_es'] = tle_es()
        self.assertIsNone(summarize(raw, previous)['tle_es']['delta'])
        for key in ('run_id', 'manifest_sha256'):
            raw['tle_es'] = tle_es(); raw['tle_es']['sampled_at'] = '2026-11-12T10:02:00+00:00'
            changed = 'other' if key == 'run_id' else '0' * 64
            if key == 'run_id': raw['tle_es']['manifest'][key] = changed
            else: raw['tle_es'][key] = changed
            raw['tle_es']['checkpoints'][0][key] = changed
            self.assertIsNone(summarize(raw, previous)['tle_es']['delta'])

    def test_tle_es_probe_reads_safe_bound_files_and_never_reuses_bad_json(self):
        function = next(node for node in ast.parse(PROBE).body if isinstance(node, ast.FunctionDef)
                        and node.name == 'read_tle_es')
        files = {}
        class MemoryPath:
            def __init__(self, value): self.value = value
            def __truediv__(self, value): return MemoryPath(self.value + '/' + value)
            def exists(self): return self.value in files
            def read_bytes(self): return files[self.value].encode()
            def read_text(self): return files[self.value]
        environment = {'datetime': datetime, 'timezone': timezone, 'json': json,
                       'hashlib': hashlib, 'Path': MemoryPath}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<tle-es-probe>', 'exec'), environment)
        probe = environment['read_tle_es']; root = '/app/output/tle_es_history_20261112'
        self.assertEqual(probe()['error_class'], 'NotStarted')
        raw = tle_es(); raw['manifest']['business_document'] = {'secret': 'must not appear'}
        files[root + '/manifest.json'] = json.dumps(raw['manifest'])
        fingerprint = hashlib.sha256(files[root + '/manifest.json'].encode()).hexdigest()
        state = {**raw['checkpoints'][0], 'manifest_sha256': fingerprint, 'business_document': {'secret': 'must not appear'},
                 'last_error': {'type': 'ESFailure', 'at': '2026-11-12T10:00:00Z', 'business_document': {'secret': 'must not appear'}}}
        files[root + '/checkpoints/worker-0.json'] = json.dumps(state)
        result = probe()
        self.assertTrue(result['verified'])
        self.assertEqual(result['manifest_sha256'], fingerprint)
        self.assertNotIn('secret', json.dumps(result))
        self.assertTrue(summarize({**snapshot(), 'tle_es': result})['tle_es']['verified'])
        files[root + '/checkpoints/worker-0.json'] = '{'
        self.assertEqual(probe()['error_class'], 'JSONDecodeError')
        files[root + '/checkpoints/worker-0.json'] = json.dumps({**state, 'worker': 1})
        self.assertFalse(probe()['verified'])

    def test_worker_failure_uses_probe_last_error_in_both_report_formats(self):
        raw = snapshot()
        raw['worker_errors'] = [{'unit': 0, 'worker': 0, 'stage': 'failed', 'last_error': 'TimeoutError'}]
        value = summarize(raw)
        self.assertEqual(value['rows'][0]['stage'], '错误')
        for render in (plain_table, markdown):
            report = render(value)
            self.assertIn('filing/RDS', report)
            self.assertIn('TimeoutError', report)
            self.assertIn('patent / Mongo', report)

    def test_pending_is_not_counted_as_committed_and_unknown_totals_stay_unknown(self):
        raw = snapshot()
        raw['checkpoints'] = [{'unit': 0, 'worker': 0, 'processed': 10, 'renamed': 3, 'deduplicated': 7,
                               'stage': 'transaction_pending', 'pending': 'batch.pending', 'done': False}]
        value = summarize(raw)
        self.assertEqual(value['totals'], {'processed': 10, 'renamed': 3, 'deduplicated': 7, 'pending': 1})
        self.assertIsNone(value['rows'][0]['completion_percent'])
        self.assertIsNone(value['rows'][0]['delta'])
        self.assertIn('旧数据总量：未核定', markdown(value))

    def test_delta_uses_real_interval_and_rewind_is_unverified(self):
        raw = snapshot()
        raw['checkpoints'] = [{'unit': 0, 'worker': 0, 'processed': 100, 'renamed': 40, 'deduplicated': 60}]
        before = summarize(raw)
        raw['observed_at'] = '2026-11-09T10:10:00+00:00'
        raw['checkpoints'][0].update(processed=400, renamed=140, deduplicated=260)
        after = summarize(raw, before)
        self.assertEqual(after['rows'][0]['delta'], 300)
        self.assertEqual(after['rows'][0]['rows_per_minute'], 30)
        raw['checkpoints'][0].update(processed=50, renamed=20, deduplicated=30)
        rewind = summarize(raw, before)['rows'][0]
        self.assertIsNone(rewind['delta'])
        self.assertIn('回退', rewind['note'])

    def test_layout_assignment_overrides_historical_checkpoint_owner(self):
        raw = snapshot()
        raw['units'][5]['unit'] = 6
        raw['checkpoints'] = [{'unit': 3, 'worker': 3, 'done': True, 'processed': 0, 'renamed': 0, 'deduplicated': 0},
                              {'unit': 6, 'worker': 0, 'processed': 300, 'renamed': 100, 'deduplicated': 200}]
        raw['backends'] = [{'application_name': 'source-dedup-run-worker-4', 'pid': 123}]
        value = summarize(raw)
        facts = next(row for row in value['rows'] if row['table'] == 'ts_rds.rds_financial_fact')
        self.assertEqual(facts['workers'], [])
        self.assertEqual((facts['stage'], facts['processed']), ('无运行会话', 300))
        self.assertEqual(value['workers_connected'], 1)

    def test_history_report_uses_six_slots_without_phantom_facts_workers(self):
        raw = snapshot()
        for original in raw['units'][:3]:
            raw['units'].append({**original, 'unit': len(raw['units']), 'worker': 7,
                                 'ordinal': 1, 'leaf': original['table'] + '_p01'})
        raw['checkpoints'] = [{'unit': unit['unit'], 'worker': (unit['unit'] + 2) % 8,
                              'processed': 10, 'renamed': 3, 'deduplicated': 7, 'done': False}
                             for unit in raw['units']]
        raw['backends'] = [{'application_name': f'source-dedup-run-worker-{number}', 'pid': 100 + number}
                           for number in range(6)]
        value = summarize(raw)
        self.assertEqual([row['workers'] for row in value['rows']], [[0, 1], [2, 3], [4, 5], [], []])
        self.assertEqual([row['stage'] for row in value['rows']], ['修复中'] * 3 + ['无运行会话'] * 2)
        self.assertEqual(value['workers_connected'], 6)
        self.assertEqual(value['totals'], {'processed': 90, 'renamed': 27, 'deduplicated': 63, 'pending': 0})
        for render in (markdown, plain_table):
            report = render(value)
            self.assertIn('实际 worker 会话 6/6', report)
            self.assertIn('每轮读取 3,000 键', report)
            for row in value['rows']:
                line = next(line for line in report.splitlines() if f"{row['scope']}/{row['layer']}" in line)
                self.assertIn(row['stage'], line)
                if render is markdown:
                    self.assertIn(f"| {len(row['workers'])} | 无 |", line)

    def test_facts_partition_requires_both_old_sources_completed(self):
        raw = snapshot()
        raw['checkpoints'] = [{'unit': 3, 'worker': 3, 'done': True, 'processed': 20, 'renamed': 15, 'deduplicated': 5}]
        first = summarize(raw)['rows'][3]
        self.assertEqual((first['partitions_done'], first['partitions']), (0, 1))
        raw['checkpoints'].append({'unit': 5, 'worker': 5, 'done': True, 'processed': 10, 'renamed': 8, 'deduplicated': 2})
        final = summarize(raw)['rows'][3]
        self.assertEqual((final['partitions_done'], final['partitions']), (1, 1))
        self.assertEqual(final['processed'], 30)

    def test_invalid_counts_and_duplicate_units_fail_instead_of_reporting_progress(self):
        raw = snapshot()
        raw['checkpoints'] = [{'unit': 0, 'processed': 20, 'renamed': 3, 'deduplicated': 7}]
        with self.assertRaises(ValueError):
            summarize(raw)
        raw = snapshot(); raw['units'].append(copy.deepcopy(raw['units'][0]))
        with self.assertRaises(ValueError):
            summarize(raw)

    def test_patent_first_sample_does_not_change_pg_totals(self):
        raw = snapshot(); before = summarize(raw)
        raw['patent'] = patent()
        value = summarize(raw, before)
        self.assertEqual(value['totals'], before['totals'])
        self.assertEqual(len(value['rows']), 5)
        self.assertEqual(value['patent']['completion_percent'], 50)
        self.assertIsNone(value['patent']['delta'])
        self.assertEqual(json.loads(json.dumps(value))['patent']['name_processed'], 1000)

    def test_patent_ten_minute_delta_uses_its_state_time(self):
        raw = snapshot(); raw['patent'] = patent(); before = summarize(raw)
        raw['observed_at'] = '2026-11-09T10:20:00+00:00'
        raw['patent'] = patent(observed_at='2026-11-09T10:10:00+00:00',
                               sampled_at=raw['observed_at'], name_processed=1300)
        value = summarize(raw, before)['patent']
        self.assertEqual((value['delta'], value['interval_seconds'], value['rows_per_minute']), (300, 600, 30))

    def test_patent_run_change_does_not_reuse_delta(self):
        raw = snapshot(); raw['patent'] = patent(); before = summarize(raw)
        before['patent']['run_id'] = 'different-run'
        raw['patent'] = patent(observed_at='2026-11-09T10:10:00+00:00', name_processed=1300)
        self.assertIsNone(summarize(raw, before)['patent']['delta'])
        raw['patent']['run_id'] = 'unexpected-current-run'
        self.assertFalse(summarize(raw, before)['patent']['verified'])

    def test_patent_unchanged_time_and_counter_rewind_leave_rate_unverified(self):
        raw = snapshot(); raw['patent'] = patent(); before = summarize(raw)
        raw['observed_at'] = '2026-11-09T10:10:00+00:00'
        value = summarize(raw, before)['patent']
        self.assertIsNone(value['delta'])
        self.assertIn('状态时间未前进', value['note'])
        raw['patent'] = patent(observed_at=raw['observed_at'], name_processed=900)
        value = summarize(raw, before)['patent']
        self.assertIsNone(value['rows_per_minute'])
        self.assertIn('回退', value['note'])

    def test_patent_read_failure_preserves_pg_report_without_stale_counters(self):
        raw = snapshot(); raw['patent'] = patent(); before = summarize(raw)
        raw['patent'] = {'verified': False, 'error_class': 'FileNotFoundError',
                         'sampled_at': '2026-11-09T10:10:00+00:00'}
        value = summarize(raw, before)
        self.assertFalse(value['patent']['verified'])
        self.assertNotIn('name_processed', value['patent'])
        self.assertEqual(value['totals'], before['totals'])
        for render in (markdown, plain_table):
            self.assertIn('filing/RDS', render(value))
            self.assertIn('未验证：FileNotFoundError', render(value))
        for invalid in ({'changed': None}, {'active_workers': 7}, {'observed_at': None}, {'sampled_at': None}):
            raw['patent'] = patent(**invalid)
            value = summarize(raw, before)
            self.assertFalse(value['patent']['verified'])
            self.assertIn('未验证：InvalidPatentState', markdown(value))

    def test_patent_rejects_old_or_missing_layout_without_reusing_previous_counters(self):
        raw = snapshot(); raw['patent'] = patent(); before = summarize(raw)
        invalid_layouts = [patent(workers=8, batch_size=5000), patent(logical_partitions=6),
                           patent(completed_workers=9)]
        for field in ('batch_size', 'logical_partitions'):
            missing = patent()
            del missing[field]
            invalid_layouts.append(missing)
        for invalid in invalid_layouts:
            with self.subTest(patent=invalid):
                raw['patent'] = invalid
                value = summarize(raw, before)
                self.assertFalse(value['patent']['verified'])
                self.assertEqual(value['patent']['error_class'], 'InvalidPatentState')
                self.assertNotIn('name_processed', value['patent'])
                self.assertNotIn('completed_workers', value['patent'])
                self.assertIsNone(value['patent']['delta'])
                self.assertIsNone(value['patent']['rows_per_minute'])
                self.assertEqual(value['totals'], before['totals'])

    def test_patent_after_naming_still_reports_name_counter_in_both_tables(self):
        raw = snapshot(); raw['patent'] = patent(stage='trigger', name_processed=2000, changed=1,
                                                completed_workers=8, active_workers=6, cdc_active=True)
        value = summarize(raw)
        self.assertTrue(value['patent']['verified'])
        self.assertEqual(value['patent']['completion_percent'], 100)
        for render in (markdown, plain_table):
            output = render(value)
            self.assertIn('2,000/2,000', output)
            self.assertIn('100.00%', output)
            self.assertIn('patent 状态时间', output)
            self.assertIn('完成命名分区', output)
            self.assertIn('8/8', output)
            self.assertIn('6/6', output)
            self.assertIn('每批 3,000 条，8 个逻辑分区', output)
            self.assertIn('state.changed（当前阶段实际修改）：1', output)
        raw['patent'] = patent()
        output = markdown(summarize(raw))
        self.assertIn('| 6/6 | 2/8 | 否 |', output)

    def test_patent_probe_projects_only_safe_fields_and_bound_worker_completions(self):
        function = next(node for node in ast.parse(PROBE).body if isinstance(node, ast.FunctionDef)
                        and node.name == 'read_patent')
        files = {}
        class MemoryPath:
            def __init__(self, value): self.value = value
            def __truediv__(self, value): return MemoryPath(self.value + '/' + value)
            def read_text(self): return files[self.value]
        base = '/app/output/sec_migration_20261104'
        state = patent(); state['business_document'] = {'secret': 'must not appear'}
        files[base + '/patent-cdc-state.json'] = json.dumps(state)
        journals = base + '/patent-cdc-20261105T105239Z-b1750edb6390/journals'
        for number in range(8):
            files[journals + f'/name-worker-{number}.json'] = json.dumps({
                'run_id': PATENT_RUN_ID, 'phase': 'name', 'complete': number < 2,
                'business_document': {'secret': 'must not appear'}})
        environment = {'datetime': datetime, 'timezone': timezone, 'json': json, 'Path': MemoryPath}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<patent-probe>', 'exec'), environment)
        result = environment['read_patent']()
        self.assertTrue(result['verified'])
        self.assertEqual(result['completed_workers'], 2)
        self.assertEqual((result['workers'], result['logical_partitions'], result['batch_size']), (6, 8, 3000))
        self.assertNotIn('secret', json.dumps(result))
        files[journals + '/name-worker-0.json'] = json.dumps({'run_id': 'other', 'phase': 'name', 'complete': True})
        self.assertFalse(environment['read_patent']()['verified'])


if __name__ == '__main__':
    unittest.main()
