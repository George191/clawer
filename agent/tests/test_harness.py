import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from agent.contracts import AgentSettings, CollectionRequest, LeaseLost, SettingsUpdate
from agent.model import CollectionModel, ModelFailure
from agent.runtime import CollectionHarness
from agent.tools import CollectionTools

TEMPLATE = """name: articles
display_name: Articles
base_url: https://example.org
data_type: article
list_page: /articles
list_fields: [{name: title, selector: h1}]
dedup_fields: [title]
"""


class ContractTests(unittest.TestCase):
    def test_only_collection_arguments_are_allowed(self):
        with self.assertRaises(ValidationError):
            CollectionRequest(url="https://example.org", goal="collect", command="rm -rf")
        with self.assertRaises(ValidationError):
            CollectionRequest(url="https://example.org", goal="collect", max_pages=101)

    def test_private_targets_and_embedded_credentials_are_rejected(self):
        for url in ["http://localhost", "http://127.0.0.1", "http://[::1]", "http://169.254.169.254",
                    "http://[::ffff:127.0.0.1]", "http://10.1.1.1", "http://192.168.1.1",
                    "https://x.internal", "https://localhost.", "https://user:password@example.org"]:
            with self.subTest(url=url), self.assertRaises((ValidationError, HTTPException)):
                CollectionRequest(url=url, goal="collect")

    def test_configuration_is_explicit_and_keys_are_secret_values(self):
        config = AgentSettings()
        self.assertEqual((config.model, config.reasoning_effort, config.enabled), ("gpt-6-sol", "high", False))
        secret = SettingsUpdate(expected_revision=0, api_key="sensitive-test-key")
        self.assertNotIn("sensitive-test-key", repr(secret))
        for endpoint in ["http://example.org/v1", "https://127.0.0.1/v1", "https://example.org/v1?key=secret"]:
            with self.subTest(endpoint=endpoint), self.assertRaises((ValidationError, HTTPException)):
                AgentSettings(base_url=endpoint)

    def test_output_contract_enforces_source_and_resource_limits(self):
        request = CollectionRequest(url="https://example.org/articles", goal="titles", max_fields=1)
        capture = {"url": request.url, "evidence": {}}
        CollectionTools.validate_template(TEMPLATE, request, capture)
        for content in [TEMPLATE.replace("https://example.org", "http://127.0.0.1"),
                        TEMPLATE.replace("https://example.org", "https://unobserved.example.com"),
                        TEMPLATE.replace("dedup_fields: [title]", "dedup_fields: [missing]"),
                        TEMPLATE.replace("[{name: title, selector: h1}]", "[{name: title, selector: h1}, {name: title, selector: h2}]"),
                        TEMPLATE + "command: shell\n", TEMPLATE.replace("base_url: https://example.org", "base_url: relative"),
                        TEMPLATE + "list_pagination: {type: page_number, max_pages: 99}\n"]:
            with self.subTest(content=content), self.assertRaises((ValueError, HTTPException)):
                CollectionTools.validate_template(content, request, capture)


class ModelTests(unittest.IsolatedAsyncioTestCase):
    def transport(self, handler):
        original = httpx.AsyncClient
        patcher = patch("agent.model.httpx.AsyncClient", side_effect=lambda **kwargs: original(
            transport=httpx.MockTransport(handler), **kwargs))
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def answer(text="ready: true"):
        return httpx.Response(200, json={"status": "completed", "output": [
            {"type": "reasoning", "summary": [{"text": "not exposed"}]},
            {"type": "message", "content": [{"type": "output_text", "text": text}]},
        ], "usage": {"input_tokens": 10, "output_tokens": 5}})

    async def test_responses_uses_configured_model_high_effort_and_no_store(self):
        requests = []
        def handler(request):
            requests.append(request)
            return self.answer()
        self.transport(handler)
        model = CollectionModel(AgentSettings(max_output_tokens=2048), "secret")
        self.assertEqual(await model.generate("page evidence", max_tokens=8192), "ready: true")
        body = json.loads(requests[0].content)
        self.assertEqual(requests[0].url.path, "/v1/responses")
        self.assertEqual(body["model"], "gpt-6-sol")
        self.assertEqual(body["reasoning"], {"effort": "high"})
        self.assertFalse(body["store"])
        self.assertEqual(body["max_output_tokens"], 2048)
        self.assertIn("untrusted evidence", body["instructions"])
        self.assertNotIn("tools", body)
        self.assertEqual(model.usage, {"input_tokens": 10, "output_tokens": 5})

    async def test_transient_status_retries_once(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(429) if len(calls) == 1 else self.answer()
        self.transport(handler)
        with patch("agent.model.asyncio.sleep", new=AsyncMock()):
            self.assertEqual(await CollectionModel(AgentSettings(), "key").generate("test"), "ready: true")
        self.assertEqual(len(calls), 2)

    async def test_auth_failure_does_not_retry_or_expose_provider_body(self):
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(401, text="private-provider-message")
        self.transport(handler)
        with self.assertRaisesRegex(ModelFailure, "MODEL_HTTP_401") as failure:
            await CollectionModel(AgentSettings(), "key").generate("test")
        self.assertNotIn("private-provider-message", str(failure.exception))
        self.assertEqual(len(calls), 1)

    async def test_ambiguous_timeout_is_not_replayed(self):
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout("timeout", request=request)
        self.transport(handler)
        with self.assertRaisesRegex(ModelFailure, "MODEL_TIMEOUT"):
            await CollectionModel(AgentSettings(), "key").generate("test")
        self.assertEqual(len(calls), 1)

    async def test_refusal_partial_and_malformed_outputs_fail(self):
        replies = [httpx.Response(200, json={"status": "incomplete"}),
                   httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}),
                   httpx.Response(200, text="not JSON"), self.answer("x" * 65537)]
        self.transport(lambda _: replies.pop(0))
        for _ in range(4):
            with self.assertRaises(ModelFailure):
                await CollectionModel(AgentSettings(), "key").generate("test")

    async def test_call_budget_is_enforced_before_network(self):
        model = CollectionModel(AgentSettings(), "key")
        model.calls = 4
        with self.assertRaisesRegex(ModelFailure, "MODEL_CALL_BUDGET"):
            await model.generate("test")

    async def test_oversized_response_envelope_is_rejected_before_decoding(self):
        self.transport(lambda _: httpx.Response(200, content=b"x" * 524289))
        with self.assertRaisesRegex(ModelFailure, "MODEL_RESPONSE_LIMIT"):
            await CollectionModel(AgentSettings(), "key").generate("test")

    async def test_total_deadline_cancels_slow_response_body(self):
        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"{"
                await asyncio.Event().wait()
        self.transport(lambda _: httpx.Response(200, stream=SlowBody()))
        original = asyncio.wait_for
        with patch("agent.model.asyncio.wait_for", new=lambda task, timeout: original(task, timeout=0.01)), self.assertRaisesRegex(ModelFailure, "MODEL_TIMEOUT"):
            await CollectionModel(AgentSettings(), "key").generate("test")


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setup_run(self, stage=0):
        run = {"id": uuid4(), "lease_token": uuid4(), "stage": stage,
               "model_config": {"revision": 1},
               "request": {"url": "https://example.org", "goal": "titles"},
               "checkpoints": {"capture": {"evidence": True}} if stage else {}}
        store = SimpleNamespace(runtime_settings=AsyncMock(return_value=(AgentSettings(enabled=True), "key")),
            progress=AsyncMock(), heartbeat=AsyncMock(return_value=True), finish=AsyncMock(return_value=True))
        async def checkpoint(current, name, value):
            current["checkpoints"][name] = value
            current["stage"] += 1
        store.checkpoint = AsyncMock(side_effect=checkpoint)
        tools = SimpleNamespace(timeout=lambda _: 5, **{name: AsyncMock(return_value={"stage": name}) for name in ("capture", "template", "adapter", "validate")})
        return run, store, tools

    async def test_resume_skips_committed_capture_and_completes_after_validation(self):
        run, store, tools = self.setup_run(stage=1)
        await CollectionHarness(store, lambda _: tools).process(run)
        tools.capture.assert_not_called()
        self.assertEqual([call.args[1] for call in store.checkpoint.await_args_list], ["template", "adapter", "validate"])
        store.finish.assert_awaited_once_with(run, "completed", result={"stage": "validate"})

    async def test_failure_does_not_fabricate_success_or_advance_checkpoint(self):
        run, store, tools = self.setup_run()
        tools.capture.side_effect = ModelFailure("MODEL_HTTP_401")
        await CollectionHarness(store, lambda _: tools).process(run)
        store.checkpoint.assert_not_called()
        store.finish.assert_awaited_once_with(run, "failed", error="MODEL_HTTP_401")

    async def test_lost_lease_cancels_active_tool_without_success(self):
        run, store, tools = self.setup_run()
        started = asyncio.Event()
        cancelled = asyncio.Event()
        async def capture(*_args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        tools.capture.side_effect = capture
        harness = CollectionHarness(store, lambda _: tools)
        async def guard(_):
            await started.wait()
            raise LeaseLost()
        with patch.object(harness, "guard", side_effect=guard):
            await harness.process(run)
        self.assertTrue(cancelled.is_set())
        store.finish.assert_not_called()

    async def test_shutdown_returns_job_to_queue_without_clearing_checkpoint(self):
        run, store, tools = self.setup_run(stage=1)
        started = asyncio.Event()
        async def template(*_args):
            started.set()
            await asyncio.Event().wait()
        tools.template.side_effect = template
        task = asyncio.create_task(CollectionHarness(store, lambda _: tools).process(run))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        store.finish.assert_awaited_once_with(run, "queued")
        self.assertEqual(run["checkpoints"], {"capture": {"evidence": True}})

    async def test_shutdown_storage_failure_preserves_cancellation(self):
        run, store, tools = self.setup_run(stage=1)
        started = asyncio.Event()
        async def template(*_args):
            started.set()
            await asyncio.Event().wait()
        tools.template.side_effect = template
        store.finish.side_effect = RuntimeError("database unavailable")
        task = asyncio.create_task(CollectionHarness(store, lambda _: tools).process(run))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
