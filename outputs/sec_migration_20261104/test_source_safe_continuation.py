import copy
from pathlib import Path
import tempfile
import unittest
from outputs.sec_migration_20261104 import source_safe_migration as m


def signed(plan):
    plan = copy.deepcopy(plan)
    plan['plan_sha256'] = m.pg.sha(m.pg.encoded({k: v for k, v in plan.items() if k != 'plan_sha256'}))
    return plan


def fixture():
    parts = [{'worker': i, 'lower': f'{i * 100 + 1:024x}', 'upper': f'{(i + 1) * 100 + 1:024x}'} for i in range(6)]
    parts[-1].update(upper=None, upper_inclusive=f'{600:024x}')
    parent = signed(dict(protocol=m.PROTOCOL, kind='facts_full', collection='financial_fact_sec_edgar',
                        data_type='financial_fact', aliases=m.SCOPES['facts_full'][1], workers=6,
                        page_size=3000, sql_batch_size=300, mongo_uuid='a' * 32, parts=parts,
                        manifest_path=None, manifest_sha256=None))
    states = [dict(plan_sha256=parent['plan_sha256'], worker=i, processed=20,
                   cursor=f'{i * 100 + 20:024x}', done=i in (2, 4), pending=None, last_error=None) for i in range(6)]
    ranges = [(21, 61), (61, 101), (121, 161), (161, 201), (321, 401), (521, 601)]
    child = copy.deepcopy(parent)
    child['parts'] = [dict(worker=i, lower=f'{lo:024x}', upper=f'{hi:024x}') for i, (lo, hi) in enumerate(ranges)]
    child['continuation'] = dict(parent_plan=parent, checkpoints=states, processed_offset=120,
                                 parent_root='/app/output/sec_migration_20261104/facts-migration-20261116')
    return signed(child)


def second_generation():
    parent = fixture()
    states = [dict(plan_sha256=parent['plan_sha256'], worker=i, processed=1,
                   cursor=p['lower'], done=i in (3, 4), pending=None, last_error=None)
              for i, p in enumerate(parent['parts'])]
    ranges = [(22, 42), (42, 61), (62, 81), (81, 101), (122, 161), (522, 601)]
    child = copy.deepcopy(parent)
    child['parts'] = [dict(worker=i, lower=f'{lo:024x}', upper=f'{hi:024x}') for i, (lo, hi) in enumerate(ranges)]
    child['continuation'] = dict(parent_plan=parent, checkpoints=states, processed_offset=126,
                                 parent_root='/app/output/sec_migration_20261104/facts-migration-20261116-rebalance-20261119')
    return signed(child)


class ContinuationTests(unittest.TestCase):
    def test_second_generation_preserves_inherited_total_and_exact_remaining_ranges(self):
        plan = second_generation()
        m.validate_plan(plan)
        for offset in (6, 125, 127):
            changed = copy.deepcopy(plan)
            changed['continuation']['processed_offset'] = offset
            with self.subTest(offset=offset), self.assertRaises(RuntimeError):
                m.validate_plan(signed(changed))
        changed = copy.deepcopy(plan)
        changed['parts'][0]['lower'] = f'{21:024x}'
        with self.assertRaises(RuntimeError):
            m.validate_plan(signed(changed))

    def test_all_ancestor_checkpoints_and_stop_files_must_remain_frozen(self):
        plan = second_generation()
        with tempfile.TemporaryDirectory() as directory:
            parent_root, original_root = Path(directory) / 'parent', Path(directory) / 'original'
            plan['continuation']['parent_root'] = str(parent_root)
            parent = plan['continuation']['parent_plan']
            parent['continuation']['parent_root'] = str(original_root)
            for root, lineage in ((parent_root, plan['continuation']), (original_root, parent['continuation'])):
                root.mkdir()
                m.pg.atomic(root / 'plan.json', lineage['parent_plan'])
                (root / 'STOP').write_text('frozen')
                for i, state in enumerate(lineage['checkpoints']):
                    m.pg.atomic(root / f'worker-{i}.json', state)
            m.validate_parent_files(plan)
            (original_root / 'STOP').unlink()
            with self.assertRaises(RuntimeError):
                m.validate_parent_files(plan)
            (original_root / 'STOP').write_text('frozen')
            m.pg.atomic(original_root / 'worker-0.json', {})
            with self.assertRaises(RuntimeError):
                m.validate_parent_files(plan)

    def test_six_disjoint_tails_exclude_completed_ranges_and_preserve_total(self):
        m.validate_plan(fixture())

    def test_gap_overlap_or_replayed_committed_prefix_is_rejected(self):
        for index, field, delta in [(0, 'lower', -1), (0, 'upper', -1), (1, 'lower', -1)]:
            p = fixture()
            p['parts'][index][field] = f'{int(p["parts"][index][field], 16) + delta:024x}'
            with self.subTest(index=index, field=field), self.assertRaises(RuntimeError):
                m.validate_plan(signed(p))

    def test_wrong_total_or_unresolved_parent_is_rejected(self):
        for field, value in [('processed_offset', 121), ('pending', 'unresolved')]:
            p = fixture()
            if field == 'pending':
                p['continuation']['checkpoints'][0][field] = value
            else:
                p['continuation'][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                m.validate_plan(signed(p))
