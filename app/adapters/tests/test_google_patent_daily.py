import asyncio
import json
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from app.logger.context import reset_database_only_logging, set_database_only_logging

_logging_token = set_database_only_logging(True)
try:
    from app.adapters.google_patent_daily import GooglePatentDailyAdapter
    from app.crawler.incremental import TimeWatermark
    from app.downloader.http_client import DownloadError
finally:
    reset_database_only_logging(_logging_token)

from app.utils.runtime_control import reset_control_checkpoint, set_control_checkpoint


class TestGooglePatentDailyWatermark(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with patch("app.adapters.get_adapter_logger", return_value=Mock()):
            self.adapter = GooglePatentDailyAdapter("https://patents.google.com", Mock())
        self.template = SimpleNamespace(_param_values={}, list_pagination=None)
        self.watermark = TimeWatermark(
            True, "publication_date", datetime(2026, 10, 18, 20, tzinfo=timezone.utc), "%Y%m%d",
        )
        released = patch(
            "app.adapters.google_patent_daily.TemplateLoader.load_released",
            new_callable=AsyncMock, return_value=SimpleNamespace(adapter_class=object()),
        )
        self.load_released = released.start()
        self.addCleanup(released.stop)

    async def test_requires_an_enabled_watermark_with_a_value(self):
        for watermark in (
            None,
            TimeWatermark(False, "", self.watermark.value, ""),
            TimeWatermark(True, "publication_date", None, "%Y%m%d"),
        ):
            with self.subTest(watermark=watermark):
                self.adapter._crawl_context = {"incremental_watermark": watermark}
                with self.assertRaisesRegex(RuntimeError, "requires an incremental watermark"):
                    await self.adapter.on_before_crawl(self.template)
        self.load_released.assert_not_awaited()

    async def test_start_uses_watermark_lookback_in_shanghai_and_ignores_start_date(self):
        for start_date in (None, "2026-08-31", "2026-10-19"):
            with self.subTest(start_date=start_date):
                self.template._param_values = {"start_date": start_date} if start_date else {}
                self.adapter._crawl_context = {"incremental_watermark": self.watermark}
                await self.adapter.on_before_crawl(self.template)
                self.assertEqual(self.adapter._start, date(2026, 10, 18))

    async def test_sitemap_cursor_can_only_move_start_backwards(self):
        redis = Mock(hget=AsyncMock())
        connection = Mock(ensure_connected=AsyncMock(return_value=redis))
        self.adapter._crawl_context = {
            "incremental_watermark": self.watermark, "workspace_task_id": "patent-daily",
        }
        with patch("app.adapters.google_patent_daily.RedisClient", return_value=connection):
            for cursor, expected in (
                ("2026-10-12", date(2026, 10, 12)),
                ("2026-10-19", date(2026, 10, 18)),
            ):
                with self.subTest(cursor=cursor):
                    redis.hget.return_value = cursor
                    await self.adapter.on_before_crawl(self.template)
                    self.assertEqual(self.adapter._start, expected)
        redis.hget.assert_awaited_with("checkpoint:patent-daily", "sitemap_week")


class TestGooglePatentDailyRequest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = Mock()
        self.client.request_page = AsyncMock()
        self.client.mark_last_proxy_failed = AsyncMock()
        with patch("app.adapters.get_adapter_logger", return_value=Mock()):
            self.adapter = GooglePatentDailyAdapter("https://patents.google.com", self.client)
        self.adapter._template = SimpleNamespace(effective_anti_crawl_enabled=True)
        self.url = "https://patents.google.com/xhr/query?url=q%3Dtest"
        self.config = object()
        self.response = {"results": {"total_num_results": 0}}

    async def test_retries_past_three_and_seven_failures_without_waiting(self):
        for failures in (4, 8):
            with self.subTest(failures=failures):
                self.client.reset_mock()
                self.client.request_page.side_effect = (
                    [OSError("connection reset")] * failures + [json.dumps(self.response)]
                )
                with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
                    result = await self.adapter._request(self.url, self.config, True, page=2)
                self.assertEqual(result["results"]["cluster"], [])
                self.client.request_page.assert_has_awaits([
                    call(self.url, self.config, anti_crawl_enabled=True,
                         adapter_name="google_patent", page=2, attempt=attempt,
                         rotate_proxy=attempt > 0)
                    for attempt in range(failures + 1)
                ])
                self.assertEqual(self.client.request_page.await_count, failures + 1)
                self.assertEqual(self.client.mark_last_proxy_failed.await_args_list,
                                 [call("google_patent")] * failures)
                sleep.assert_not_awaited()

    async def test_network_failures_rotate_even_with_pre_proxy_configured(self):
        self.client.request_page.side_effect = [
            asyncio.TimeoutError("timed out"),
            DownloadError(self.url, message="proxy failed"),
            json.dumps(self.response),
        ]
        with patch("app.adapters.google_patent_daily.settings.proxy_pre_proxy_url",
                   "http://pre-proxy.example:8080"):
            await self.adapter._request(self.url, self.config, True)
        self.assertEqual([item.kwargs["rotate_proxy"]
                          for item in self.client.request_page.await_args_list],
                         [False, True, True])
        self.assertEqual(self.client.mark_last_proxy_failed.await_count, 2)
        self.client._create_client.assert_not_called()

    async def test_control_checkpoint_can_cancel_repeated_failures(self):
        self.client.request_page.side_effect = OSError("connection reset")
        checkpoint = AsyncMock(side_effect=[True] * 8 + [False])
        token = set_control_checkpoint(checkpoint)
        try:
            with self.assertRaises(asyncio.CancelledError):
                await self.adapter._request(self.url, self.config, True)
        finally:
            reset_control_checkpoint(token)
        self.assertEqual(checkpoint.await_count, 9)
        self.assertEqual(self.client.request_page.await_count, 8)
        self.assertEqual(self.client.mark_last_proxy_failed.await_count, 8)

    async def test_cancelled_request_is_not_retried(self):
        self.client.request_page.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.adapter._request(self.url, self.config, True)
        self.client.request_page.assert_awaited_once()
        self.client.mark_last_proxy_failed.assert_not_awaited()

    async def test_google_query_rejection_is_not_retried(self):
        self.client.request_page.return_value = json.dumps({
            "results": {"user_error": "Invalid query"},
        })
        with self.assertRaisesRegex(RuntimeError, "Google Patent query rejected: Invalid query"):
            await self.adapter._request(self.url, self.config, True)
        self.client.request_page.assert_awaited_once()
        self.client.mark_last_proxy_failed.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
