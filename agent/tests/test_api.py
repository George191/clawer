import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI

from agent import api
from agent.contracts import SettingsView
from app.web.api.dependencies.common import get_current_user


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.user = SimpleNamespace(id=uuid4(), is_superuser=True)
        self.app = FastAPI()
        self.app.include_router(api.router)
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.store = SimpleNamespace(settings=AsyncMock(return_value=SettingsView()),
                                     enqueue=AsyncMock(return_value={"status": "queued"}),
                                     get=AsyncMock(return_value={"status": "running"}),
                                     events=AsyncMock(return_value=[]), control=AsyncMock(return_value={"status": "cancelled"}))
        self.store_patch = patch.object(api, "store", self.store)
        self.store_patch.start()

    async def asyncTearDown(self):
        self.store_patch.stop()
        await self.client.aclose()

    async def test_settings_require_admin(self):
        self.user.is_superuser = False
        for method, path in [("GET", "/settings"), ("POST", "/settings/test")]:
            response = await self.client.request(method, "/agents/collection" + path)
            self.assertEqual(response.status_code, 403)
        self.store.settings.assert_not_called()

    async def test_missing_auth_is_rejected(self):
        self.app.dependency_overrides.clear()
        response = await self.client.get("/agents/collection/settings")
        self.assertEqual(response.status_code, 401)

    async def test_settings_read_never_contains_api_key_field(self):
        response = await self.client.get("/agents/collection/settings")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("api_key", response.json())
        self.assertNotIn("api_key_ciphertext", response.json())
        self.assertEqual(response.json()["model"], "gpt-6-sol")

    async def test_request_requires_idempotency_and_forbids_general_tools(self):
        path = "/agents/collection/runs"
        payload = {"url": "https://example.org", "goal": "titles"}
        self.assertEqual((await self.client.post(path, json=payload)).status_code, 422)
        self.assertEqual((await self.client.post(path, headers={"Idempotency-Key": "one"}, json={**payload, "command": "shell"})).status_code, 422)
        self.store.enqueue.assert_not_called()
        response = await self.client.post(path, headers={"Idempotency-Key": "one"}, json=payload)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.store.enqueue.await_args.args[:2], (self.user.id, "one"))

    async def test_owner_and_event_cursor_are_forwarded_by_server(self):
        run_id = uuid4()
        response = await self.client.get(f"/agents/collection/runs/{run_id}/events?after=12&limit=20")
        self.assertEqual(response.status_code, 200)
        self.store.events.assert_awaited_once_with(self.user.id, run_id, 12, 20)
        self.assertEqual((await self.client.get(f"/agents/collection/runs/{run_id}/events?limit=10000")).status_code, 422)
