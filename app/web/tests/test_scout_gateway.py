import asyncio
import json
import time
import threading
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.ai import gateway
from app.downloader.proxy_tunnel import ProxyTunnel
from app.web.utils.validation import validate_target_url
from app.config.ai_settings import LLMProvider, ai_settings
from app.web.api.routes import ai_collect, task_socket
from app.web.services.browser_renderer import BrowserRenderResult


class ScoutGatewayTest(unittest.IsolatedAsyncioTestCase):
    async def test_refinement_and_adapter_stream_the_configured_scout_model(self):
        patches = ExitStack()
        self.addCleanup(patches.close)
        url = "https://example.org/articles"
        template = (
            "name: articles\ndisplay_name: Articles\nbase_url: https://example.org\n"
            "data_type: article\nlist_page: /articles\nadapter: articles\n"
            "list_fields: [{name: title, selector: h1}]\ndedup_fields: [title]\n"
        )
        adapter = 'class Articles:\n    def title(self, record):\n        return record["title"]'
        requests = []

        def respond(req):
            body = json.loads(req.content)
            requests.append(body)
            output = template if len(requests) == 1 else adapter
            encoded = json.dumps({"choices": [{"delta": {"content": output}, "finish_reason": "stop"}]})
            return httpx.Response(200, text=f"data: {encoded}\n\ndata: [DONE]\n\n")

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        # Each generation owns its gateway client, so construct a fresh one per call.
        patches.enter_context(patch.object(gateway.httpx, "AsyncClient", side_effect=[
            client, type(client)(transport=httpx.MockTransport(respond)),
        ]))
        for name, value in {
            "llm_provider": LLMProvider.CUSTOM, "custom_base_url": "https://fixture.invalid/v1",
            "scout_model": "Scout-AI-Regression", "ai_enabled": True,
        }.items():
            patches.enter_context(patch.object(ai_settings, name, value))
        patches.enter_context(patch.object(ai_collect.browser_renderer, "render", AsyncMock(
            return_value=BrowserRenderResult(url, "Articles", "<h1>Title</h1>"),
        )))
        patches.enter_context(patch.object(ai_collect.ai_collect_store, "list_template_definitions", AsyncMock(return_value=[])))
        save = patches.enter_context(patch.object(ai_collect.ai_collect_store, "save_analysis", AsyncMock()))
        send = AsyncMock()

        await task_socket._run_scout({
            "request_id": "first", "type": "start_analyze", "url": url,
            "prompt": "Keep the existing category", "current_template_yaml": "name: previous",
        }, send)
        events = [call.args[0] for call in send.await_args_list]
        self.assertEqual(events[-1]["type"], "analyze_complete", events[-1])
        self.assertTrue(any(e["type"] == "analyze_model" and e["data"]["kind"] == "template_delta" for e in events))
        self.assertTrue(events[-1]["data"]["agent"]["decision"]["requires_adapter"])
        saved = save.await_args.args[0]
        patches.enter_context(patch.object(ai_collect.ai_collect_store, "get_analysis", AsyncMock(return_value=saved)))
        send.reset_mock()
        await task_socket._run_scout({
            "request_id": "second", "type": "generate_adapter", "url": url,
            "template_id": saved["template_id"], "prompt": "Keep titles", "current_adapter_code": "# old class",
        }, send)
        self.assertEqual([call.args[0]["type"] for call in send.await_args_list],
                         ["adapter_started", "adapter_delta", "adapter_ready"])
        self.assertEqual(send.await_args.args[0]["data"]["code"], adapter)
        self.assertEqual([req["model"] for req in requests], ["Scout-AI-Regression"] * 2)
        self.assertTrue(all(req["stream"] for req in requests))
        self.assertIn("name: previous", requests[0]["messages"][0]["content"])
        self.assertIn("Keep the existing category", requests[0]["messages"][0]["content"])
        self.assertIn("# old class", requests[1]["messages"][0]["content"])

    async def test_gateway_failure_is_reported_without_persisting_a_result(self):
        send = AsyncMock()
        error = HTTPException(502, "Gateway rejected Scout-AI-Collect")
        with patch.object(ai_collect.ai_collect_store, "get_analysis", AsyncMock(return_value={"template_yaml": ""})), \
             patch.object(ai_collect.adapter_agent, "generate_adapter", AsyncMock(side_effect=error)), \
             patch.object(ai_collect.ai_collect_store, "save_analysis", AsyncMock()) as save:
            await task_socket._run_scout({
                "request_id": "fail", "type": "generate_adapter", "template_id": "articles",
                "url": "https://example.org",
            }, send)
        self.assertEqual(send.await_args.args[0], {
            "request_id": "fail", "type": "adapter_error", "data": {"message": error.detail},
        })
        save.assert_not_awaited()

    def test_renderer_tunnel_rechecks_destinations_before_connecting(self):
        tunnel = ProxyTunnel("http://proxy.example:8080", None, timeout=5,
                             validate_destination=validate_target_url)
        with patch("app.downloader.proxy_tunnel.Curl") as curl:
            with self.assertRaises(HTTPException):
                tunnel._connect("192.168.1.2", 80, threading.Event())
            curl.return_value.perform.assert_not_called()

    async def test_invalid_target_stops_before_browser_or_gateway(self):
        send = AsyncMock()
        with patch.object(ai_collect.browser_renderer, "render", AsyncMock()) as render:
            await task_socket._run_scout({
                "request_id": "test", "type": "start_analyze", "url": "http://127.0.0.1/",
            }, send)
        self.assertEqual(send.await_args.args[0]["type"], "analyze_error")
        render.assert_not_awaited()


class ScoutSocketTest(unittest.TestCase):
    def test_authenticated_socket_cancels_analysis_and_still_accepts_subscriptions(self):
        app = FastAPI()
        app.include_router(task_socket.router)
        client = TestClient(app)
        async def pending(*args):
            await asyncio.Event().wait()

        with patch.object(task_socket, "decode_session_token", return_value={"exp": time.time() + 60}), \
             patch.object(task_socket, "get_current_user", AsyncMock()), \
             patch.object(task_socket, "build_template", side_effect=pending), \
             client.websocket_connect("/ws", subprotocols=["bearer", "fixture"]) as ws:
            self.assertEqual(ws.receive_json()["type"], "task_stream_status")
            ws.send_json({"type": "start_analyze", "request_id": "run", "url": "https://example.org"})
            self.assertEqual(ws.receive_json()["type"], "analyze_started")
            ws.send_json({"type": "cancel_analyze", "request_id": "run"})
            self.assertEqual(ws.receive_json()["type"], "analyze_cancelled")
            ws.send_json({"type": "unsubscribe", "channel": "tasks"})
            self.assertEqual(ws.receive_json()["type"], "unsubscribed")
