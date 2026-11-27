"""Real SQL concurrency tests. Only an explicitly named disposable database is accepted."""

import asyncio
import importlib
import hashlib
import json
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

from agent.contracts import AgentRunRequest, AgentSettings, SYSTEM_COLLECTION_AGENT_ID, LeaseLost, RunConflict, SettingsUpdate
from agent.runtime import AgentWorker
from agent.store import AgentStore
from agent.tests.test_harness import TEMPLATE
from app.storage.connection_credentials import connection_cipher
from app.web.api.models import User
from app.web.services.browser_renderer import BrowserRenderResult

TEST_URL = os.getenv("AGENT_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_URL, "Set AGENT_TEST_DATABASE_URL to a disposable agent_test database")
class PostgresStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if make_url(TEST_URL).database != "agent_test":
            raise RuntimeError("Integration tests refuse any database other than agent_test")
        self.database = create_async_engine(TEST_URL, connect_args={"server_settings": {"search_path": "app,pg_catalog"}})
        self.store = AgentStore(self.database)
        self.owner, self.other = uuid4(), uuid4()
        self.request = AgentRunRequest(agent_id=SYSTEM_COLLECTION_AGENT_ID, product_domain="ai-collect", input={"url": "https://example.org/articles", "goal": "Collect titles"})
        self.key_patch = patch("agent.store.cipher", side_effect=lambda: connection_cipher("isolated-test-encryption-secret"))
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)
        registry_key_patch = patch("agent.registry.cipher", side_effect=lambda: connection_cipher("isolated-test-encryption-secret"))
        registry_key_patch.start()
        self.addCleanup(registry_key_patch.stop)
        migration = importlib.import_module("app.web.alembic.versions.c801b426df90_collection_agent_harness")
        async with self.database.begin() as conn:
            # The database name check above is mandatory before resetting this test-only schema.
            await conn.execute(text("DROP SCHEMA IF EXISTS app CASCADE"))
            await conn.execute(text("CREATE SCHEMA app"))
            await conn.run_sync(lambda sync: User.__table__.c.status.type.create(sync))
            await conn.execute(text("CREATE TABLE app.\"user\" (id uuid PRIMARY KEY, email text UNIQUE, status app.statustypes DEFAULT 'ENABLE')"))
            def upgrade(sync):
                with Operations.context(MigrationContext.configure(sync)):
                    migration.upgrade()
                    importlib.import_module("app.web.alembic.versions.d29c0e317fa2_agent_management").upgrade()
                    importlib.import_module("app.web.alembic.versions.e38a62f9c104_global_agents").upgrade()
            await conn.run_sync(upgrade)
            await conn.execute(text('INSERT INTO app."user" (id,email) VALUES (:owner,:owner_email),(:other,:other_email)'),
                               {"owner": self.owner, "other": self.other, "owner_email": "owner@example.org", "other_email": "other@example.org"})
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
            await conn.execute(text("UPDATE app.agent_runs SET lease_until=now()-interval '1 second' WHERE id=:id"), {"id": run["id"]})

    async def test_credentials_are_encrypted_masked_and_survive_store_recreation(self):
        view = await self.store.settings()
        self.assertNotIn("isolated-model-key", view.model_dump_json())
        async with self.database.connect() as conn:
            encrypted = (await conn.execute(text("SELECT api_key_ciphertext FROM app.agent_settings"))).scalar_one()
        self.assertNotEqual(encrypted, "isolated-model-key")
        config, key = await AgentStore(self.database).runtime_settings(view.revision)
        self.assertEqual(key, "isolated-model-key")
        self.assertEqual((config.model, config.reasoning_effort), ("gpt-6-sol", "high"))

    async def test_managed_agents_share_connection_but_keep_models_and_owners_isolated(self):
        from agent.registry import AgentRegistry, ConnectionUpdate, DefinitionUpdate
        registry = AgentRegistry(self.database)
        connection = (await registry.save_connection(self.owner, ConnectionUpdate(name="Personal", api_key="managed-secret")))["id"]
        first = DefinitionUpdate(name="Articles", harness="collection", connection_id=connection, model="model-one", instructions="Extract article titles.")
        ident = (await registry.save_definition(self.owner, first))["id"]
        await registry.save_definition(self.owner, first.model_copy(update={"name": "Patents", "model": "model-two"}))
        catalog = await registry.list(self.owner)
        catalog["agents"] = [row for row in catalog["agents"] if not row["system_key"]]
        self.assertEqual(len(catalog["connections"]), 1)
        self.assertEqual({row["model"] for row in catalog["agents"]}, {"model-one", "model-two"})
        self.assertNotIn("managed-secret", str(catalog))
        self.assertNotIn("api_key_ciphertext", str(catalog))
        self.assertTrue(all(row["system_key"] for row in (await registry.list(self.other))["agents"]))
        for operation in (registry.resolve(self.other, ident), registry.save_definition(self.other, first), registry.save_connection(self.other, ConnectionUpdate(name="Steal", expected_revision=1), connection)):
            with self.assertRaises(HTTPException) as denied:
                await operation
            self.assertEqual(denied.exception.status_code, 404)
        with self.assertRaises(HTTPException):
            await registry.resolve(self.owner, ident)
        config, key, snapshot = await registry.resolve(self.owner, ident, require_enabled=False)
        self.assertEqual(key, "managed-secret")
        self.assertEqual(config.model, "model-one")
        await registry.mark_verified(self.owner, snapshot)
        await registry.save_definition(self.owner, first.model_copy(update={"enabled": True, "expected_revision": 1}), ident)
        _, _, snapshot = await registry.resolve(self.owner, ident)
        request = self.request.model_copy(update={"agent_id": ident})
        run = await self.store.enqueue(self.owner, "managed", request)
        self.assertEqual(run["model_config"], snapshot)
        self.assertNotIn("managed-secret", str(run))
        claimed = await self.store.claim()
        seen_models = []
        async def generate(model, *_args, **_kwargs):
            seen_models.append((model.config.model, model.instructions, model.api_key))
            return TEMPLATE
        with patch("agent.tools.browser_renderer.render", new=AsyncMock(return_value=BrowserRenderResult(
            "https://example.org/articles", "Articles", "<html><article>Article</article></html>"
        ))), patch("agent.model.AgentModel.generate", new=generate):
            await AgentWorker(self.store).process(claimed)
        self.assertEqual((await self.store.get(self.owner, run["id"]))["status"], "completed")
        self.assertTrue(seen_models)
        self.assertEqual(seen_models[0][0], "model-one")
        self.assertIn("Extract article titles.", seen_models[0][1])
        self.assertEqual(seen_models[0][2], "managed-secret")
        # Updating shared credentials invalidates all dependent verifications.
        await registry.save_connection(self.owner, ConnectionUpdate(name="Personal", api_key="replacement", expected_revision=1), connection)
        with self.assertRaises(HTTPException) as stale:
            await registry.mark_verified(self.owner, snapshot)
        self.assertEqual(stale.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            await registry.resolve(self.owner, ident)
        with self.assertRaises(HTTPException):
            await registry.delete(self.owner, "connections", connection, 2)
        with self.assertRaises(HTTPException):
            await registry.save_definition(self.owner, first, ident)

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

    async def test_system_agent_is_global_and_immutable(self):
        from agent.registry import AgentRegistry
        registry = AgentRegistry(self.database)
        for actor in (self.owner, self.other):
            catalog = await registry.list(actor)
            system = next(row for row in catalog["agents"] if row["system_key"] == "collection")
            self.assertEqual(system["id"], SYSTEM_COLLECTION_AGENT_ID)
            self.assertEqual(system["visibility"], "public")
            self.assertFalse(system["can_edit"])
            self.assertTrue(system["verified"])
            with self.assertRaises(HTTPException):
                await registry.delete(actor, "agents", SYSTEM_COLLECTION_AGENT_ID, system["revision"])
        submitted = await self.store.enqueue(self.other, "ontology", self.request.model_copy(update={"product_domain": "ontology"}))
        self.assertEqual(submitted["product_domain"], "ontology")
        self.assertEqual(len(await self.store.list(self.other, None, 100)), 1)
        self.assertEqual(await self.store.list(self.other, "ai-collect", 100), [])
        self.assertEqual(await self.store.list(self.owner, None, 100), [])

    async def test_upgrade_preserves_historical_runs_events_and_idempotency(self):
        async with self.database.begin() as conn:
            await conn.execute(text("DROP SCHEMA app CASCADE"))
            await conn.execute(text("CREATE SCHEMA app"))
            await conn.run_sync(lambda sync: User.__table__.c.status.type.create(sync))
            await conn.execute(text("CREATE TABLE app.\"user\" (id uuid PRIMARY KEY, email text UNIQUE, status app.statustypes DEFAULT 'ENABLE')"))
            await conn.execute(text('INSERT INTO app."user" (id) VALUES (:id)'), {"id": self.owner})
            def legacy(sync):
                with Operations.context(MigrationContext.configure(sync)):
                    importlib.import_module("app.web.alembic.versions.c801b426df90_collection_agent_harness").upgrade()
                    importlib.import_module("app.web.alembic.versions.d29c0e317fa2_agent_management").upgrade()
            await conn.run_sync(legacy)
            run_id = uuid4()
            request = {"url": "https://example.org", "goal": "Collect titles"}
            snapshot = {**AgentSettings().model_dump(), "revision": 7}
            await conn.execute(text("""INSERT INTO app.collection_agent_runs
                (id,owner_id,idempotency_key,request_hash,request,model_config,status,stage,result,checkpoints)
                VALUES (:id,:owner,'original','original-hash',CAST(:request AS jsonb),CAST(:snapshot AS jsonb),
                    'completed',4,CAST(:result AS jsonb),CAST(:checkpoints AS jsonb))"""),
                {"id": run_id, "owner": self.owner, "request": json.dumps(request), "snapshot": json.dumps(snapshot),
                 "result": json.dumps({"published": False}), "checkpoints": json.dumps({"capture": {"evidence": "saved"}})})
            await conn.execute(text("INSERT INTO app.collection_agent_events(run_id,seq,event) VALUES(:id,1,'{\"type\":\"completed\"}')"), {"id": run_id})
            def upgrade(sync):
                with Operations.context(MigrationContext.configure(sync)):
                    importlib.import_module("app.web.alembic.versions.e38a62f9c104_global_agents").upgrade()
            await conn.run_sync(upgrade)
            row = (await conn.execute(text("SELECT * FROM app.agent_runs WHERE id=:id"), {"id": run_id})).mappings().one()
            self.assertEqual(row["checkpoints"]["capture"]["evidence"], "saved")
            self.assertEqual((row["status"], row["stage"], row["stage_count"]), ("completed", 4, 4))
            self.assertEqual(row["agent_id"], SYSTEM_COLLECTION_AGENT_ID)
            self.assertEqual(row["request"]["input"], request)
            encoded = json.dumps(row["request"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            self.assertEqual(row["request_hash"], hashlib.sha256(encoded.encode()).hexdigest())
            self.assertEqual(row["model_config"]["connection_revision"], 7)
            self.assertEqual((await conn.execute(text("SELECT count(*) FROM app.agent_events WHERE run_id=:id"), {"id": run_id})).scalar_one(), 1)
            self.assertIsNone((await conn.execute(text("SELECT to_regclass('app.collection_agent_runs')"))).scalar_one())

    async def test_private_selected_and_public_access_with_revocation(self):
        from agent.registry import AgentRegistry, ConnectionUpdate, DefinitionUpdate
        registry = AgentRegistry(self.database)
        outsider = uuid4()
        async with self.database.begin() as conn:
            await conn.execute(text('INSERT INTO app."user" (id,email) VALUES (:id,\'outsider@example.org\')'), {"id": outsider})
        connection = (await registry.save_connection(self.owner, ConnectionUpdate(name="Private key", api_key="owner-secret")))["id"]
        payload = DefinitionUpdate(name="Reusable", harness="collection", connection_id=connection, model="gpt-6-sol")
        ident = (await registry.save_definition(self.owner, payload))["id"]
        for actor in (self.other, outsider):
            with self.assertRaises(HTTPException):
                await registry.resolve(actor, ident, require_enabled=False)
        _, _, snapshot = await registry.resolve(self.owner, ident, require_enabled=False)
        await registry.mark_verified(self.owner, snapshot)
        shared = payload.model_copy(update={"visibility": "shared", "shared_with": ["other@example.org"], "enabled": True, "expected_revision": 1})
        await registry.save_definition(self.owner, shared, ident)
        catalog = await registry.list(self.other)
        self.assertEqual(catalog["connections"], [])
        row = next(row for row in catalog["agents"] if row["id"] == ident)
        self.assertFalse(row["can_edit"])
        self.assertNotIn("owner-secret", str(catalog))
        self.assertNotIn("api_key_ciphertext", str(catalog))
        with self.assertRaises(HTTPException):
            await registry.resolve(outsider, ident)
        with self.assertRaises(HTTPException):
            await registry.resolve(self.other, ident, require_enabled=False, require_owner=True)
        with self.assertRaises(HTTPException):
            await registry.delete(self.other, "agents", ident, 2)
        run = await self.store.enqueue(self.other, "shared", self.request.model_copy(update={"agent_id": ident, "product_domain": "ontology"}))
        claimed = await self.store.claim()
        # Removing a grant also prevents an already queued run from using the owner's key.
        await registry.save_definition(self.owner, payload.model_copy(update={"enabled": True, "expected_revision": 2}), ident)
        with patch("agent.model.AgentModel.generate", new=AsyncMock()) as model:
            await AgentWorker(self.store).process(claimed)
            model.assert_not_called()
        self.assertEqual((await self.store.get(self.other, run["id"]))["error_code"], "DEPENDENCY_HTTP_404")
        await registry.save_definition(self.owner, payload.model_copy(update={"enabled": True, "visibility": "public", "expected_revision": 3}), ident)
        _, key, _ = await registry.resolve(outsider, ident)
        self.assertEqual(key, "owner-secret")
        # A recipient can use an agent, but cannot bind a new definition to its private connection.
        with self.assertRaises(HTTPException):
            await registry.save_definition(outsider, payload)

    async def test_sharing_rejects_unknown_users_atomically(self):
        from agent.registry import AgentRegistry, ConnectionUpdate, DefinitionUpdate
        registry = AgentRegistry(self.database)
        connection = (await registry.save_connection(self.owner, ConnectionUpdate(name="Model", api_key="key")))["id"]
        with self.assertRaises(HTTPException) as denied:
            await registry.save_definition(self.owner, DefinitionUpdate(name="Invalid share", model="model", connection_id=connection,
                visibility="shared", shared_with=["missing@example.org"]))
        self.assertEqual(denied.exception.status_code, 422)
        self.assertTrue(all(row["system_key"] for row in (await registry.list(self.owner))["agents"]))

    async def test_general_agent_uses_its_own_contract_and_one_stage(self):
        from agent.registry import AgentRegistry, ConnectionUpdate, DefinitionUpdate
        registry = AgentRegistry(self.database)
        connection = (await registry.save_connection(self.owner, ConnectionUpdate(name="General", api_key="key")))["id"]
        payload = DefinitionUpdate(name="Summarizer", connection_id=connection, model="gpt-6-sol", instructions="Summarize in one sentence.")
        ident = (await registry.save_definition(self.owner, payload))["id"]
        _, _, snapshot = await registry.resolve(self.owner, ident, require_enabled=False)
        await registry.mark_verified(self.owner, snapshot)
        await registry.save_definition(self.owner, payload.model_copy(update={"enabled": True, "expected_revision": 1}), ident)
        submitted = await self.store.enqueue(self.owner, "summary", AgentRunRequest(agent_id=ident, product_domain="ontology",
            input={"task": "Summarize", "context": "Three companies joined."}))
        self.assertEqual(submitted["harness"], "assistant")
        self.assertEqual(submitted["stage_count"], 1)
        run = await self.store.claim()
        with patch("agent.model.AgentModel.generate", new=AsyncMock(return_value="Three companies joined.")) as model:
            await AgentWorker(self.store).process(run)
        current = await self.store.get(self.owner, submitted["id"])
        self.assertEqual((current["status"], current["stage"]), ("completed", 1))
        self.assertEqual(current["result"]["text"], "Three companies joined.")
        self.assertIn("Three companies joined.", model.await_args.args[0])

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
            await self.store.enqueue(self.owner, "one", self.request.model_copy(update={"input": {**self.request.input, "goal": "Different request"}}))

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
        recovered = await AgentStore(self.database).claim()
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
        ))), patch("agent.model.AgentModel.generate", new=AsyncMock(return_value=TEMPLATE)):
            await AgentWorker(self.store).process(run)
        current = await self.store.get(self.owner, submitted["id"])
        self.assertEqual((current["status"], current["stage"]), ("completed", 4))
        self.assertTrue(current["result"]["requires_review"])
        self.assertFalse(current["result"]["published"])
        self.assertFalse(current["result"]["validation"]["adapter_executed"])
        events = await self.store.events(self.owner, submitted["id"], 0, 100)
        self.assertEqual([event["event"]["stage"] for event in events if event["event"]["type"] == "stage_completed"],
                         ["capture", "template", "adapter", "validate"])

    async def test_user_removal_does_not_block_on_settings_audit_reference(self):
        await self.enqueue()
        async with self.database.begin() as conn:
            await conn.execute(text('DELETE FROM app."user" WHERE id=:owner'), {"owner": self.owner})
            count = (await conn.execute(text("SELECT count(*) FROM app.agent_runs"))).scalar_one()
            self.assertEqual(count, 0)
        self.assertTrue((await self.store.settings()).api_key_configured)
