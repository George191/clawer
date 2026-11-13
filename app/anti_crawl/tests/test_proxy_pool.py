"""Proxy pool supplier-outage and recovery regressions with mocked HTTP."""

import asyncio
import logging
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

for root in (Path.cwd(), *Path(__file__).resolve().parents):
    if (root / "app" / "__init__.py").is_file():
        sys.path.insert(0, str(root))
        break

from app.anti_crawl.adapters.base import ProxyInfo, ProxySourceAdapter
from app.anti_crawl.adapters.zdopen import ZdopenAPIAdapter
from app.anti_crawl.proxy_pool import ProxyPool


class Source(ProxySourceAdapter):
    def __init__(self, name='source', responses=None, *, fallback=False):
        super().__init__({'fallback_only': fallback})
        self._name = name
        self.responses = list(responses or [[]])
        self.calls = 0

    @property
    def name(self):
        return self._name

    def validate_config(self):
        return True

    async def fetch(self):
        self.calls += 1
        await asyncio.sleep(0)
        result = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return result


class ProxyReloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_empty_pool_fetches_once_and_reload_cannot_bypass_cooldown(self):
        source = Source()
        pool = ProxyPool()
        pool.register_adapter(source)
        leases, sticky = await asyncio.gather(
            asyncio.gather(*(pool.lease_proxy(i) for i in range(20))),
            asyncio.gather(*(pool.get_proxy() for _ in range(20))),
        )
        self.assertEqual(leases + sticky, [None] * 40)
        await asyncio.gather(*(pool.reload() for _ in range(10)))
        self.assertEqual(source.calls, 1)
        self.assertGreater(pool._next_load_at, time.monotonic())

    async def test_pool_recovers_after_cooldown(self):
        source = Source(responses=[[], [ProxyInfo('http://192.0.2.1:8080')]])
        pool = ProxyPool()
        pool.register_adapter(source)
        self.assertIsNone(await pool.lease_proxy(1))
        self.assertIsNone(await pool.get_proxy())
        self.assertEqual(source.calls, 1)
        pool._next_load_at = time.monotonic() - 1
        self.assertEqual(await pool.lease_proxy(2), 'http://192.0.2.1:8080')
        self.assertEqual(source.calls, 2)
        self.assertEqual(await pool.lease_proxy(2), 'http://192.0.2.1:8080')
        self.assertEqual(source.calls, 2)

    async def test_supplier_exception_also_enters_empty_pool_cooldown(self):
        source = Source(responses=[OSError('supplier unavailable')])
        pool = ProxyPool()
        pool.register_adapter(source)
        self.assertIsNone(await pool.lease_proxy(1))
        self.assertIsNone(await pool.lease_proxy(2))
        await pool.reload()
        self.assertEqual(source.calls, 1)

    async def test_rate_limit_returns_once_and_allows_fallback(self):
        primary = ZdopenAPIAdapter({'url': 'http://supplier.invalid/proxies'})
        client = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(
            raise_for_status=Mock(), json=Mock(return_value={'code': '12002'}))))
        primary._get_client = AsyncMock(return_value=client)
        fallback = Source('fallback', [[ProxyInfo('http://192.0.2.2:8080')]], fallback=True)
        pool = ProxyPool()
        pool.register_adapter(primary)
        pool.register_adapter(fallback)
        self.assertEqual(await asyncio.wait_for(pool.lease_proxy(1), 0.5),
                         'http://192.0.2.2:8080')
        client.get.assert_awaited_once()
        self.assertEqual(fallback.calls, 1)

    async def test_success_preserves_unique_leases_and_failed_host_exclusion(self):
        first, second = 'http://192.0.2.1:8080', 'http://192.0.2.2:8080'
        source = Source(responses=[[ProxyInfo(first), ProxyInfo(second)]])
        pool = ProxyPool()
        pool.register_adapter(source)
        self.assertEqual(await pool.lease_proxy(1, 'site-a'), first)
        self.assertEqual(await pool.lease_proxy(2, 'site-a'), second)
        await pool.mark_failure(first, 'site-a')
        self.assertEqual(await pool.lease_proxy(1, 'site-a', exclude_proxy_url=first), second)
        await pool.release_proxy(2)
        self.assertEqual(await pool.lease_proxy(3, 'site-b'), first)
        self.assertEqual(source.calls, 1)


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
