import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai import gateway
from app.config.ai_settings import LLMProvider, ai_settings
from app.web.api.dependencies.common import get_current_user
from app.web.api.routes import etl
from app.web.services import layer_script_chat as service


class LayerScriptChatTests(unittest.TestCase):
    def setUp(self):
        patches = ExitStack()
        self.addCleanup(patches.close)
        app = FastAPI()
        app.include_router(etl.router)
        self.actor = SimpleNamespace(is_superuser=True)
        app.dependency_overrides[get_current_user] = lambda: self.actor
        self.client = TestClient(app)
        self.path = "/etl/handlers/ts_ods/example/chat"
        self.code = "def transform(row):\n    return row\n"
        self.body = {
            "code": self.code,
            "messages": [{"role": "user", "content": "Explain the handler"}],
        }
        self.catalog = AsyncMock()
        self.catalog.columns.return_value = [
            {"name": "title", "type": "text", "comment": "not sent"}
        ]
        patches.enter_context(patch.object(etl, "get_etl_layers", return_value=self.catalog))
        patches.enter_context(
            patch.multiple(
                ai_settings,
                ai_enabled=True,
                llm_provider=LLMProvider.CUSTOM,
                custom_base_url="https://model.test/v1/",
                custom_api_key="fixture",
                helio_model="Helio-AI-Test",
                scout_model="Scout-AI-Test",
            )
        )

    def test_followup_sends_current_context_and_returns_a_validated_candidate(self):
        self.body["messages"] += [
            {"role": "assistant", "content": "It returns the row."},
            {"role": "user", "content": "Return its title."},
        ]

        self.assertEqual(
            self.client.get("/etl/handlers/chat-model").json(), {"model": "Helio-AI-Test"}
        )

        def model(request):
            self.assertEqual(str(request.url), "https://model.test/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer fixture")
            body = json.loads(request.content)
            self.assertEqual(body["model"], "Helio-AI-Test")
            messages = body["messages"]
            self.assertEqual(messages[1:-1], self.body["messages"][:-1])
            self.assertEqual(
                json.loads(messages[-1]["content"]),
                {
                    "layer": "ts_ods",
                    "table": "example",
                    "columns": [{"name": "title", "type": "text"}],
                    "script": self.code,
                    "request": "Return its title.",
                },
            )
            answer = {
                "reply": "Return the title.",
                "edits": [{"before": "return row", "after": "return row['title']"}],
            }
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(answer)}}
                    ]
                },
            )

        with httpx_mock(model):
            result = self.client.post(self.path, json=self.body)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(
            result.json(),
            {
                "model": "Helio-AI-Test",
                "reply": "Return the title.",
                "code": self.code.replace("return row", "return row['title']"),
            },
        )
        self.catalog.columns.assert_awaited_once_with("ts_ods", "example")

    def test_invalid_role_and_reader_are_rejected_before_model_access(self):
        result = self.client.post(
            self.path, json={**self.body, "messages": [{"role": "system", "content": "x"}]}
        )
        self.assertEqual(result.status_code, 422)
        self.actor.is_superuser = False
        self.assertEqual(self.client.post(self.path, json=self.body).status_code, 403)
        self.catalog.columns.assert_not_called()

    def test_disabled_model_and_invalid_output_are_explicit(self):
        ai_settings.ai_enabled = False
        self.assertEqual(self.client.post(self.path, json=self.body).status_code, 503)
        ai_settings.ai_enabled = True
        with httpx_mock(lambda _: httpx.Response(200, json={"choices": []})):
            self.assertEqual(self.client.post(self.path, json=self.body).status_code, 502)

    def test_analysis_empty_editor_and_ambiguous_or_invalid_replacements(self):
        def review(code, edits):
            return service._review(json.dumps({"reply": "Review", "edits": edits}), code)["code"]

        self.assertIsNone(review(self.code, []))
        self.assertEqual(review("", [{"before": "", "after": "pass"}]), "pass")
        for before, after in [("row", "title"), ("missing", ""), ("return row", "return (")]:
            with self.subTest(before=before), self.assertRaises(ValueError):
                review(self.code, [{"before": before, "after": after}])
        with self.assertRaisesRegex(ValueError, "overlap"):
            review(
                self.code,
                [{"before": "return row", "after": "pass"}, {"before": "return", "after": "pass"}],
            )


def httpx_mock(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return patch.object(gateway.httpx, "AsyncClient", return_value=client)
