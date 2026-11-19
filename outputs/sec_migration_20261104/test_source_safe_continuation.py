import copy
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


class ContinuationTests(unittest.TestCase):
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
