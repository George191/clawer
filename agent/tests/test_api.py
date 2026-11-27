import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI

from agent import api, management
from agent.contracts import SettingsView, SYSTEM_COLLECTION_AGENT_ID
from app.web.api.dependencies.common import get_current_user


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.user = SimpleNamespace(id=uuid4(), is_superuser=True)
        self.app = FastAPI()
        self.app.include_router(api.router)
        self.app.include_router(management.router)
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.store = SimpleNamespace(settings=AsyncMock(return_value=SettingsView()),
                                     list=AsyncMock(return_value=[]),
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
            response = await self.client.request(method, "/agents" + path)
            self.assertEqual(response.status_code, 403)
        self.store.settings.assert_not_called()

    async def test_missing_auth_is_rejected(self):
        self.app.dependency_overrides.clear()
        response = await self.client.get("/agents/settings")
        self.assertEqual(response.status_code, 401)

    async def test_settings_read_never_contains_api_key_field(self):
        response = await self.client.get("/agents/settings")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("api_key", response.json())
        self.assertNotIn("api_key_ciphertext", response.json())
        self.assertEqual(response.json()["model"], "gpt-6-sol")

    async def test_request_requires_idempotency_and_forbids_general_tools(self):
        path = "/agents/runs"
        payload = {"agent_id": str(SYSTEM_COLLECTION_AGENT_ID), "product_domain": "ontology", "input": {"url": "https://example.org", "goal": "titles"}}
        self.assertEqual((await self.client.post(path, json=payload)).status_code, 422)
        self.assertEqual((await self.client.post(path, headers={"Idempotency-Key": "one"}, json={**payload, "command": "shell"})).status_code, 422)
        self.store.enqueue.assert_not_called()
        response = await self.client.post(path, headers={"Idempotency-Key": "one"}, json=payload)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.store.enqueue.await_args.args[:2], (self.user.id, "one"))

    async def test_owner_and_event_cursor_are_forwarded_by_server(self):
        run_id = uuid4()
        response = await self.client.get(f"/agents/runs/{run_id}/events?after=12&limit=20")
        self.assertEqual(response.status_code, 200)
        self.store.events.assert_awaited_once_with(self.user.id, run_id, 12, 20)
        self.assertEqual((await self.client.get(f"/agents/runs/{run_id}/events?limit=10000")).status_code, 422)

    async def test_agent_activity_filter_keeps_authenticated_owner(self):
        agent_id = uuid4()
        response = await self.client.get(f"/agents/runs?agent_id={agent_id}&limit=25")
        self.assertEqual(response.status_code, 200)
        self.store.list.assert_awaited_once_with(self.user.id, None, 25, agent_id)
        self.assertEqual(response.json(), {"items": []})
        self.assertEqual((await self.client.get("/agents/runs?agent_id=invalid")).status_code, 422)

    async def test_builder_returns_reviewable_draft_without_saving(self):
        agent_id = uuid4()
        draft = {"name": "Research", "description": "Research assistant", "instructions": "Summarize the supplied context."}
        from json import dumps
        with patch.object(management.registry, "resolve", AsyncMock(return_value=(SettingsView(), "test-key", {"agent_revision": 2, "instructions": "Old prompt"}))) as resolve, \
             patch.object(management.registry, "save_definition", AsyncMock()) as save, \
             patch.object(management.AgentModel, "generate", AsyncMock(return_value=dumps(draft))):
            response = await self.client.post(f"/agents/definitions/{agent_id}/build", json={"message": "Help with research", "expected_revision": 2})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), draft)
            resolve.assert_awaited_once_with(self.user.id, agent_id, require_enabled=False, require_owner=True)
            save.assert_not_called()

    async def test_builder_rejects_stale_revision_and_invalid_output(self):
        agent_id = uuid4()
        with patch.object(management.registry, "resolve", AsyncMock(return_value=(SettingsView(), "test-key", {"agent_revision": 2, "instructions": "Prompt"}))), \
             patch.object(management.AgentModel, "generate", AsyncMock(return_value="not JSON")) as generate:
            response = await self.client.post(f"/agents/definitions/{agent_id}/build", json={"message": "Research", "expected_revision": 1})
            self.assertEqual(response.status_code, 409)
            generate.assert_not_called()
            response = await self.client.post(f"/agents/definitions/{agent_id}/build", json={"message": "Research", "expected_revision": 2})
            self.assertEqual(response.status_code, 502)

    async def test_catalog_uses_existing_definition_timestamp_column(self):
        from datetime import datetime, timezone
        from agent.registry import AgentRegistry
        updated = datetime.now(timezone.utc)
        row = {"config": {"name": "Agent"}, "owner_id": self.user.id, "id": uuid4(),
               "connection_id": uuid4(), "revision": 1, "tested_revision": 1,
               "tested_connection_revision": 1, "connection_revision": 1, "visibility": "private",
               "shared_with": [], "system_key": None, "updated_at": updated}
        results = [Mock(), Mock(), Mock()]
        results[0].mappings.return_value.all.return_value = []
        results[1].mappings.return_value.all.return_value = [row]
        results[2].mappings.return_value.one.return_value = {"config": {}, "revision": 1, "tested_revision": 1}
        connection = SimpleNamespace(execute=AsyncMock(side_effect=results))
        context = AsyncMock()
        context.__aenter__.return_value = connection
        catalog = await AgentRegistry(SimpleNamespace(connect=lambda: context)).list(self.user.id)
        self.assertEqual(catalog["agents"][0]["updated_at"], updated)
        self.assertNotIn("created_at", catalog["agents"][0])
