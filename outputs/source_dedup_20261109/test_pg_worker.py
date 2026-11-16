"""Transaction, replay, coverage and preservation regressions for source renaming."""
import copy
import gzip
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from outputs.source_dedup_20261109 import pg_worker as subject

OLD, NEW, KIND = 'sec_edgar_filing', 'filing_sec_edgar', 'filing'
UNIT = {'unit': 0, 'old': OLD, 'new': NEW, 'data_type': KIND, 'schema': 'ts_rds',
        'table': 'rds_filing', 'layer': 'rds', 'leaf': 'rds_filing_p0', 'oid': 11, 'ordinal': 0}


def raw(row):
    return json.dumps(row, sort_keys=True, separators=(', ', ': '))


def record(rid, source, value=1):
    return {'record_id': rid, 'data_source': source, 'data_type': KIND,
            'raw_data': {'data_source': source, 'value': value, 'assets': {'url': 'filing/original/key.html'},
                         '_meta': {'template': source, 'data_source': source, 'write_token': 'opaque-token',
                                   'download_version': 7, 'updated_at': '2026-11-01T00:00:00+00:00'}},
            'kafka_topic': 'source', 'kafka_partition': 2, 'kafka_offset': 99,
            'created_at': '2026-10-01T00:00:00+00:00', 'updated_at': '2026-11-01T00:00:00+00:00'}


def route(row, layer='rds'):
    result = copy.deepcopy(row); result['data_source'] = NEW
    if layer == 'rds':
        result['raw_data']['data_source'] = NEW
        meta = result['raw_data'].get('_meta') or {}
        for name in ('template', 'data_source', 'source'):
            if meta.get(name) == OLD:
                meta[name] = NEW
    return result


class Transaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        c = self.connection
        self.before = copy.deepcopy(c.rows)
        c.txid += 1; self.txid = c.txid
        return self

    async def __aexit__(self, kind, value, trace):
        c = self.connection
        if kind:
            c.rows = self.before; c.outcomes[self.txid] = 'aborted'
        else:
            c.outcomes[self.txid] = 'committed'
            if c.lose_commit:
                c.lose_commit = False
                raise OSError('Commit acknowledged by server, transport reply lost')


class Connection:
    def __init__(self, rows=()):
        self.rows = {(r['record_id'], r['data_source'], r['data_type']): raw(r) for r in rows}
        self.txid, self.outcomes = 100, {}
        self.lose_commit = False
        self.fail_delete = False
        self.fail_rename_on = None
        self.rename_attempts = 0
        self.calls, self.guard = [], None
        self.close = AsyncMock()

    def is_closed(self):
        return False

    def transaction(self):
        return Transaction(self)

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        if 'INSERT INTO public.source_dedup_20261109_tx' in sql:
            self.guard = {'processed_rows': args[6], 'batch_sha256': args[7]}
        return 'SELECT 1'

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if 'SELECT processed_rows,batch_sha256' in sql:
            return self.guard
        raise AssertionError(sql)

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if 'k CROSS JOIN LATERAL' in sql:
            result = []
            for k in json.loads(args[0]):
                key = k['record_id'], k['data_source'], k['data_type']
                if key in self.rows:
                    result.append({**k, 'raw': self.rows[key]})
            return result
        if 'AS record_id,' in sql and 'jsonb_array_elements' in sql:
            layer = 'rds' if "'{raw_data}'" in sql else 'ods'
            return [{'record_id': r['record_id'], 'raw': raw(route(r, layer))} for r in json.loads(args[0])]
        if 'UPDATE ' in sql and 'SET data_source=' in sql:
            self.rename_attempts += 1
            if self.rename_attempts == self.fail_rename_on:
                raise TimeoutError('Injected SQL sub-batch timeout')
            changed = []
            for before in json.loads(args[0]):
                old = before['record_id'], OLD, KIND
                new = before['record_id'], NEW, KIND
                if self.rows.get(old) == raw(before) and new not in self.rows:
                    layer = 'rds' if ',raw_data=' in sql else 'ods'
                    self.rows[new] = raw(route(before, layer)); del self.rows[old]
                    changed.append({'record_id': new[0], 'data_source': new[1], 'data_type': new[2], 'raw': self.rows[new]})
            return changed
        if 'UPDATE ' in sql and 'SET raw_data=' in sql:
            changed = []
            for before in json.loads(args[0]):
                key = before['record_id'], NEW, KIND
                after = raw(route(before))
                if self.rows.get(key) == raw(before) and after != self.rows[key]:
                    self.rows[key] = after
                    changed.append({'record_id': key[0], 'data_source': key[1], 'data_type': key[2], 'raw': after})
            return changed
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        if sql == 'SELECT txid_current()':
            return self.txid
        if 'pg_try_advisory_lock' in sql:
            return True
        if 'pg_wal_lsn_diff' in sql:
            return 123456
        if 'pg_xact_status' in sql:
            return self.outcomes[int(args[0])]
        if 'DELETE FROM ONLY' in sql:
            if self.fail_delete:
                self.fail_delete = False
                raise RuntimeError('Injected DELETE failure')
            changed = 0
            for before in json.loads(args[0]):
                key = before['record_id'], OLD, KIND
                if self.rows.get(key) == raw(before):
                    del self.rows[key]; changed += 1
            return changed
        raise AssertionError(sql)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state_path = self.root / 'worker.json'
        self.state = {'plan_sha256': 'a' * 64, 'unit': 0, 'worker': 0, 'cursor': None,
                      'processed': 0, 'renamed': 0, 'deduplicated': 0, 'pending': None}
        self.plan = {'plan_sha256': 'a' * 64, 'batch_size': 300, 'guard_table': subject.GUARD_TABLE}

    async def apply(self, connection, ids):
        return await subject.apply_batch(connection, self.root, self.plan, UNIT,
            [{'record_id': rid, 'data_type': KIND} for rid in ids], self.state, self.state_path)

    async def test_old_only_is_update_not_delete_insert_preserves_business_and_positions(self):
        before = record('a', OLD, 123456789123456789123456789)
        c = Connection([before]); receipt = await self.apply(c, ['a'])
        self.assertNotIn(('a', OLD, KIND), c.rows)
        self.assertEqual(json.loads(c.rows[('a', NEW, KIND)]), route(before))
        self.assertEqual(receipt['records'][0]['action'], 'rename')
        self.assertFalse(any('DELETE FROM' in sql or 'INSERT INTO "ts_rds"' in sql for sql, _ in c.calls))
        self.assertTrue(any('UPDATE "ts_rds"."rds_filing"' in sql for sql, _ in c.calls))
        self.assertEqual(c.guard['processed_rows'], 1)
        self.assertIsNone(receipt['archive'])
        state = subject.checkpoint_after(self.state, receipt)
        self.assertEqual((state['processed'], state['renamed'], state['deduplicated']), (1, 1, 0))

    async def test_duplicate_keeps_canonical_even_when_old_has_different_newer_business(self):
        old, new = record('a', OLD, 999), record('a', NEW, 17)
        old['raw_data']['_meta']['updated_at'] = '2030-01-01T00:00:00+00:00'
        c = Connection([old, new]); receipt = await self.apply(c, ['a'])
        self.assertEqual(len(c.rows), 1)
        self.assertEqual(json.loads(c.rows[('a', NEW, KIND)]), new)
        self.assertEqual(receipt['records'][0]['action'], 'dedup')
        archive = self.root / receipt['archive']['path']
        self.assertEqual(subject.sha(archive.read_bytes()), receipt['archive']['sha256'])
        self.assertEqual(json.loads(gzip.decompress(archive.read_bytes())), old)

    async def test_mixed_returning_and_locked_noop_snapshots_need_one_row_read(self):
        new_b = record('b', NEW, 123456789123456789123456789)
        new_c = record('c', NEW, 31)
        new_c['raw_data']['data_source'] = OLD
        new_c['raw_data']['_meta']['template'] = OLD
        c = Connection([record('a', OLD), record('b', OLD, 999), new_b, record('c', OLD, 998), new_c])
        fetch = c.fetch; routing_returns = []
        async def capture(sql, *args):
            values = await fetch(sql, *args)
            if 'SET raw_data=' in sql:
                routing_returns.extend(row['record_id'] for row in values)
            return values
        c.fetch = capture
        receipt = await self.apply(c, ['a', 'b', 'c'])
        self.assertEqual((receipt['rows'], routing_returns), (3, ['c']))
        self.assertEqual(sum('k CROSS JOIN LATERAL' in sql for sql, _ in c.calls), 1)
        self.assertEqual(json.loads(c.rows[('b', NEW, KIND)]), new_b)
        self.assertEqual(json.loads(c.rows[('c', NEW, KIND)]), route(new_c))
        self.assertEqual(set(c.rows), {(rid, NEW, KIND) for rid in ('a', 'b', 'c')})

    async def test_invalid_rename_returning_identity_or_afterimage_rolls_back(self):
        for fault in ('missing', 'duplicate', 'wrong_source', 'wrong_type', 'wrong_raw'):
            with self.subTest(fault=fault):
                c = Connection([record('a', OLD), record('b', OLD)])
                initial = copy.deepcopy(c.rows); fetch = c.fetch
                async def corrupt(sql, *args):
                    values = await fetch(sql, *args)
                    if 'SET data_source=' in sql:
                        if fault == 'missing':
                            values.pop()
                        elif fault == 'duplicate':
                            values[1] = dict(values[0])
                        elif fault == 'wrong_source':
                            values[0]['data_source'] = OLD
                        elif fault == 'wrong_type':
                            values[0]['data_type'] = 'financial_fact'
                        else:
                            values[0]['raw'] = raw(record('a', NEW, 404))
                    return values
                c.fetch = corrupt
                with self.assertRaisesRegex(RuntimeError, 'CAS failed|afterimages differ'):
                    await self.apply(c, ['a', 'b'])
                self.assertEqual(c.rows, initial)
                self.assertEqual(c.outcomes[c.txid], 'aborted')
                self.assertFalse(list((self.root / 'transactions').glob('*.committed.json')))

    async def test_invalid_canonical_returning_or_missing_required_change_rolls_back(self):
        for fault in ('missing', 'duplicate', 'unexpected', 'wrong_raw'):
            with self.subTest(fault=fault):
                canonical = record('a', NEW, 17)
                canonical['raw_data']['data_source'] = OLD
                c = Connection([record('a', OLD, 999), canonical])
                initial = copy.deepcopy(c.rows); fetch = c.fetch
                async def corrupt(sql, *args):
                    values = await fetch(sql, *args)
                    if 'SET raw_data=' in sql:
                        if fault == 'missing':
                            values = []
                        elif fault == 'duplicate':
                            values += [dict(values[0])]
                        elif fault == 'unexpected':
                            values[0]['record_id'] = 'unlocked'
                        else:
                            values[0]['raw'] = raw(record('a', NEW, 404))
                    return values
                c.fetch = corrupt
                with self.assertRaisesRegex(RuntimeError, 'unexpected identities|afterimages differ'):
                    await self.apply(c, ['a'])
                self.assertEqual(c.rows, initial)
                self.assertEqual(c.outcomes[c.txid], 'aborted')

    async def test_ods_returning_preserves_body_and_needs_one_row_read(self):
        before, canonical = record('a', OLD), record('b', NEW, 19)
        c = Connection([before, record('b', OLD, 999), canonical])
        unit = {**UNIT, 'schema': 'ts_ods', 'table': 'ods_filing', 'leaf': 'ods_filing_p0', 'layer': 'ods'}
        receipt = await subject.apply_batch(c, self.root, self.plan, unit,
            [{'record_id': rid, 'data_type': KIND} for rid in ('a', 'b')], self.state, self.state_path)
        self.assertEqual(receipt['rows'], 2)
        self.assertEqual(json.loads(c.rows[('a', NEW, KIND)]), route(before, 'ods'))
        self.assertEqual(json.loads(c.rows[('b', NEW, KIND)]), canonical)
        self.assertEqual(sum('k CROSS JOIN LATERAL' in sql for sql, _ in c.calls), 1)
        self.assertFalse(any('SET raw_data=' in sql for sql, _ in c.calls))

    async def test_sql_payload_and_returning_keep_decimal_text_without_python_roundtrip(self):
        before = raw(record('a', OLD)).replace('"value": 1', '"value": 123456789123456789.123456789123456789')
        after = before.replace('"' + OLD + '"', '"' + NEW + '"')
        c = Connection(); c.rows[('a', OLD, KIND)] = before
        fetch = c.fetch
        async def precise(sql, *args):
            if 'AS record_id,' in sql and 'jsonb_array_elements' in sql:
                self.assertEqual(args[0], '[' + before + ']')
                c.calls.append((sql, args))
                return [{'record_id': 'a', 'raw': after}]
            if 'SET data_source=' in sql:
                self.assertEqual(args[0], '[' + before + ']')
                c.calls.append((sql, args))
                del c.rows[('a', OLD, KIND)]; c.rows[('a', NEW, KIND)] = after
                return [{'record_id': 'a', 'data_source': NEW, 'data_type': KIND, 'raw': after}]
            return await fetch(sql, *args)
        c.fetch = precise
        receipt = await self.apply(c, ['a'])
        self.assertEqual(receipt['records'][0]['expected_new_sha256'], subject.sha(after))
        self.assertIn('123456789123456789.123456789123456789', c.rows[('a', NEW, KIND)])

    async def test_mixed_batch_rollback_and_replay_does_not_lose_old_rows(self):
        old_a, old_b, new_b = record('a', OLD), record('b', OLD, 2), record('b', NEW, 3)
        c = Connection([old_a, old_b, new_b]); initial = copy.deepcopy(c.rows)
        c.fail_delete = True
        with self.assertRaisesRegex(RuntimeError, 'Injected DELETE'):
            await self.apply(c, ['a', 'b'])
        self.assertEqual(c.rows, initial)
        pending = subject.read(self.state_path)
        recovered = await subject.recover_pending(c, self.root, self.plan, UNIT, pending, self.state_path)
        self.assertIsNone(recovered['pending']); self.assertEqual(recovered['processed'], 0)
        receipt = await self.apply(c, ['a', 'b'])
        after = subject.checkpoint_after(recovered, receipt)
        self.assertEqual((after['processed'], after['renamed'], after['deduplicated']), (2, 1, 1))
        self.assertEqual(set(c.rows), {('a', NEW, KIND), ('b', NEW, KIND)})

    async def test_lost_commit_reply_recovers_without_rewriting_or_double_counting(self):
        c = Connection([record('a', OLD), record('a', NEW, 9)])
        c.lose_commit = True
        with self.assertRaises(OSError):
            await self.apply(c, ['a'])
        self.assertEqual(sum('k CROSS JOIN LATERAL' in sql for sql, _ in c.calls), 1)
        pending = subject.read(self.state_path)
        recovered = await subject.recover_pending(c, self.root, self.plan, UNIT, pending, self.state_path)
        self.assertEqual(sum('k CROSS JOIN LATERAL' in sql for sql, _ in c.calls), 2)
        self.assertEqual(recovered['processed'], 1)
        self.assertEqual(recovered['deduplicated'], 1)
        again = await subject.recover_pending(c, self.root, self.plan, UNIT, recovered, self.state_path)
        self.assertEqual(again['processed'], 1)
        self.assertEqual(json.loads(c.rows[('a', NEW, KIND)])['raw_data']['value'], 9)

    async def test_lost_commit_recovery_rereads_and_rejects_different_afterimage(self):
        c = Connection([record('a', OLD)]); c.lose_commit = True
        with self.assertRaises(OSError):
            await self.apply(c, ['a'])
        pending = subject.read(self.state_path)
        c.rows[('a', NEW, KIND)] = raw(record('a', NEW, 404))
        with self.assertRaisesRegex(RuntimeError, 'Lost-ack committed transaction afterimages differ'):
            await subject.recover_pending(c, self.root, self.plan, UNIT, pending, self.state_path)
        self.assertEqual(sum('k CROSS JOIN LATERAL' in sql for sql, _ in c.calls), 2)
        self.assertEqual(subject.read(self.state_path)['processed'], 0)

    async def test_existing_commit_receipt_survives_checkpoint_write_failure(self):
        c = Connection([record('a', OLD)])
        await self.apply(c, ['a'])
        pending = subject.read(self.state_path)
        recovered = await subject.recover_pending(c, self.root, self.plan, UNIT, pending, self.state_path)
        self.assertEqual(recovered['processed'], 1)
        self.assertEqual(len(list((self.root / 'transactions').glob('*.committed.json'))), 1)

    async def test_six_worker_unit_coverage_is_disjoint_and_honors_output_data_type(self):
        class Catalog:
            async def fetchval(self, sql, *args):
                return 'HASH (record_id, data_source, data_type)' if 'pg_get_partkeydef' in sql else False

            async def fetch(self, sql, *args):
                return [{'schema': 'ts_ods', 'name': 'ods_social_account_p' + str(i), 'oid': 100+i,
                         'bound': f'FOR VALUES WITH (modulus 32, remainder {i})'} for i in range(32)]

        units = await subject.discover(Catalog(), [{'old': 'twitter', 'new': 'account_twitter',
            'data_type': 'account', 'relations': [{'layer': 'ods', 'schema': 'ts_ods',
                'name': 'ods_social_account', 'row_data_type': 'social_account'}]}])
        groups = [{u['oid'] for u in units if u['worker'] == worker} for worker in range(6)]
        self.assertEqual(set.union(*groups), set(range(100, 132)))
        self.assertEqual(sum(map(len, groups)), 32)
        self.assertTrue(all(u['data_type'] == 'social_account' for u in units))
        self.assertTrue(all(groups))

    async def test_sec_facts_aliases_remain_financial_and_rds_units_precede_ods(self):
        class Catalog:
            async def fetchval(self, sql, *args):
                return 'HASH (record_id, data_source, data_type)' if 'pg_get_partkeydef' in sql else False

            async def fetch(self, sql, *args):
                schema, name = args[0].replace('"', '').split('.')
                return [{'schema': schema, 'name': name + '_p' + str(i), 'oid': 100+i,
                         'bound': f'FOR VALUES WITH (modulus 2, remainder {i})'} for i in range(2)]

        scopes = [{'old': old, 'new': 'financial_fact_sec_edgar', 'data_type': 'financial_fact',
                   'relations': [{'layer': layer, 'schema': 'ts_' + layer, 'name': layer + '_financial_fact',
                                  'row_data_type': 'financial_fact'} for layer in ('ods', 'rds')]}
                  for old in ('sec_edgar_company', 'sec_edgar_financial_fact')]
        units = await subject.discover(Catalog(), scopes)
        self.assertEqual([u['layer'] for u in units], ['rds'] * 4 + ['ods'] * 4)
        self.assertEqual({u['data_type'] for u in units}, {'financial_fact'})
        self.assertEqual({u['old'] for u in units}, {'sec_edgar_company', 'sec_edgar_financial_fact'})
        bad = {**scopes[0], 'data_type': 'organization'}
        with self.assertRaises(ValueError):
            await subject.discover(Catalog(), [bad])

    async def test_initial_key_fetch_timeout_retries_without_advancing_checkpoint(self):
        class KeyConnection:
            def __init__(self):
                self.reads = 0
                self.close = AsyncMock()

            async def fetchval(self, sql, *args):
                return True

            async def fetch(self, sql, *args, **kwargs):
                self.reads += 1
                if self.reads == 1:
                    raise TimeoutError('Cold initial key read')
                return []

            def is_closed(self):
                return False

        source = b'{"scopes":[]}'
        (self.root / 'scope.json').write_bytes(source)
        plan = {'plan_sha256': subject.sha(source), 'workers': 6, 'batch_size': 300,
                'units': [{**UNIT, 'worker': 0}]}
        subject.atomic(self.root / 'plan.json', plan)
        connection = KeyConnection()
        with patch.object(subject, 'connect', new=AsyncMock(return_value=connection)) as connect, \
             patch.object(subject.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
            await subject.worker_run(self.root, 0)
        self.assertEqual(connection.reads, 2)
        self.assertIn('-worker-0', connect.call_args.args[0])
        sleep.assert_awaited_once()
        checkpoint = subject.read(self.root / 'checkpoints/unit-0.json')
        self.assertTrue(checkpoint['done'])
        self.assertEqual(checkpoint['processed'], 0)
        self.assertIsNone(checkpoint['cursor'])
        connection.close.assert_awaited_once()

    async def test_duplicate_worker_owner_refuses_second_session(self):
        connection = type('Connection', (), {'fetchval': AsyncMock(return_value=False), 'close': AsyncMock()})()
        with patch.object(subject, 'connect', new=AsyncMock(return_value=connection)):
            with self.assertRaisesRegex(RuntimeError, 'already owns worker 2'):
                await subject.owned_connection(self.root, 2)
        connection.close.assert_awaited_once()

    def legacy_plan(self, units):
        source = b'{"scopes":[],"original_run":true}'
        (self.root / 'scope.json').write_bytes(source)
        plan = {'plan_sha256': subject.sha(source), 'workers': 6, 'batch_size': 300,
                'units': units, 'guard_table': None}
        subject.atomic(self.root / 'plan.json', plan)
        subject.atomic(self.root / 'layout.json', {'workers': 6, 'page_size': 3000,
            'sql_batch_size': 300, 'plan_sha256': plan['plan_sha256']})
        return plan

    async def test_history_units_resume_while_facts_checkpoints_remain_untouched(self):
        tables = [('rds', 'filing', 32), ('rds', 'financial_fact', 64),
                  ('ods', 'filing', 32), ('ods', 'filing_document', 32),
                  ('ods', 'financial_fact', 64)]
        units = []
        for layer, kind, count in tables:
            for ordinal in range(count):
                number = len(units)
                units.append({**UNIT, 'unit': number, 'worker': number % 6, 'ordinal': ordinal % 32,
                    'schema': 'ts_' + layer, 'table': layer + '_' + kind, 'layer': layer,
                    'data_type': kind, 'leaf': layer + '_' + kind + '_p' + str(ordinal % 32),
                    'old': ('sec_edgar_company' if ordinal < 32 else 'sec_edgar_financial_fact')
                           if kind == 'financial_fact' else OLD,
                    'new': 'financial_fact_sec_edgar' if kind == 'financial_fact' else NEW})
        plan = self.legacy_plan(units)
        owners = [subject.unit_owner(unit, {'workers': 6}) for unit in units]
        self.assertEqual([owners.count(owner) for owner in range(6)], [16] * 6)
        self.assertEqual(owners.count(None), 128)
        active_partial = {1, 14, 96, 97, 128, 129}
        partial = active_partial | {32, 160}
        self.assertEqual({owners[number] for number in active_partial}, set(range(6)))
        originals = {}; original_checkpoints = {}
        for unit in units:
            state = {**self.state, 'plan_sha256': plan['plan_sha256'], 'unit': unit['unit'],
                'worker': unit['unit'] % 8, 'cursor': f"{unit['unit']:06d}", 'processed': 100 + unit['unit'],
                'renamed': 80 + unit['unit'], 'deduplicated': 20, 'done': unit['unit'] not in partial}
            if unit['unit'] == 32:
                state.update(pending='transactions/preserved-facts-pending.json', last_error='HistoricalError')
            path = self.root / 'checkpoints' / f"unit-{unit['unit']}.json"
            subject.atomic(path, state); originals[unit['unit']] = copy.deepcopy(state)
            original_checkpoints[unit['unit']] = path.read_bytes()
        c = Connection([record('000999', OLD)])
        pending_path = self.root / 'checkpoints/unit-14.json'
        c.lose_commit = True
        with self.assertRaises(OSError):
            await subject.apply_batch(c, self.root, plan, units[14],
                [{'record_id': '000999', 'data_type': KIND}], originals[14], pending_path)
        original_files = {name: (self.root / name).read_bytes() for name in ('scope.json', 'plan.json')}
        pending_files = {p: p.read_bytes() for p in (self.root / 'transactions').glob('*.prepared.json')}
        fetch = c.fetch; key_queries = []

        async def keys_or_rows(sql, *args, **kwargs):
            if 'SELECT record_id,data_type FROM ONLY' in sql:
                key_queries.append((sql, args))
                return []
            return await fetch(sql, *args)

        c.fetch = keys_or_rows
        output = io.StringIO()
        with patch.object(subject, 'connect', new=AsyncMock(return_value=c)), redirect_stdout(output):
            for owner in range(6):
                await subject.worker_run(self.root, owner)
        for unit in units:
            path = self.root / 'checkpoints' / f"unit-{unit['unit']}.json"
            state = subject.read(path)
            if owners[unit['unit']] is None:
                self.assertEqual(path.read_bytes(), original_checkpoints[unit['unit']])
                self.assertEqual(sum(subject.unit_owner(unit, {'workers': 6}) == n for n in range(6)), 0)
                continue
            self.assertEqual(state['worker'], owners[unit['unit']])
            self.assertTrue(state['done'])
            delta = int(unit['unit'] == 14)
            self.assertEqual(state['processed'], originals[unit['unit']]['processed'] + delta)
            self.assertEqual(state['renamed'], originals[unit['unit']]['renamed'] + delta)
            self.assertEqual(state['deduplicated'], originals[unit['unit']]['deduplicated'])
            self.assertEqual(state['cursor'], '000999' if delta else originals[unit['unit']]['cursor'])
            self.assertEqual(sum(subject.unit_owner(unit, {'workers': 6}) == n for n in range(6)), 1)
        for name, before in original_files.items():
            self.assertEqual((self.root / name).read_bytes(), before)
        for path, before in pending_files.items():
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(key_queries), len(active_partial))
        self.assertFalse(any('financial_fact' in sql for sql, _ in key_queries))
        for number in active_partial:
            unit = units[number]
            cursor = '000999' if number == 14 else originals[number]['cursor']
            self.assertEqual(sum('FROM ONLY ' + subject.qualified(unit['schema'], unit['leaf']) in sql
                and args == (unit['old'], unit['data_type'], cursor, 3000) for sql, args in key_queries), 1)
        starts = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual({row['worker'] for row in starts}, set(range(6)))
        self.assertTrue(all(row['page_size'] == 3000 and row['sql_batch_size'] == 300 for row in starts))

    async def test_3000_key_page_uses_300_row_transactions_and_recovers_failed_second_batch(self):
        plan = self.legacy_plan([{**UNIT, 'worker': 0}])
        c = Connection([record(f'{n:06d}', OLD) for n in range(3000)])
        c.fail_rename_on = 2
        fetch = c.fetch; page_limits = []; page_lengths = []

        async def keys_or_rows(sql, *args, **kwargs):
            if 'SELECT record_id,data_type FROM ONLY' in sql:
                if len(args) == 3:
                    old, kind, limit = args
                    cursor = None
                else:
                    old, kind, cursor, limit = args
                rows = [{'record_id': rid, 'data_type': dtype} for rid, source, dtype in sorted(c.rows)
                        if source == old and dtype == kind and (cursor is None or rid > cursor)][:limit]
                page_limits.append(limit); page_lengths.append(len(rows))
                return rows
            return await fetch(sql, *args)

        c.fetch = keys_or_rows
        output = io.StringIO()
        with patch.object(subject, 'connect', new=AsyncMock(return_value=c)), \
             patch.object(subject.asyncio, 'sleep', new_callable=AsyncMock), redirect_stdout(output):
            await subject.worker_run(self.root, 0)
        state = subject.read(self.root / 'checkpoints/unit-0.json')
        self.assertEqual((state['processed'], state['renamed'], state['deduplicated']), (3000, 3000, 0))
        self.assertEqual(state['cursor'], '002999')
        self.assertTrue(state['done'])
        self.assertEqual(len(c.rows), 3000)
        self.assertTrue(all(source == NEW for _, source, _ in c.rows))
        self.assertEqual(page_lengths, [3000, 2700, 0])
        self.assertEqual(set(page_limits), {3000})
        locks = [len(json.loads(args[0])) for sql, args in c.calls if 'k CROSS JOIN LATERAL' in sql]
        self.assertTrue(locks)
        self.assertLessEqual(max(locks), 600)
        receipts = [subject.read(p) for p in (self.root / 'transactions').glob('*.committed.json')]
        self.assertEqual(sum(r['rows'] for r in receipts), 3000)
        self.assertEqual(len(receipts), 10)
        self.assertTrue(all(r['rows'] <= 300 for r in receipts))
        logs = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(all(row['page_size'] == 3000 and row['sql_batch_size'] == 300 for row in logs))
        committed = [row for row in logs if row['stage'] == 'batch_committed']
        self.assertEqual(sum(row['rows'] for row in committed), 3000)
        self.assertEqual(committed[-1]['processed'], 3000)


class BoundsTests(unittest.TestCase):
    def test_six_slots_cover_history_without_scanning_facts_and_reject_other_tables(self):
        layout = {'workers': 6}
        for schema, table, base in [('ts_rds', 'rds_filing', 0), ('ts_ods', 'ods_filing', 2),
                ('ts_ods', 'ods_filing_document', 4)]:
            for ordinal in range(32):
                self.assertEqual(subject.unit_owner({'unit': ordinal + 1, 'ordinal': ordinal,
                    'schema': schema, 'table': table}, layout), base + ordinal % 2)
        for schema, table in [('ts_rds', 'rds_financial_fact'), ('ts_ods', 'ods_financial_fact')]:
            self.assertIsNone(subject.unit_owner({'unit': 0, 'schema': schema, 'table': table}, layout))
        with self.assertRaisesRegex(ValueError, 'Unexpected SEC history repair table'):
            subject.unit_owner({'unit': 0, 'schema': 'ts_rds', 'table': 'rds_organization'}, layout)

    def test_patent_separate_plan_uses_all_six_disjoint_worker_slots(self):
        units = [{'unit': number, 'schema': 'ts_' + layer, 'table': layer + '_patent'}
                 for number, layer in enumerate(['rds'] * 32 + ['ods'] * 32)]
        groups = [{unit['unit'] for unit in units if subject.unit_owner(unit, {'workers': 6}) == worker}
                  for worker in range(6)]
        self.assertTrue(all(groups))
        self.assertEqual(sum(map(len, groups)), len(units))
        self.assertEqual(set.union(*groups), set(range(64)))

    def controller_processes(self, start_error=None, failed=None):
        processes = []
        for number in range(6):
            process = Mock(pid=None, exitcode=None)
            def start(process=process, number=number):
                if number == start_error:
                    raise OSError('Injected process start failure')
                process.pid = number + 100
                if number == failed:
                    process.exitcode = 1
            def terminate(process=process):
                process.exitcode = -15
            process.start.side_effect = start
            process.is_alive.side_effect = lambda process=process: process.pid is not None and process.exitcode is None
            process.terminate.side_effect = terminate
            processes.append(process)
        return processes

    def test_last_worker_failure_stops_and_joins_other_workers_without_waiting_for_first(self):
        with tempfile.TemporaryDirectory() as directory:
            processes = self.controller_processes(failed=5)
            context = Mock(); context.Process.side_effect = processes
            with patch.object(subject, 'prepare', new=AsyncMock(return_value={'plan_sha256': 'a' * 64})), \
                 patch.object(subject.multiprocessing, 'get_context', return_value=context):
                with self.assertRaisesRegex(RuntimeError, 'workers failed; preserve checkpoints'):
                    subject.main(type('Args', (), {'root': directory, 'prepare': False, 'worker': None})())
            self.assertEqual(context.Process.call_count, 6)
            for process in processes:
                self.assertEqual(process.join.call_args_list[0].kwargs, {'timeout': 0.2})
                process.join.assert_called_with()
            for process in processes[:5]:
                process.terminate.assert_called_once()
            processes[5].terminate.assert_not_called()

    def test_start_failure_stops_and_joins_only_started_children(self):
        with tempfile.TemporaryDirectory() as directory:
            processes = self.controller_processes(start_error=3)
            context = Mock(); context.Process.side_effect = processes
            with patch.object(subject, 'prepare', new=AsyncMock(return_value={'plan_sha256': 'a' * 64})), \
                 patch.object(subject.multiprocessing, 'get_context', return_value=context):
                with self.assertRaisesRegex(OSError, 'Injected process start failure'):
                    subject.main(type('Args', (), {'root': directory, 'prepare': False, 'worker': None})())
            for process in processes[:3]:
                process.terminate.assert_called_once()
                process.join.assert_called_once_with()
            for process in processes[3:]:
                process.terminate.assert_not_called()
                process.join.assert_not_called()
            for process in processes[4:]:
                process.start.assert_not_called()

    def test_successful_controller_joins_all_six_children_without_termination(self):
        with tempfile.TemporaryDirectory() as directory:
            processes = self.controller_processes()
            for process in processes:
                def join(timeout=None, process=process):
                    process.exitcode = 0
                process.join.side_effect = join
            context = Mock(); context.Process.side_effect = processes
            with patch.object(subject, 'prepare', new=AsyncMock(return_value={'plan_sha256': 'a' * 64})), \
                 patch.object(subject.multiprocessing, 'get_context', return_value=context):
                subject.main(type('Args', (), {'root': directory, 'prepare': False, 'worker': None})())
            for process in processes:
                process.start.assert_called_once()
                process.join.assert_called_with()
                process.terminate.assert_not_called()

    def test_runtime_layout_requires_six_workers_and_3000_keys_with_bounded_sql_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan = {'plan_sha256': 'a' * 64}
            self.assertEqual(subject.runtime_layout(root, plan), {
                'workers': 6, 'page_size': 3000, 'sql_batch_size': 300, **plan})
            for workers, page_size, batch in ((8, 5000, 300), (8, 3000, 300),
                                              (6, 5000, 300), (6, 3000, 301), (6, 3000, 0)):
                with self.subTest(workers=workers, page_size=page_size, sql_batch_size=batch):
                    subject.atomic(root / 'layout.json', {'workers': workers, 'page_size': page_size,
                        'sql_batch_size': batch, **plan})
                    with self.assertRaisesRegex(RuntimeError, '6-worker/3000-key'):
                        subject.runtime_layout(root, plan)

    def test_worker_cli_accepts_only_zero_through_five(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(subject, 'worker_entry') as entry:
            for number in (-1, 6, 7):
                with self.subTest(worker=number), self.assertRaisesRegex(ValueError, '0 through 5'):
                    subject.main(type('Args', (), {'root': directory, 'prepare': False, 'worker': number})())
            entry.assert_not_called()
            for number in (0, 5):
                subject.main(type('Args', (), {'root': directory, 'prepare': False, 'worker': number})())
                entry.assert_called_with(str(Path(directory).resolve()), number)

    def test_dedicated_facts_tail_authorizes_only_its_disjoint_six_worker_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scopes = [{'old': old, 'new': 'financial_fact_sec_edgar', 'data_type': 'financial_fact',
                       'relations': [{'schema': 'ts_' + layer, 'name': layer + '_financial_fact', 'layer': layer}
                                     for layer in ('rds', 'ods')]}
                      for old in ('sec_edgar_financial_fact', 'sec_edgar_company')]
            scope = {'protocol': 'source_safe_facts_full_alias_tail_v1', 'workers': 6,
                     'batch_size': 300, 'guard_table': None, 'scopes': scopes}
            subject.atomic(root / 'scope.json', scope)
            plan = {'plan_sha256': subject.sha((root / 'scope.json').read_bytes())}
            layout = subject.runtime_layout(root, plan)
            self.assertEqual(layout['scope'], 'facts_full')
            units = []
            for source in scopes:
                for layer in ('rds', 'ods'):
                    for ordinal in range(32):
                        units.append({'unit': len(units), 'ordinal': ordinal, 'schema': 'ts_' + layer,
                            'table': layer + '_financial_fact', 'old': source['old'],
                            'new': source['new'], 'data_type': source['data_type']})
            assigned = [{u['unit'] for u in units if subject.unit_owner(u, layout) == n} for n in range(6)]
            self.assertEqual(set.union(*assigned), set(range(128)))
            self.assertEqual(sum(map(len, assigned)), 128)
            self.assertTrue(all(subject.unit_owner(u, {'workers': 6}) is None for u in units))
            for bad in ({**scope, 'scopes': scopes[:1]},
                        {**scope, 'scopes': [{**scopes[0], 'new': 'other_source'}, scopes[1]]},
                        {**scope, 'workers': 8}, {**scope, 'guard_table': subject.GUARD_TABLE}):
                subject.atomic(root / 'scope.json', bad)
                with self.assertRaisesRegex(RuntimeError, 'facts source-tail scope'):
                    subject.runtime_layout(root, plan)

    def test_facts_layout_flag_cannot_enable_original_history_units(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); plan = {'plan_sha256': 'a' * 64}
            subject.atomic(root / 'scope.json', {'protocol': 'sec_history_source_dedup_v1'})
            subject.atomic(root / 'layout.json', {**plan, 'scope': 'facts_full'})
            with self.assertRaisesRegex(RuntimeError, 'immutable scope'):
                subject.runtime_layout(root, plan)

    def test_first_keyset_page_omits_null_cursor_and_binds_limit_third(self):
        query, parameters = subject.keyset_page_query(UNIT, None, 3000)
        self.assertEqual(parameters, [OLD, KIND, 3000])
        self.assertIn('FROM ONLY "ts_rds"."rds_filing_p0"', query)
        self.assertIn('ORDER BY record_id LIMIT $3', query)
        self.assertNotIn('record_id>', query)
        self.assertNotIn('IS NULL', query)
        self.assertNotIn(' OR ', query)

    def test_resume_keyset_page_keeps_explicit_index_lower_bound_and_cursor(self):
        cursor = '541d4e5c03517dbb5aba8e202473b73e'
        query, parameters = subject.keyset_page_query(UNIT, cursor, 3000)
        self.assertEqual(parameters, [OLD, KIND, cursor, 3000])
        self.assertIn('AND record_id>$3::text', query)
        self.assertIn('ORDER BY record_id LIMIT $4', query)
        self.assertNotIn('IS NULL', query)
        self.assertNotIn(' OR ', query)

    def test_batch_limits_and_oversized_identity(self):
        rows = [{'old_raw': '{}', 'new_raw': None}] * 3001
        self.assertEqual(len(subject.bounded(rows, 3000)), 3000)
        with self.assertRaises(ValueError):
            subject.bounded(rows, 3001)
        with self.assertRaises(RuntimeError):
            subject.bounded([{'old_raw': 'x' * subject.MAX_BYTES, 'new_raw': None}])

    def test_exact_allowed_source_pairs_only_and_no_unrelated_id_dedup(self):
        a, b = raw(record('same-id', OLD)), raw(record('same-id', 'other_website'))
        result = subject.classify([{'record_id': 'same-id', 'data_type': KIND}],
            {('same-id', OLD, KIND): a, ('same-id', 'other_website', KIND): b}, OLD, NEW)
        self.assertEqual(result[0]['action'], 'rename')


if __name__ == '__main__':
    unittest.main()
