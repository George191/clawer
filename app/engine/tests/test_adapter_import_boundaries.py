"""Published bundles defer ETL imports until their normalizer is invoked."""

import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

from app.adapters import load_adapter_module_from_source, validate_adapter_imports
from app.adapters.runtime import validate_extension_source


class AdapterImportBoundaryTests(unittest.TestCase):
    def test_loading_and_publication_reject_eager_etl_imports(self):
        cases = (
            "import app.etl",
            "import app.etl.normalizers as normalizers",
            "from app.etl.normalizers.base import safe_str",
            "from app import etl as pipeline",
            "from .. import etl",
            "from ..etl.normalizers import get_normalizer",
            "try:\n    from app.etl import normalizers\nexcept ImportError:\n    pass",
            "if True:\n    import app.etl.normalizers",
            "with open(__file__) as stream:\n    import app.etl.normalizers",
            "class Collector:\n    from app.etl.normalizers import get_normalizer",
            "class Collector:\n    def method(self, value=__import__('app.etl.normalizers')):\n        return value",
            "@__import__('app.etl.normalizers').decorator\ndef normalize(value):\n    return value",
            "def normalize(value=__import__('app.etl.normalizers')):\n    return value",
            "def normalize(value: __import__('app.etl.normalizers')):\n    return value",
            "def normalize(value) -> __import__('app.etl.normalizers'):\n    return value",
            "async def normalize(value=__import__('app.etl.normalizers')):\n    return value",
            "factory = lambda value=__import__('app.etl.normalizers'): value",
            "factory = (lambda: __import__('app.etl.normalizers'))()",
            "import importlib\nnormalizers = importlib.import_module('app.etl.normalizers')",
            "import importlib\nnormalizers = importlib.import_module('.etl.normalizers', 'app')",
            "normalizers = __import__('app', fromlist=['etl'])",
            "from importlib import import_module as load\ndef normalize(value=load('app.etl.normalizers')):\n    return value",
        )
        for source in cases:
            with self.subTest(source=source):
                for validate in (validate_adapter_imports, validate_extension_source):
                    with self.assertRaisesRegex(ValueError, 'ETL import.*inside a function body'):
                        validate(source)
                with self.assertRaisesRegex(ValueError, 'ETL import.*inside a function body'):
                    load_adapter_module_from_source('boundary_rejected', source, 'fixture/adapter.py')

    def test_lazy_function_method_lambda_and_nested_class_imports_are_allowed(self):
        source = textwrap.dedent('''
            import json
            def normalize(record):
                from app.etl.normalizers.acticle import normalize_acticle
                class Helper:
                    import app.etl.normalizers.base
                return normalize_acticle(record)
            async def normalize_async(record):
                import app.etl.normalizers
                return normalize(record)
            class Collector:
                def normalize(self, record):
                    from app import etl
                    return record
            factory = lambda: __import__('app.etl.normalizers')
            NORMALIZERS = {'acticle': normalize}
        ''')
        self.assertIsInstance(validate_adapter_imports(source), ast.Module)
        self.assertEqual(validate_extension_source(source), {'NORMALIZERS'})

    def test_validation_runs_before_any_source_execution(self):
        source = "raise RuntimeError('must not execute')\nimport app.etl.normalizers"
        with self.assertRaisesRegex(ValueError, 'ETL import'):
            load_adapter_module_from_source('boundary_not_executed', source, 'fixture/adapter.py')
        name = 'app.adapters._runtime_boundary_not_executed_' + hashlib.sha256(source.encode()).hexdigest()[:16]
        self.assertNotIn(name, sys.modules)

    def test_cold_collector_record_and_ods_paths_defer_sync_and_async_normalizers(self):
        fixture = textwrap.dedent('''
            from app.adapters import BaseSiteAdapter, register_adapter
            from app.records.adapters import RecordAdapter

            @register_adapter('boundary_fixture')
            class FixtureCollector(BaseSiteAdapter):
                pass

            class FixtureRecords(RecordAdapter):
                pass

            {async_prefix}def normalize(record):
                from app.etl.normalizers.acticle import normalize_acticle
                return normalize_acticle(record)

            NORMALIZERS = {{'acticle': normalize}}
            RECORD_ADAPTERS = {{'acticle': FixtureRecords}}
            ODS_FIELDS = {{}}
        ''')
        runner = textwrap.dedent('''
            import asyncio, inspect, json, sys
            from types import SimpleNamespace
            from unittest.mock import AsyncMock, patch
            import app.adapters as adapters
            from app.adapters import runtime
            from app.records.adapters import get_record_adapter
            source = SOURCE
            def cold():
                assert not any(name.startswith('app.etl.normalizers') for name in sys.modules)
            cold()
            collector = adapters.load_adapter_class_from_source('boundary_fixture', source, 'fixture/adapter.py')
            cold()
            assert collector.__name__ == 'FixtureCollector'
            assert 'boundary_fixture' not in adapters._ADAPTER_REGISTRY
            row = {'name': 'boundary_fixture', 'adapter': 'fixture/adapter.py'}
            pg = SimpleNamespace(fetch_one=AsyncMock(return_value=row))
            store = SimpleNamespace(get_object_bytes=AsyncMock(return_value=source.encode()))
            async def run():
                route = {'template_name': 'boundary_fixture', 'data_type': 'acticle'}
                with patch.object(runtime, 'get_pg_client', return_value=pg), patch.object(
                        runtime, 'get_business_metadata_minio_client', return_value=store):
                    record_adapter = await get_record_adapter(None, route, ['id'])
                    assert record_adapter.clean({'id': 'unchanged'}) == {'id': 'unchanged'}
                    cold()
                    module = await runtime.get_source_module('boundary_fixture', export='NORMALIZERS')
                    cold()
                    result = module.NORMALIZERS['acticle']({
                        '_meta': {'record_id': 'fixture-id'}, 'data_source': 'acticle_wechat',
                        'title': 'fixture title', 'content_html': '<p>fixture body</p>'})
                    if inspect.isawaitable(result):
                        cold()
                        result = await result
                    assert result['record_id'] == 'fixture-id'
                    assert result['data_type'] == 'acticle'
                    assert result['title'] == 'fixture title'
                    assert 'app.etl.normalizers.acticle' in sys.modules
                    assert 'app.etl.normalizers.vulnerability' not in sys.modules
                    assert adapters.load_adapter_module_from_source(
                        'boundary_fixture', source, 'fixture/adapter.py') is module
                    assert 'boundary_fixture' not in adapters._ADAPTER_REGISTRY
                return {'collector_loaded': True, 'record_loaded': True, 'etl_called': True}
            print(json.dumps(asyncio.run(run())))
        ''')
        root = Path(__file__).resolve().parents[3]
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                source = fixture.format(async_prefix='async ' if asynchronous else '')
                code = runner.replace('source = SOURCE', 'source = ' + repr(source))
                result = subprocess.run([sys.executable, '-B', '-c', code], cwd=root,
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                self.assertEqual(json.loads(result.stdout), {
                    'collector_loaded': True, 'record_loaded': True, 'etl_called': True,
                })


if __name__ == '__main__':
    unittest.main()
