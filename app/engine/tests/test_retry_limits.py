import unittest
from unittest.mock import AsyncMock, patch

from app.adapters import BaseSiteAdapter
from app.base.http import ResponseValidationError
from app.engine import spider_engine as module
from app.models.template import SiteTemplate


class RetryLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_network_or_invalid_pages_fail_without_advancing(self):
        for response_type in ("html", "json"):
            for error in (ConnectionError("broken tunnel"), ResponseValidationError("challenge page")):
                with self.subTest(response_type=response_type, error=type(error).__name__):
                    template = SiteTemplate(
                        name="fixture", base_url="https://fixture.invalid", data_type="news",
                        response_type=response_type, list_page="/?page={page}",
                        list_fields=[], dedup_fields=["id"],
                    )
                    client = AsyncMock()
                    client.request_page.side_effect = error
                    storage = AsyncMock()
                    engine = module.SpiderEngine(http_client=client, storage=storage, adapter_class=BaseSiteAdapter)
                    with patch.object(module, "_init_enhancements"):
                        result = await engine.crawl(template)
                    self.assertFalse(result.success)
                    self.assertIn("after 5 attempts", str(result.error))
                    self.assertEqual(client.request_page.await_count, 5)
                    storage.save_records.assert_not_awaited()
                    self.assertEqual(result.pages_processed, 0)

    async def test_failed_saves_do_not_report_records_as_saved(self):
        template = SiteTemplate(
            name="fixture", base_url="https://fixture.invalid", data_type="news",
            list_page="/", list_fields=[], dedup_fields=["id"],
        )
        storage = AsyncMock()
        storage.save_records.side_effect = ConnectionError("database unavailable")
        engine = module.SpiderEngine(http_client=AsyncMock(), storage=storage)
        result = module.CrawlResult("fixture", "news")
        with patch.object(module, "controlled_sleep", AsyncMock()):
            with self.assertRaisesRegex(RuntimeError, "after 5 attempts"):
                await engine._save_page_records(template, [{"id": "1"}], result)
        self.assertEqual(storage.save_records.await_count, 5)
        self.assertEqual(result.saved_records, 0)
