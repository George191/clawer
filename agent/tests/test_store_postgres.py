"""Real SQL concurrency tests. Only an explicitly named disposable database is accepted."""

import asyncio
import importlib
import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from agent.contracts import CollectionRequest, LeaseLost, RunConflict, SettingsUpdate
from agent.runtime import CollectionHarness
from agent.store import CollectionStore
from agent.tests.test_harness import TEMPLATE
from app.storage.connection_credentials import connection_cipher
from app.web.services.browser_renderer import BrowserRenderResult

TEST_URL = os.getenv("AGENT_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_URL, "Set AGENT_TEST_DATABASE_URL to a disposable collection_agent_test database")
class PostgresStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if make_url(TEST_URL).database != "collection_agent_test":
            raise RuntimeError("Integration tests refuse any database other than collection_agent_test")
        self.database = create_async_engine(TEST_URL)
        self.store = CollectionStore(self.database)
        self.owner, self.other = uuid4(), uuid4()
        self.request = CollectionRequest(url="https://example.org/articles", goal="Collect titles")
        self.key_patch = patch("agent.store.cipher", side_effect=lambda: connection_cipher("isolated-test-encryption-secret"))
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)
        migration = importlib.import_module("app.web.alembic.versions.c801b426df90_collection_agent_harness")
        async with self.database.begin() as conn:
            # The database name check above is mandatory before resetting this test-only schema.
            await conn.execute(text("DROP SCHEMA IF EXISTS app CASCADE"))
            await conn.execute(text("CREATE SCHEMA app"))
            await conn.execute(text('CREATE TABLE app."user" (id uuid PRIMARY KEY)'))
            def upgrade(sync):
                with Operations.context(MigrationContext.configure(sync)):
                    migration.upgrade()
            await conn.run_sync(upgrade)
            await conn.execute(text('INSERT INTO app."user" VALUES (:owner),(:other)'), {"owner": self.owner, "other": self.other})
        view = await self.store.save_settings(SettingsUpdate(expected_revision=0, api_key="isolated-model-key"), self.owner)
        await self.store.mark_verified(view.revision)
        view = await self.store.save_settings(SettingsUpdate(expected_revision=view.revision, enabled=True), self.owner)
        self.revision = view.revision

    async def asyncTearDown(self):
        await self.database.dispose()

    async def enqueue(self, key="one", owner=None):
        return await self.store.enqueue(owner or self.owner, key, self.request)

    async def expire(self, run):
        async with self.database.begin() as conn:
            await conn.execute(text("UPDATE app.collection_agent_runs SET lease_until=now()-interval '1 second' WHERE id=:id"), {"id": run["id"]})

    async def test_credentials_are_encrypted_masked_and_survive_store_recreation(self):
        view = await self.store.settings()
        self.assertNotIn("isolated-model-key", view.model_dump_json())
        async with self.database.connect() as conn:
            encrypted = (await conn.execute(text("SELECT api_key_ciphertext FROM app.collection_agent_settings"))).scalar_one()
        self.assertNotEqual(encrypted, "isolated-model-key")
        config, key = await CollectionStore(self.database).runtime_settings(view.revision)
        self.assertEqual(key, "isolated-model-key")
        self.assertEqual((config.model, config.reasoning_effort), ("gpt-6-sol", "high"))

    async def test_optimistic_settings_and_connection_test_revision(self):
        with self.assertRaises(RunConflict):
            await self.store.save_settings(SettingsUpdate(expected_revision=0), self.owner)
        with self.assertRaises(RunConflict):
            await self.store.mark_verified(self.revision - 1)
        with self.assertRaises(RunConflict):
            await self.store.save_settings(SettingsUpdate(expected_revision=self.revision, enabled=True, model="gpt-6.2-sol"), self.owner)
        changed = await self.store.save_settings(SettingsUpdate(expected_revision=self.revision, model="gpt-6.2-sol"), self.owner)
        self.assertFalse(changed.connection_verified)
        self.assertTrue(changed.api_key_configured)
        with self.assertRaises(RunConflict):
            await self.store.save_settings(SettingsUpdate(expected_revision=changed.revision, enabled=True, model="gpt-6.2-sol"), self.owner)

    async def test_clear_key_disables_and_prevents_new_collection(self):
        cleared = await self.store.save_settings(SettingsUpdate(expected_revision=self.revision, clear_api_key=True), self.owner)
        self.assertFalse(cleared.api_key_configured)
        self.assertFalse(cleared.connection_verified)
        with self.assertRaises(HTTPException) as failure:
            await self.enqueue()
        self.assertEqual(failure.exception.status_code, 503)

    async def test_concurrent_idempotency_commits_one_run_and_event(self):
        results = await asyncio.gather(*(self.enqueue() for _ in range(12)))
        self.assertEqual(len({result["id"] for result in results}), 1)
        events = await self.store.events(self.owner, results[0]["id"], 0, 100)
        self.assertEqual([event["seq"] for event in events], [1])
        with self.assertRaises(RunConflict):
            await self.store.enqueue(self.owner, "one", self.request.model_copy(update={"goal": "Different request"}))

    async def test_owner_isolation_and_same_key_is_independent_per_owner(self):
        first = await self.enqueue()
        second = await self.enqueue(owner=self.other)
        self.assertNotEqual(first["id"], second["id"])
        for action in [self.store.get(self.other, first["id"]), self.store.events(self.other, first["id"], 0, 10),
                       self.store.control(self.other, first["id"], "cancel"), self.store.control(self.other, first["id"], "resume")]:
            with self.assertRaises(HTTPException) as failure:
                await action
            self.assertEqual(failure.exception.status_code, 404)

    async def test_queue_quota_and_cancel_are_idempotent(self):
        first = await self.enqueue("one")
        await self.enqueue("two")
        await self.enqueue("three")
        with self.assertRaises(HTTPException) as failure:
            await self.enqueue("four")
        self.assertEqual(failure.exception.status_code, 429)
        cancelled = await self.store.control(self.owner, first["id"], "cancel")
        repeated = await self.store.control(self.owner, first["id"], "cancel")
        self.assertEqual(cancelled["event_seq"], repeated["event_seq"])
        self.assertEqual((await self.enqueue("four"))["status"], "queued")

    async def test_concurrent_claims_are_exclusive_and_global_limit_is_two(self):
        await self.enqueue("one")
        await self.enqueue("two")
        await self.enqueue("three")
        claims = await asyncio.gather(*(self.store.claim() for _ in range(10)))
        ids = [run["id"] for run in claims if run]
        # A losing advisory try-lock intentionally returns no claim; retry acquires remaining capacity.
        while len(ids) < 2:
            run = await self.store.claim()
            if run is None:
                break
            ids.append(run["id"])
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)
        self.assertIsNone(await self.store.claim())

    async def test_expired_worker_cannot_heartbeat_checkpoint_or_complete(self):
        await self.enqueue()
        old = await self.store.claim()
        await self.expire(old)
        self.assertFalse(await self.store.heartbeat(old["id"], old["lease_token"]))
        fresh = await self.store.claim()
        self.assertNotEqual(old["lease_token"], fresh["lease_token"])
        with self.assertRaises(LeaseLost):
            await self.store.checkpoint(old, "capture", {"stale": True})
        with self.assertRaises(LeaseLost):
            await self.store.progress(old, "capture", "Searching")
        self.assertFalse(await self.store.finish(old, "completed", result={"stale": True}))
        await self.store.checkpoint(fresh, "capture", {"fresh": True})
        self.assertEqual((await self.store.get(self.owner, fresh["id"]))["stage"], 1)

    async def test_committed_checkpoint_survives_reclaim(self):
        await self.enqueue()
        first = await self.store.claim()
        await self.store.checkpoint(first, "capture", {"url": "https://example.org", "evidence": "durable"})
        await self.expire(first)
        recovered = await CollectionStore(self.database).claim()
        self.assertEqual(recovered["stage"], 1)
        self.assertEqual(recovered["checkpoints"]["capture"]["evidence"], "durable")

    async def test_cancellation_fences_inflight_results_and_events_are_replayable(self):
        submitted = await self.enqueue()
        running = await self.store.claim()
        await self.store.control(self.owner, submitted["id"], "cancel")
        self.assertFalse(await self.store.heartbeat(running["id"], running["lease_token"]))
        with self.assertRaises(LeaseLost):
            await self.store.checkpoint(running, "capture", {"late": True})
        self.assertFalse(await self.store.finish(running, "completed", result={"late": True}))
        events = await self.store.events(self.owner, submitted["id"], 0, 100)
        self.assertEqual([event["seq"] for event in events], list(range(1, len(events) + 1)))
        self.assertEqual(len(await self.store.events(self.owner, submitted["id"], 1, 100)), len(events) - 1)
        current = await self.store.get(self.owner, submitted["id"])
        self.assertEqual(current["status"], "cancelled")
        self.assertIsNone(current["result"])

    async def test_checkpoint_and_event_roll_back_together(self):
        await self.enqueue()
        running = await self.store.claim()
        with patch.object(self.store, "event", new=AsyncMock(side_effect=RuntimeError("event storage failed"))), self.assertRaises(RuntimeError):
            await self.store.checkpoint(running, "capture", {"not_committed": True})
        current = await self.store.get(self.owner, running["id"])
        self.assertEqual(current["stage"], 0)
        self.assertEqual(running["checkpoints"], {})

    async def test_crash_attempt_budget_is_not_reset(self):
        submitted = await self.enqueue()
        for attempt in range(1, 4):
            run = await self.store.claim()
            self.assertEqual(run["attempts"], attempt)
            await self.expire(run)
        self.assertIsNone(await self.store.claim())
        current = await self.store.get(self.owner, submitted["id"])
        self.assertEqual((current["status"], current["error_code"]), ("failed", "ATTEMPTS_EXHAUSTED"))
        with self.assertRaises(RunConflict):
            await self.store.control(self.owner, submitted["id"], "resume")

    async def test_full_harness_commits_real_contract_and_review_result(self):
        submitted = await self.enqueue()
        run = await self.store.claim()
        with patch("agent.tools.browser_renderer.render", new=AsyncMock(return_value=BrowserRenderResult(
            "https://example.org/articles", "Articles", "<html><title>Articles</title><article><h1>First article</h1></article></html>"
        ))), patch("agent.model.CollectionModel.generate", new=AsyncMock(return_value=TEMPLATE)):
            await CollectionHarness(self.store).process(run)
        current = await self.store.get(self.owner, submitted["id"])
        self.assertEqual((current["status"], current["stage"]), ("completed", 4))
        self.assertTrue(current["result"]["requires_review"])
        self.assertFalse(current["result"]["published"])
        self.assertFalse(current["result"]["validation"]["adapter_executed"])
        events = await self.store.events(self.owner, submitted["id"], 0, 100)
        self.assertEqual([event["event"]["stage"] for event in events if event["event"]["type"] == "stage_completed"],
                         ["capture", "template", "adapter", "validate"])
