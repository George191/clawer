import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.logger.context import reset_database_only_logging, set_database_only_logging

_token = set_database_only_logging(True)
try:
    from app.adapters import BaseSiteAdapter, GenericAdapter
    from app.downloader.http_client import HttpClient
    from app.engine.spider_engine import SpiderEngine
    from app.models.template import RequestConfig, SiteTemplate, TemplateParam
finally:
    reset_database_only_logging(_token)


def template():
    return SiteTemplate(
        name="engine_test", base_url="https://example.org", data_type="satellite",
        adapter="download_fixture", response_type="html", list_page="/catalog.json",
        anti_crawl_enabled=False, download_use_proxy=False,
        list_request=RequestConfig(headers={"Accept": "application/json", "X-Existing": "preserved"}),
        list_fields=[{"name": "source_id", "selector": "//div[@class='record']", "selector_type": "xpath"}],
        dedup_fields=["source_id"],
    )


class HeaderDownloadAdapter(BaseSiteAdapter):
    async def on_before_crawl(self, template):
        self._template = template

    def on_request_headers(self, page):
        return {"Accept": "application/octet-stream", "X-Adapter": "enabled"}

    async def request_list_page(self, url, config, **kwargs):
        content = await self._client.download_bytes(
            url, config, use_proxy=self._template.effective_download_use_proxy,
        )
        return content.decode("utf-8")

    async def parse_list_response(self, page, content):
        return json.loads(content)

    def on_page_advance(self):
        return False

    async def on_error(self, error, page, attempt):
        return "abort" if isinstance(error, ValueError) else None


class TestHtmlEngineAdapterIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for target in ("app.adapters.get_adapter_logger", "app.engine.spider_engine.logger"):
            active = patch(target, return_value=Mock()) if target.endswith("get_adapter_logger") else patch(target, Mock())
            active.start()
            self.addCleanup(active.stop)
        enhancements = patch("app.engine.spider_engine._init_enhancements")
        enhancements.start()
        self.addCleanup(enhancements.stop)
        self.client = Mock(request_page=AsyncMock(), download_bytes=AsyncMock())
        self.storage = Mock(save_records=AsyncMock(return_value=["stored-1"]), last_save_stats={})

    async def test_custom_download_request_receives_headers_and_saves_parsed_record(self):
        self.client.download_bytes.return_value = (
            b'[{"source_id":"25544","satellite_name":"ISS","norad_id":25544}]'
        )
        config = template()
        engine = SpiderEngine(http_client=self.client, storage=self.storage, adapter_class=HeaderDownloadAdapter)
        result = await engine.crawl(config)
        self.assertTrue(result.success, result.errors)
        self.assertEqual(result.saved_records, 1)
        self.assertEqual(result.pages_processed, 1)
        self.assertEqual(result.records[0]["satellite_name"], "ISS")
        self.assertEqual(result.records[0]["norad_id"], 25544)
        self.client.request_page.assert_not_awaited()
        url, request = self.client.download_bytes.await_args.args
        self.assertEqual(url, "https://example.org/catalog.json")
        self.assertEqual(request.headers, {"Accept": "application/octet-stream", "X-Existing": "preserved", "X-Adapter": "enabled"})
        self.assertEqual(config.list_request.headers, {"Accept": "application/json", "X-Existing": "preserved"})
        self.assertEqual(self.client.download_bytes.await_args.kwargs, {"use_proxy": False})
        self.storage.save_records.assert_awaited_once_with(
            "engine_test", "satellite", ["source_id"], result.records,
        )

    async def test_generic_html_adapter_keeps_existing_http_and_template_parsing(self):
        self.client.request_page.return_value = '<html><div class="record">25544</div></html>'
        config = template()
        config.adapter = None
        engine = SpiderEngine(http_client=self.client, storage=self.storage, adapter_class=GenericAdapter)
        result = await engine.crawl(config)
        self.assertTrue(result.success, result.errors)
        self.assertEqual(result.records, [{"source_id": "25544"}])
        self.assertIs(self.client.request_page.await_args.args[1], config.list_request)
        self.assertEqual(self.client.request_page.await_args.kwargs, {
            "anti_crawl_enabled": False, "adapter_name": None, "page": 1, "attempt": 0,
        })
        self.client.download_bytes.assert_not_awaited()
        self.storage.save_records.assert_awaited_once()

    async def test_malformed_custom_response_aborts_instead_of_retrying_forever(self):
        self.client.download_bytes.return_value = b"<html>Service unavailable</html>"
        engine = SpiderEngine(http_client=self.client, storage=self.storage, adapter_class=HeaderDownloadAdapter)
        # Bound the retry loop so a regression fails promptly.
        with patch.object(engine, "_retry_loop", return_value=iter(range(2))):
            result = await engine.crawl(template())
        self.assertFalse(result.success)
        self.assertIn("Expecting value", result.errors[0])
        self.client.download_bytes.assert_awaited_once()
        self.storage.save_records.assert_not_awaited()

    async def test_sensitive_parameters_are_excluded_from_saved_record_metadata(self):
        self.client.request_page.return_value = '<html><div class="record">25544</div></html>'
        config = template()
        config.adapter = None
        config.params = [TemplateParam(name="api_key", sensitive=True), TemplateParam(name="year")]
        config._param_values = {"api_key": "test-secret", "year": "2025"}
        engine = SpiderEngine(http_client=self.client, storage=self.storage, adapter_class=GenericAdapter)
        result = await engine.crawl(config)
        self.assertTrue(result.success, result.errors)
        saved_records = self.storage.save_records.await_args.args[3]
        self.assertEqual(saved_records[0]["_meta_search_params"], {"year": "2025"})
        self.assertNotIn("test-secret", repr(saved_records))

class TestHttpRequestBody(unittest.IsolatedAsyncioTestCase):
    async def test_json_and_legacy_form_bodies_are_forwarded_exclusively(self):
        for json_data, form_data, body_key, expected in (
            ({"skip": 0, "take": 100}, {}, "json", {"skip": 0, "take": 100}),
            ({}, {"unused": "value"}, "json", {}),
            (None, {"keyword": "ISS"}, "data", {"keyword": "ISS"}),
        ):
            with self.subTest(json_data=json_data, form_data=form_data):
                response = Mock(status_code=200, text='{"table": []}')
                session = Mock(request=AsyncMock(return_value=response))
                context = AsyncMock()
                context.__aenter__.return_value = session
                client = HttpClient()
                config = RequestConfig(method="POST", json_data=json_data, form_data=form_data)
                with (
                    patch.object(client, "_create_client", AsyncMock(return_value=context)),
                    patch.object(client, "_before_request", AsyncMock(return_value="direct")),
                    patch.object(client, "_after_request", AsyncMock()),
                    patch("app.downloader.http_client._proxy_pool", None),
                ):
                    result = await client.request_page(
                        "https://example.org/query", config, anti_crawl_enabled=False,
                        force_direct=True, no_timeout=True,
                    )
                self.assertEqual(result, '{"table": []}')
                forwarded = session.request.await_args.kwargs
                self.assertEqual(forwarded[body_key], expected)
                self.assertNotIn("data" if body_key == "json" else "json", forwarded)
                response.raise_for_status.assert_called_once()
