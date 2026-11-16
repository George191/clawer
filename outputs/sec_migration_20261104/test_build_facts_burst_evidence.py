"""Offline coverage, same-server data flow and output privacy checks for facts evidence."""
import ast
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from outputs.sec_migration_20261104 import build_facts_burst_evidence as subject


def encoded(rows):
    return ''.join(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n' for row in rows).encode()


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        root = Path(directory.name); self.baseline = root / 'baseline'; self.naming = root / 'naming'
        self.baseline.mkdir(); self.naming.mkdir(); self.output = root / 'evidence'
        self.rows, approved, planned = {}, [], []
        for number in range(6):
            lower, last = f'{number * 10:024x}', f'{number * 10 + 1:024x}'
            self.rows[number] = [{'mongo_id': value, 'record_id': f'private-fixture-{number}-{ordinal}'}
                                 for ordinal, value in enumerate((lower, last))]
            raw = encoded(self.rows[number]); ids = ''.join(row['record_id'] + '\n' for row in self.rows[number]).encode()
            approved.append((lower, last, 2, subject.sha(raw), subject.sha(ids)))
            planned.append({'lower': lower, 'upper': f'{number * 10 + 10:024x}' if number < 5 else None})
            (self.baseline / f'worker-{number}.json').write_text(json.dumps({
                'worker': number, 'processed': 2, 'last_id': last, 'last_error': None, 'done': False}))
        plan = {'workers': 6, 'batch_size': 3000, 'mongo_uuid': str(bytes.fromhex(subject.UUID)), 'parts': planned}
        plan_raw = json.dumps(plan).encode(); (self.baseline / 'plan.json').write_bytes(plan_raw)
        (self.baseline / 'state.json').write_text(json.dumps({'stage': 'paused', 'workers': 0, 'processed': 12}))
        (self.baseline / 'STOP').write_text('Keep the copying baseline paused.\n')
        (self.naming / 'facts-cdc-state.json').write_text(json.dumps({
            'stage': 'naming_complete_waiting_backfill', 'cdc_active': False, 'observed_at': 'first'}))
        stack = ExitStack(); self.addCleanup(stack.close)
        for name, value in {'BASELINE': self.baseline, 'NAMING': self.naming, 'OUTPUT': self.output,
                            'APPROVED': tuple(approved), 'PLAN_SHA256': subject.sha(plan_raw), 'TOTAL': 12}.items():
            stack.enter_context(patch.object(subject, name, value))

    def collect(self, syncer, output, part):
        path = output / part['records_path']; path.write_bytes(encoded(self.rows[part['worker']]))
        return subject.validate_records(path, part)

    def test_six_source_server_files_are_bound_and_stdout_contains_only_hashes_and_counts(self):
        with patch.object(subject, 'native_syncer', return_value={'id': 'native-id'}) as native, \
             patch.object(subject, 'collect_part', side_effect=self.collect) as collect, redirect_stdout(io.StringIO()) as output:
            subject.main()
        summary = json.loads(output.getvalue()); manifest_path = Path(summary['manifest'])
        self.assertEqual(summary['total'], 12); self.assertEqual(collect.call_count, 6)
        self.assertEqual(sorted(call.args[2]['worker'] for call in collect.call_args_list), list(range(6)))
        self.assertNotIn('private-fixture', output.getvalue())
        manifest = json.loads(manifest_path.read_bytes()); self.assertEqual(manifest['protocol'], subject.PROTOCOL)
        self.assertEqual(manifest['mongo_uuid_hex'], subject.UUID)
        self.assertEqual(sum(part['actual_count'] for part in manifest['parts']), 12)
        self.assertEqual(summary['manifest_sha256'], subject.sha(manifest_path.read_bytes()))
        original = manifest_path.read_bytes()
        gate = self.naming / 'facts-cdc-state.json'; value = json.loads(gate.read_bytes()); value['observed_at'] = 'later'
        gate.write_text(json.dumps(value))
        with patch.object(subject, 'native_syncer', return_value={'id': 'native-id'}), \
             patch.object(subject, 'collect_part', side_effect=self.collect):
            subject.build()
        self.assertEqual(manifest_path.read_bytes(), original)
        native.assert_called_once()

    def test_changed_plan_or_checkpoint_blocks_before_mongo_reader(self):
        path = self.baseline / 'worker-2.json'; value = json.loads(path.read_bytes()); value['last_id'] = f'{999:024x}'
        path.write_text(json.dumps(value))
        with patch.object(subject, 'native_syncer') as native:
            with self.assertRaisesRegex(RuntimeError, 'committed worker prefix changed'):
                subject.build()
        native.assert_not_called()

    def test_missing_stop_or_released_naming_gate_blocks(self):
        (self.naming / 'baseline-ready.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'naming gate changed'):
            subject.snapshot()
        (self.naming / 'baseline-ready.json').unlink(); (self.baseline / 'STOP').unlink()
        with self.assertRaises(FileNotFoundError):
            subject.snapshot()

    def test_source_controls_changed_during_read_do_not_publish_manifest(self):
        def collect(syncer, output, part):
            result = self.collect(syncer, output, part)
            if part['worker'] == 0:
                (self.baseline / 'STOP').write_text('Changed stop fence.')
            return result
        with patch.object(subject, 'native_syncer', return_value={'id': 'native-id'}), \
             patch.object(subject, 'collect_part', side_effect=collect):
            with self.assertRaisesRegex(RuntimeError, 'changed during collection'):
                subject.build()
        self.assertFalse((self.output / 'facts-burst-manifest-20261116.json').exists())

    def test_private_docker_stdout_is_written_to_same_host_file(self):
        part = subject.snapshot()[0][0]; self.output.mkdir()
        def run(command, **kwargs):
            self.assertEqual(command[:6], ['docker', 'exec', '-i', '-e', 'PYTHONPATH=/app', 'native-id'])
            self.assertEqual(command[6:], ['python', '-B', '-'])
            self.assertTrue(kwargs['stdout'].writable()); self.assertEqual(kwargs['stderr'], subject.subprocess.PIPE)
            self.assertNotIn('directConnection', kwargs['input'])
            self.assertNotIn('private-fixture', kwargs['input'])
            kwargs['stdout'].write(encoded(self.rows[0]))
            return SimpleNamespace(returncode=0)
        with patch.object(subject.subprocess, 'run', side_effect=run), redirect_stdout(io.StringIO()) as output:
            result = subject.collect_part({'id': 'native-id'}, self.output, part)
        self.assertEqual(result['actual_count'], 2); self.assertEqual(output.getvalue(), '')
        self.assertEqual((self.output / part['records_path']).read_bytes(), encoded(self.rows[0]))
        self.assertFalse(list(self.output.glob('*.partial-*')))

    def test_reader_failure_never_echoes_record_data_or_publishes_partial_file(self):
        part = subject.snapshot()[0][0]; self.output.mkdir()
        with patch.object(subject.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stderr='private-fixture')), \
             redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(RuntimeError, 'bounded reader failed') as error:
                subject.collect_part({'id': 'native-id'}, self.output, part)
        self.assertNotIn('private-fixture', str(error.exception)); self.assertEqual(output.getvalue(), '')
        self.assertFalse(list(self.output.iterdir()))

    def test_record_file_hash_boundaries_and_duplicate_ids_are_enforced(self):
        part = subject.snapshot()[0][0]; path = self.output; path.write_bytes(encoded(self.rows[0]))
        subject.validate_records(path, part)
        with self.assertRaisesRegex(RuntimeError, 'duplicate record ID'):
            subject.validate_records(path, part, {self.rows[0][0]['record_id']})
        rows = self.rows[0]; changed = [dict(row) for row in rows]; changed[0]['record_id'] = 'different'
        path.write_bytes(encoded(changed))
        with self.assertRaisesRegex(RuntimeError, 'hashes differ'):
            subject.validate_records(path, part)
        changed[0]['mongo_id'] = f'{999:024x}'; path.write_bytes(encoded(changed))
        with self.assertRaisesRegex(RuntimeError, 'ordered prefix'):
            subject.validate_records(path, part)

    def test_native_syncer_requires_unique_existing_service_and_host_mapping(self):
        with patch.object(subject.subprocess, 'check_output', return_value='one\ntwo\n'):
            with self.assertRaisesRegex(RuntimeError, 'expected one native syncer'):
                subject.native_syncer()
        row = {'State': {'Running': True}, 'HostConfig': {'ExtraHosts': []}, 'Image': 'same-image'}
        with patch.object(subject.subprocess, 'check_output', side_effect=['native-id\n', json.dumps([row])]):
            with self.assertRaisesRegex(RuntimeError, 'host mapping is absent'):
                subject.native_syncer()


class MongoReaderTests(unittest.TestCase):
    def test_indexed_closed_prefix_pages_retry_without_advancing_cursor(self):
        function = next(node for node in ast.parse(subject.WORKER).body if isinstance(node, ast.FunctionDef))
        sleep = Mock(); environment = {'ObjectId': lambda value: value, 'PyMongoError': TimeoutError,
                                      'time': SimpleNamespace(sleep=sleep)}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<bounded-worker>', 'exec'), environment)
        documents = [{'_id': f'{number:024x}', '_meta': {'record_id': f'fixture-{number}'}} for number in range(3001)]
        calls = []
        class Cursor:
            def __init__(self, rows): self.rows = rows
            def sort(self, key, direction):
                self_case.assertEqual((key, direction), ('_id', 1)); return self
            def hint(self, value): self_case.assertEqual(value, '_id_'); return self
            def limit(self, value): self_case.assertEqual(value, 3000); self.rows = self.rows[:value]; return self
            def batch_size(self, value): self_case.assertEqual(value, 3000); return self
            def max_time_ms(self, value): self_case.assertEqual(value, 30000); return self
            def __iter__(self): return iter(self.rows)
        class Collection:
            def find(self, query, projection):
                calls.append(query)
                self_case.assertEqual(projection, {'_id': 1, '_meta.record_id': 1})
                if len(calls) == 1: raise TimeoutError('bounded transient read')
                bounds = query['_id']; self_case.assertEqual(bounds['$lte'], documents[-1]['_id'])
                lower = bounds.get('$gte', bounds.get('$gt'))
                return Cursor([row for row in documents if row['_id'] <= bounds['$lte'] and
                               (row['_id'] >= lower if '$gte' in bounds else row['_id'] > lower)])
        self_case = self
        part = {'lower': documents[0]['_id'], 'upper_inclusive': documents[-1]['_id'], 'processed': 3001}
        rows = list(environment['read_rows'](Collection(), part))
        self.assertEqual(len(rows), 3001); sleep.assert_called_once_with(1)
        self.assertEqual(calls[0], calls[1]); self.assertEqual(calls[2]['_id']['$gt'], documents[2999]['_id'])
        self.assertEqual(calls[-1]['_id']['$gt'], documents[-1]['_id'])


if __name__ == '__main__':
    unittest.main()
