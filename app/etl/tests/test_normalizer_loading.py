"""Exercise cold imports in fresh interpreters, without external services."""

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class NormalizerLoadingTests(unittest.TestCase):
    def run_cold(self, source):
        result = subprocess.run(
            [sys.executable, "-B", "-c", textwrap.dedent(source)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_package_and_base_imports_do_not_register_normalizers(self):
        self.run_cold("""
            import sys
            import app.etl.normalizers as normalizers
            assert normalizers._NORMALIZER_REGISTRY == {}
            from app.etl.normalizers.base import safe_str
            assert safe_str(' value ') == 'value'
            assert normalizers._NORMALIZER_REGISTRY == {}
            assert {name for name in sys.modules if name.startswith(normalizers.__name__ + '.')} == {
                'app.etl.normalizers.base',
            }
        """)

    def test_direct_news_import_registers_only_news(self):
        self.run_cold("""
            import sys
            import app.etl.normalizers as normalizers
            from app.etl.normalizers import news
            assert normalizers._NORMALIZER_REGISTRY
            assert all(key.startswith('news:') for key in normalizers._NORMALIZER_REGISTRY)
            assert 'app.etl.normalizers.space' not in sys.modules
            assert 'app.etl.normalizers.vulnerability' not in sys.modules
            assert normalizers.get_normalizer('news', 'news_space') is news.normalize_space_news
            assert normalizers.get_normalizer('news', 'news_n2yo') is news.normalize_n2yo_news
        """)

    def test_lazy_builtin_families_are_isolated(self):
        cases = (
            ('acticle', 'acticle_wechat', 'acticle', ('acticle',)),
            ('intelligence', 'intelligence_planet', 'intelligence', ('intelligence',)),
            ('navwarn', 'navwarn_nga', 'navwarn', ('navwarn',)),
            ('news', 'news_space', 'news', ('news',)),
            ('patent', 'patent_google', 'patent', ('patent',)),
            ('financial_fact', 'filing_sec_edgar', 'financial_fact', ('financial_fact',)),
            ('contract', 'contract_sam', 'procurement', ('contract', 'tender')),
            ('tender', 'tender_sam', 'procurement', ('contract', 'tender')),
            ('search_result', 'search_bing', 'search_result', ('search_result',)),
            ('account', 'account_twitter', 'social_account', ('account',)),
            ('tweet', 'tweet_twitter', 'social_content', ('tweet',)),
            ('vulnerability', 'nvd', 'vulnerability', ('vulnerability',)),
        )
        for data_type, source, module, registered_types in cases:
            with self.subTest(data_type=data_type):
                self.run_cold(f"""
                    import sys
                    import app.etl.normalizers as normalizers
                    fn = normalizers.get_normalizer({data_type!r}, {source!r})
                    assert fn is not None and fn.__module__ == 'app.etl.normalizers.' + {module!r}
                    assert {{key.split(':')[0] for key in normalizers._NORMALIZER_REGISTRY}} == set({registered_types!r})
                    assert 'app.etl.normalizers.filing_document' not in sys.modules
                    assert normalizers.get_normalizer({data_type!r}, {source!r}) is fn
                """)

    def test_space_types_share_one_module_and_generic_lookup(self):
        self.run_cold("""
            import sys
            import app.etl.normalizers as normalizers
            kinds = {
                'constellation', 'debrisevents', 'debris', 'launchvehicles', 'frequency',
                'groundstations', 'launchinfo', 'satellite', 'launchsites', 'orbital',
                'organization',
            }
            for kind in kinds - {'organization'}:
                fn = normalizers.get_normalizer(kind, 'fixture_source')
                assert fn.__name__ == 'normalize_' + kind
            assert set(normalizers._NORMALIZER_REGISTRY) == {kind + ':*' for kind in kinds}
            assert 'app.etl.normalizers.organization' not in sys.modules
            assert 'app.etl.normalizers.news' not in sys.modules
        """)

    def test_organization_specific_loads_when_space_generic_is_registered(self):
        self.run_cold("""
            import app.etl.normalizers as normalizers
            from app.etl.normalizers import space
            assert 'organization:organization_sec_edgar' not in normalizers._NORMALIZER_REGISTRY
            fn = normalizers.get_normalizer('organization', 'organization_sec_edgar')
            from app.etl.normalizers.organization import normalize_sec_edgar_organization
            assert fn is normalize_sec_edgar_organization
            assert normalizers.get_normalizer('organization', 'organization_gcat') is space.normalize_organization
        """)

    def test_filing_documents_load_only_when_needed(self):
        self.run_cold("""
            import sys
            import app.etl.normalizers as normalizers
            fn = normalizers.get_normalizer('filing', 'filing_sec_edgar')
            assert set(normalizers._NORMALIZER_REGISTRY) == {'filing:filing_sec_edgar'}
            assert 'app.etl.normalizers.filing_document' not in sys.modules
            rows = fn({'data_type': 'filing', 'data_source': 'filing_sec_edgar'})
            assert len(rows) == 1 and rows[0]['data_type'] == 'filing'
            document_fn = normalizers.get_normalizer('filing_document', 'filing_sec_edgar')
            assert document_fn.__module__ == 'app.etl.normalizers.filing_document'
            assert set(normalizers._NORMALIZER_REGISTRY) == {
                'filing:filing_sec_edgar', 'filing_document:filing_sec_edgar',
            }
        """)

    def test_explicit_specific_registration_precedes_builtin_loading(self):
        self.run_cold("""
            import sys
            import app.etl.normalizers as normalizers
            def custom(record):
                return record
            normalizers.register_normalizer('news', 'news_space', custom)
            assert normalizers.get_normalizer('news', 'news_space') is custom
            assert 'app.etl.normalizers.news' not in sys.modules
            normalizers.register_normalizer('fixture', '*', custom)
            assert normalizers.get_normalizer('fixture', 'unknown_source') is custom
        """)

    def test_missing_normalizer_preserves_warning_and_returns_none(self):
        self.run_cold("""
            from unittest.mock import patch
            import app.etl.normalizers as normalizers
            with patch.object(normalizers.logger, 'warning') as warning:
                assert normalizers.get_normalizer('unknown_type', 'unknown_source') is None
            warning.assert_called_once_with(
                'No source-specific normalizer registered for %s, fallback to generic',
                'unknown_type:unknown_source',
            )
            assert normalizers._NORMALIZER_REGISTRY == {}
        """)
