import asyncio
import ast
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg_worker as worker
import registry_targets as targets


class TargetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous = dict(targets.TARGETS)
        targets.TARGETS.clear()
        targets.TARGETS[('rds', 'filing')] = {
            'id': 12, 'layer': 'rds', 'schema_name': 'helio.raw.data.store', 'table_name': 'Actual "Table'}
        self.previous_objects = list(targets.OBJECTS)
        targets.OBJECTS[:] = list(targets.TARGETS.values())
        targets.CONNECTION_ID = 'connection-a'

    def tearDown(self):
        targets.TARGETS.clear(); targets.TARGETS.update(self.previous)
        targets.OBJECTS[:] = self.previous_objects

    def test_identifier_and_registry_table(self):
        self.assertEqual(targets.table('rds', 'filing'), '"helio.raw.data.store"."Actual ""Table"')
        for name in ('', 'a\x00b', '界' * 22):
            with self.assertRaises(ValueError):
                targets.quote(name)
        with self.assertRaises(KeyError):
            targets.table('ods', 'missing')
        template = "SELECT '{\"nested\":{}}'::jsonb FROM {table}"
        self.assertEqual(targets.render(template, 'rds', 'filing'),
                         "SELECT '{\"nested\":{}}'::jsonb FROM " + targets.table('rds', 'filing'))
        with self.assertRaises(ValueError):
            targets.render('{table} {table}', 'rds', 'filing')

    async def test_public_prepare_and_default_cli_create_then_verify_immutable_ledger(self):
        unit = {'unit': 0, 'layer': 'rds', 'data_type': 'filing', 'schema': 'old',
                'table': 'parent', 'leaf': 'leaf', 'oid': 77}
        targets.TARGETS[('rds', 'filing')].update(schema_name='old', table_name='parent')
        identity = {'connection_id': 'connection-a', 'object_id': 12, 'database_oid': 31,
                    'parent_oid': 55, 'leaf_oid': 77, 'bound': 'HASH bound',
                    'leaf_schema': 'old', 'leaf_name': 'leaf'}
        connection = AsyncMock(); connection.fetchval.return_value = 55
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'scope.json').write_bytes(worker.encoded({'scopes': []}))
            args = SimpleNamespace(root=str(root), prepare=True, worker=None,
                                   prepare_targets=False, check_targets=False)
            with patch.object(worker, 'connect', AsyncMock(return_value=connection)), \
                 patch.object(worker, 'discover', AsyncMock(return_value=[unit])), \
                 patch.object(worker, 'target_identity', AsyncMock(return_value=identity)), \
                 patch('builtins.print'):
                await asyncio.to_thread(worker.main, args)
            original = {name:(root/name).read_bytes() for name in ('plan.json','layout.json','targets.json')}
            # An existing pre-cutover plan without a ledger also keeps the
            # original --prepare behavior: capture once without changing plan.
            (root/'targets.json').unlink()
            with patch.object(worker, 'connect', AsyncMock(return_value=connection)), \
                 patch.object(worker, 'target_identity', AsyncMock(return_value=identity)), \
                 patch('builtins.print'):
                await asyncio.to_thread(worker.main, args)
            self.assertEqual(original, {name:(root/name).read_bytes() for name in original})
            targets.TARGETS[('rds', 'filing')].update(schema_name='new.with.dots', table_name='renamed')
            renamed = {**identity, 'leaf_schema':'new.with.dots', 'leaf_name':'renamed_leaf'}
            process = Mock(exitcode=0, pid=None)
            process.is_alive.return_value = False
            context = Mock(); context.Process.return_value = process
            args.prepare = False
            # Default CLI must validate the existing ledger before spawning; it
            # must not try to resolve the historical old name after a rename.
            with patch.object(worker, 'connect', AsyncMock(return_value=connection)), \
                 patch.object(worker, 'target_identity', AsyncMock(return_value=renamed)), \
                 patch.object(worker, 'target_manifest', AsyncMock(side_effect=AssertionError('regeneration forbidden'))), \
                 patch.object(worker.multiprocessing, 'get_context', return_value=context), \
                 patch('builtins.print'):
                await asyncio.to_thread(worker.main, args)
            self.assertEqual(process.start.call_count, worker.WORKERS)
            self.assertEqual(original, {name:(root/name).read_bytes() for name in original})
            (root/'targets.json').unlink()
            with patch.object(worker, 'connect', AsyncMock(return_value=connection)), \
                 patch.object(worker, 'target_identity', AsyncMock(return_value=renamed)), \
                 patch.object(worker.multiprocessing, 'get_context', return_value=context):
                with self.assertRaisesRegex(RuntimeError, 'active registered object'):
                    await asyncio.to_thread(worker.main, args)
            self.assertEqual(process.start.call_count, worker.WORKERS)

    async def test_cross_connection_rejected_before_business_connect(self):
        metadata = AsyncMock()
        metadata.fetch_all.return_value = [{'id': 'a', 'layer': 'rds', 'engine': 'postgresql'},
                                           {'id': 'b', 'layer': 'ods', 'engine': 'postgresql'}]
        connect = AsyncMock()
        with patch.dict(sys.modules, {
                'app.storage.business_database': SimpleNamespace(business_connection_options=lambda row: None),
                'app.storage.postgres_client': SimpleNamespace(get_pg_client=lambda: metadata),
                'asyncpg': SimpleNamespace(connect=connect)}):
            with self.assertRaisesRegex(RuntimeError, 'same authorized connection'):
                await targets.connect('test')
            connect.assert_not_awaited()
            metadata.close.assert_awaited_once()

    async def test_connection_preserves_credentials_tls_and_rejects_duplicate_binding(self):
        metadata = AsyncMock()
        sources = [{'id': 'a', 'layer': layer, 'engine': 'postgresql'} for layer in ('rds', 'ods')]
        objects = [{'id': 12, 'layer': 'rds', 'data_type': 'filing',
                    'schema_name': 'custom.with.dots', 'table_name': 'Different Parent'}]
        transport = object()
        connection = object()
        connect = AsyncMock(return_value=connection)
        modules = {
            'app.storage.business_database': SimpleNamespace(business_connection_options=
                lambda row: ('postgresql+asyncpg://test:password@business.test/renamed.database', {'ssl': transport})),
            'app.storage.postgres_client': SimpleNamespace(get_pg_client=lambda: metadata),
            'asyncpg': SimpleNamespace(connect=connect)}
        metadata.fetch_all.side_effect = [sources, objects]
        with patch.dict(sys.modules, modules):
            self.assertIs(await targets.connect('test'), connection)
        self.assertEqual(connect.await_args.args[0], 'postgresql://test:password@business.test/renamed.database')
        self.assertIs(connect.await_args.kwargs['ssl'], transport)
        self.assertEqual(targets.table('rds', 'filing'), '"custom.with.dots"."Different Parent"')
        connect.reset_mock()
        metadata.fetch_all.side_effect = [sources, objects + objects]
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(RuntimeError, 'Ambiguous'):
            await targets.connect('test')
        connect.assert_not_awaited()

    async def test_real_partition_identity_query_checks_registered_parent_and_planned_oid(self):
        connection = AsyncMock()
        connection.fetchrow.return_value = {'database_oid': 31, 'parent_oid': 55, 'leaf_oid': 77,
            'leaf_schema': 'helio.raw.data.store', 'leaf_name': 'leaf', 'bound': 'HASH bound'}
        unit = {'_object_id': 12, 'oid': 77, 'layer': 'rds'}
        result = await worker.target_identity(connection, unit)
        self.assertEqual(result['object_id'], 12)
        self.assertEqual(connection.fetchrow.await_args.args[1:],
                         ('"helio.raw.data.store"."Actual ""Table"', 77))
        self.assertIn('pg_partition_tree', connection.fetchrow.await_args.args[0])
        connection.fetchrow.return_value = None
        with self.assertRaisesRegex(RuntimeError, 'no longer belongs'):
            await worker.target_identity(connection, unit)

    async def test_facts_and_patent_writes_follow_registry_not_legacy_names(self):
        directory = Path(__file__).resolve().parent.parent / 'sec_migration_20261104'
        sources = {name: ast.parse((directory / (name + '.py')).read_text())
                   for name in ('facts_direct_baseline', 'patent_direct_baseline')}
        facts_sql = {node.targets[0].id: ast.literal_eval(node.value)
                     for node in sources['facts_direct_baseline'].body
                     if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                     and node.targets[0].id in ('RDS_SQL', 'ODS_SQL', 'VERIFY_SQL')}
        @asynccontextmanager
        async def transaction():
            yield
        for kind, name in [('financial_fact', 'facts_direct_baseline'), ('patent', 'patent_direct_baseline')]:
            for layer in ('rds', 'ods'):
                targets.TARGETS[(layer, kind)] = {'schema_name': 'custom.' + layer, 'table_name': 'Actual ' + kind}
            namespace = {'targets': targets, 'KIND': kind, 'SQL_BATCH': 300,
                         'prepare_rows': lambda docs: ('rds', 'ods', 'keys'), **facts_sql}
            namespace['base'] = SimpleNamespace(targets=targets, **facts_sql)
            chosen = [node for node in sources[name].body if
                      (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                       and node.targets[0].id in ('RDS_SQL', 'ODS_SQL')) or
                      (isinstance(node, ast.AsyncFunctionDef) and node.name == 'write_batch')]
            module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
                                      *chosen], type_ignores=[])
            exec(compile(ast.fix_missing_locations(module), name, 'exec'), namespace)
            connection = SimpleNamespace(transaction=transaction, execute=AsyncMock(), fetchval=AsyncMock(return_value=1))
            await namespace['write_batch'](connection, [{}])
            statements = [call.args[0] for call in connection.execute.await_args_list]
            for layer, index in [('rds', 2), ('ods', 3)]:
                self.assertIn('INSERT INTO ' + targets.table(layer, kind), statements[index])
            self.assertTrue(all('ts_rds' not in sql and 'ts_ods' not in sql for sql in statements))
            self.assertIn(targets.table('rds', kind), connection.fetchval.await_args_list[0].args[0])

    async def test_manifest_preserves_plan_and_binds_oid_after_rename(self):
        unit = {'unit': 0, 'layer': 'rds', 'data_type': 'filing',
                'schema': 'old_schema', 'table': 'old_parent', 'leaf': 'old_leaf', 'oid': 77}
        plan = {'plan_sha256': 'scope-hash', 'units': [unit]}
        identity = {'connection_id': 'connection-a', 'object_id': 12, 'database_oid': 31,
                    'parent_oid': 55, 'leaf_oid': 77, 'bound': 'HASH bound',
                    'leaf_schema': 'old_schema', 'leaf_name': 'old_leaf'}
        # Preparation must resolve the original names while they still exist.
        targets.TARGETS[('rds', 'filing')].update(schema_name='old_schema', table_name='old_parent')
        connection = AsyncMock()
        connection.fetchval.return_value = 55
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = worker.encoded(plan)
            (root / 'plan.json').write_bytes(raw)
            checkpoint = b'{"pending":"old-receipt.json","plan_sha256":"scope-hash"}'
            (root / 'checkpoint.json').write_bytes(checkpoint)
            with patch.object(worker, 'connect', AsyncMock(return_value=connection)), \
                 patch.object(worker, 'target_identity', AsyncMock(return_value=identity)):
                await worker.prepare_targets(root, plan)
            targets.TARGETS[('rds', 'filing')].update(schema_name='helio.raw.data.store', table_name='renamed_parent')
            renamed = {**identity, 'leaf_schema': 'helio.raw.data.store', 'leaf_name': 'renamed_leaf'}
            with patch.object(worker, 'target_identity', AsyncMock(return_value=renamed)):
                bound = await worker.bind_unit(connection, root, plan, unit)
            self.assertEqual(worker.unit_parent(bound), '"helio.raw.data.store"."renamed_parent"')
            self.assertEqual(worker.unit_leaf(bound), '"helio.raw.data.store"."renamed_leaf"')
            self.assertEqual(bound['schema'], 'old_schema')
            self.assertEqual(bound['table'], 'old_parent')
            self.assertEqual((root / 'plan.json').read_bytes(), raw)
            self.assertEqual((root / 'checkpoint.json').read_bytes(), checkpoint)
            for key, value in [('connection_id', 'other'), ('database_oid', 99), ('object_id', 99),
                               ('parent_oid', 99), ('leaf_oid', 99), ('bound', 'different')]:
                with self.subTest(key=key), patch.object(worker, 'target_identity',
                        AsyncMock(return_value={**renamed, key: value})):
                    with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                        await worker.bind_unit(connection, root, plan, unit)
            (root / 'plan.json').write_bytes(raw + b' ')
            with self.assertRaisesRegex(RuntimeError, 'identity manifest'):
                await worker.bind_unit(connection, root, plan, unit)

    async def test_cannot_capture_replaced_original_parent(self):
        unit = {'unit': 0, 'layer': 'rds', 'data_type': 'filing', 'schema': 'old', 'table': 'parent', 'oid': 77}
        targets.TARGETS[('rds', 'filing')].update(schema_name='old', table_name='parent')
        connection = AsyncMock(); connection.fetchval.return_value = None
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(worker, 'connect', AsyncMock(return_value=connection)), \
             patch.object(worker, 'target_identity', AsyncMock(return_value={'parent_oid': 55})):
            with self.assertRaisesRegex(RuntimeError, 'before renaming'):
                await worker.prepare_targets(Path(directory), {'units': [unit]})
            self.assertFalse((Path(directory) / 'targets.json').exists())


if __name__ == '__main__':
    unittest.main()
