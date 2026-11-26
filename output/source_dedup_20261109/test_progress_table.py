"""Regression coverage for owner handover and independent progress scopes."""
import ast
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from output.source_dedup_20261109 import progress_table as progress

STAMP = '2026-11-26T07:00:00+00:00'


def migration(**changes):
    return {'verified': True, 'sampled_at': STAMP, 'plan_sha256': 'plan',
            'worker_processed': [100] * 6, 'processed': 600, 'total': None,
            'stage': 'running', 'pg_workers': list(range(6)), 'pending': 0, 'errors': [],
            'tail': None, 'workers': 6, 'sql_batch_size': 300, **changes}


class OwnerTests(unittest.TestCase):
    def setUp(self):
        functions = [n for n in ast.parse(progress.HOST).body
                     if isinstance(n, ast.FunctionDef) and n.name == 'validate_owner']
        self.source = Mock(return_value='a' * 64)
        self.container = {'Id': 'new-id', 'Image': 'image-id',
                          'Config': {'Image': 'current:image', 'Cmd': ['--root', '/app/output/sec_migration_20261104/current']},
                          'State': {'Running': True, 'ExitCode': 0, 'StartedAt': '2026-11-26T06:27:10.656383379Z'},
                          'RestartCount': 0,
                          'Mounts': [{'Type': 'bind', 'RW': True, 'Source': '/data/clawer/output', 'Destination': '/app/output'}]}
        self.owner = {'container': 'new-owner-name', 'container_id': 'new-id', 'image': 'current:image',
                      'image_id': 'image-id', 'root': '/data/clawer/output/sec_migration_20261104/current',
                      'source_sha256': 'a' * 64, 'worker_source_sha256': 'a' * 64,
                      'registry_source_sha256': 'a' * 64}
        self.run = Mock(side_effect=lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps([self.container])))
        env = {'subprocess': SimpleNamespace(run=self.run), 'json': json, 'Path': PurePosixPath,
               'source_sha': self.source, 're': re, 'datetime': datetime}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<owner-check>', 'exec'), env)
        self.validate = env['validate_owner']

    def test_new_generation_name_is_verified_by_identity_not_name_prefix(self):
        value = self.validate('facts', self.owner)
        self.assertTrue(value['verified'])
        self.assertEqual(value['id'], 'new-id')
        self.assertEqual(value['sample_started_at'], '2026-11-26T06:27:10.656383Z')
        self.assertEqual(self.source.call_count, 3)

    def test_stopped_owner_is_observed_instead_of_aborting_collection(self):
        self.container['State']['Running'] = False
        self.assertFalse(self.validate('patent', self.owner)['container_running'])

    def test_wrong_container_image_entrypoint_or_mount_is_rejected(self):
        for area, key, bad in [('root', 'Id', 'different-id'), ('root', 'Image', 'other-image'),
                               ('Config', 'Image', 'other:tag'), ('Config', 'Cmd', ['--root', '/other'])]:
            with self.subTest(key=key, area=area):
                target = self.container if area == 'root' else self.container[area]
                old = target[key]; target[key] = bad
                with self.assertRaises(ValueError): self.validate('facts', self.owner)
                target[key] = old
        self.container['Mounts'][0]['Source'] = '/unrelated'
        with self.assertRaisesRegex(ValueError, 'mount'): self.validate('facts', self.owner)

    def test_source_or_registry_drift_is_rejected(self):
        self.source.side_effect = ['a' * 64, 'a' * 64, 'b' * 64]
        with self.assertRaisesRegex(ValueError, 'registry'): self.validate('facts', self.owner)
        self.source.side_effect = None; self.source.return_value = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'source SHA'): self.validate('facts', self.owner)

    def test_missing_container_is_explicit(self):
        self.run.side_effect = None; self.run.return_value = SimpleNamespace(returncode=1)
        with self.assertRaisesRegex(RuntimeError, 'unavailable'): self.validate('facts', self.owner)


class ProgressTests(unittest.TestCase):
    def test_bad_history_keeps_independent_committed_migration_progress(self):
        raw = {'observed_at': STAMP, 'run': 'current', 'history_error': 'OwnerMismatch',
               'migrations': {'facts': migration(), 'patent': migration(stage='paused', pg_workers=[])}}
        value = progress.summarize(raw)
        self.assertEqual(value['migrations']['facts']['processed'], 600)
        text = progress.markdown(value)
        self.assertIn('filing/RDS | 未验证 | —', text)
        self.assertIn('600/未核定', text)
        self.assertIn('暂停', text)
        self.assertNotIn('ContainerExit', text)

    def test_bad_history_counters_do_not_erase_other_scopes(self):
        raw = {'observed_at': STAMP, 'run': 'current', 'units': [{'unit': 1}],
               'checkpoints': [{'unit': 1, 'processed': 10, 'renamed': 2, 'deduplicated': 3}],
               'migrations': {'facts': migration(), 'patent': migration()}}
        value = progress.summarize(raw)
        self.assertIn('Checkpoint committed counters differ', value['history_error'])
        self.assertEqual(value['migrations']['facts']['processed'], 600)

    def test_stopped_paused_job_is_not_a_failure(self):
        raw = migration(stage='paused', pg_workers=[], container_running=False, exit_code=0)
        value = progress.summarize_migration(raw, None, STAMP)
        self.assertEqual(value['stage'], 'paused')
        self.assertEqual(value['errors'], [])

    def test_abnormal_exit_preserves_commits_but_reports_failure(self):
        raw = migration(container_running=False, exit_code=1)
        value = progress.summarize_migration(raw, None, STAMP)
        self.assertEqual(value['stage'], 'failed')
        self.assertEqual(value['processed'], 600)
        self.assertIn('ContainerExit1', value['errors'])

    def test_unverified_scope_does_not_reuse_old_counters(self):
        value = progress.summarize_migration({'verified': False, 'error_class': 'BadOwner'}, migration(), STAMP)
        self.assertFalse(value['verified'])
        self.assertNotIn('processed', value)
        self.assertIsNone(value['delta'])

    def test_delta_uses_real_interval_and_worker_commits(self):
        before = migration()
        now = migration(sampled_at='2026-11-26T07:02:00+00:00', worker_processed=[110] * 6, processed=660)
        value = progress.summarize_migration(now, before, now['sampled_at'])
        self.assertEqual((value['delta'], value['rows_per_minute']), (60, 30))

    def test_history_reader_binds_plan_and_current_owner_before_loading_rows(self):
        funcs = [n for n in ast.parse(progress.PROBE).body if isinstance(n, ast.FunctionDef)]
        env = {'json': json, 'hashlib': hashlib, 'ROOT': Path('/unused'),
               'OWNERS': {'history': {'plan_sha256': 'wrong', 'root': '/wrong'}}}
        exec(compile(ast.Module(body=funcs, type_ignores=[]), '<history-check>', 'exec'), env)
        class FakeRoot:
            def __truediv__(self, key): return self
            def read_text(self): return json.dumps({'plan_sha256': 'actual'})
        env['ROOT'] = FakeRoot()
        with self.assertRaises(ValueError): env['read_history']()


if __name__ == '__main__':
    unittest.main()
