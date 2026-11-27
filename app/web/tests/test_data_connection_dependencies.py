"""Disconnect must expose and preserve configured flows before removing credentials."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.web.api.models.data_connections import ConnectionDependencies
from app.web.api.models.ontology_registry import Definition, Source
from app.web.api.routes import ontology_registry as registry
from app.web.services import data_connections as sources


def result(rows=(), *, one=None):
    response = MagicMock()
    response.mappings.return_value = response
    response.__iter__.side_effect = lambda: iter(rows)
    response.first.return_value = next(iter(rows), None)
    response.one.return_value = one
    return response


def fixture(*, engine="postgresql", business=False, batch=(), transfer=(), ontology=()):
    identifier = str(uuid4())
    row = {"id": identifier, "name": "Saved connection", "engine": engine,
           "config": {}, "credentials_ciphertext": "never-return-this-secret"}
    statements = []

    async def execute(statement, params=None):
        sql = str(statement)
        statements.append((sql, params))
        if "to_regclass" in sql:
            return result(one={"business": True, "batch": True, "transfer": True, "ontology": True})
        if "FROM app.helio_batch_sync_plans" in sql:
            return result(batch)
        if "FROM app.helio_transfer_plans" in sql:
            return result(transfer)
        if "FROM app.helio_registry_definitions" in sql:
            return result(ontology)
        return result([row])

    session = SimpleNamespace(execute=AsyncMock(side_effect=execute), scalar=AsyncMock(return_value=business))

    @asynccontextmanager
    async def context():
        yield session

    service = object.__new__(sources.DataConnections)
    service._session = context
    return service, session, identifier, row, statements


class ConnectionDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_all_sync_schemas_and_draft_and_published_definitions_without_secrets(self):
        plans = [{"id": uuid4(), "name": "Sync one", "source_schema": "one", "source_table": "records"},
                 {"id": uuid4(), "name": "Sync two", "source_schema": "two", "source_table": "records"}]
        definitions = [{"id": uuid4(), "semantic_id": "draft", "name": "Draft object", "draft": True, "published": False},
                       {"id": uuid4(), "semantic_id": "published", "name": "Published object", "draft": False, "published": True}]
        service, _, identifier, _, sql = fixture(business=True, batch=plans, ontology=definitions)
        with patch.object(sources, "require_unconfigured", new_callable=AsyncMock):
            payload = ConnectionDependencies.model_validate(await service.dependencies(identifier))
        self.assertFalse(payload.canDisconnect)
        self.assertEqual([item.type for item in payload.dependencies],
                         ["business_database", "batch_sync", "batch_sync", "object_type", "object_type"])
        self.assertIn("one.records", payload.dependencies[1].message)
        self.assertIn("two.records", payload.dependencies[2].message)
        self.assertIn("draft", payload.dependencies[3].message)
        self.assertIn("published definition", payload.dependencies[4].message)
        self.assertNotIn("never-return-this-secret", payload.model_dump_json())
        # Include hidden/soft-deleted definitions: restoring them must not create dangling sources.
        query = next(statement for statement, _ in sql if "FROM app.helio_registry_definitions" in statement)
        self.assertIn("draft_definition #>>", query)
        self.assertIn("published_definition #>>", query)
        self.assertNotIn("deleted=false", query)

    async def test_delete_rechecks_dependencies_under_update_lock_after_a_clear_preview(self):
        service, _, identifier, _, sql = fixture()
        dependency = {"type": "batch_sync", "id": str(uuid4()), "name": "New sync", "message": "Resolve this sync."}
        service._connection_dependencies = AsyncMock(side_effect=[[], [dependency]])
        self.assertTrue((await service.dependencies(identifier))["canDisconnect"])
        with self.assertRaises(HTTPException) as failure:
            await service.delete(identifier)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertEqual(failure.exception.detail["code"], "connection_in_use")
        self.assertEqual(failure.exception.detail["dependencies"], [dependency])
        self.assertIn("FOR UPDATE", sql[-1][0])
        self.assertFalse(any("DELETE FROM" in statement for statement, _ in sql))

    async def test_saved_source_transfer_draft_blocks_disconnect(self):
        plan = {"id": uuid4(), "name": "Connected flow", "source_schema": "public", "source_table": "orders"}
        service, _, identifier, _, sql = fixture(transfer=[plan])
        with patch.object(sources, "require_unconfigured", new_callable=AsyncMock):
            with self.assertRaises(HTTPException) as failure:
                await service.delete(identifier)
        dependencies = failure.exception.detail["dependencies"]
        self.assertEqual(dependencies[0]["type"], "transfer_plan")
        self.assertEqual(dependencies[0]["id"], str(plan["id"]))
        self.assertFalse(any("DELETE FROM" in statement for statement, _ in sql))

    async def test_unused_connection_deletes_only_registry_row_and_preserves_capture_guard(self):
        service, _, identifier, _, sql = fixture()
        with patch.object(sources, "require_unconfigured", new_callable=AsyncMock) as capture:
            self.assertEqual(await service.delete(identifier), {"deleted": True})
        capture.assert_awaited_once()
        self.assertEqual(sum("DELETE FROM helio_data_connections" in statement for statement, _ in sql), 1)
        self.assertFalse(any("CASCADE" in statement or "DROP" in statement for statement, _ in sql))

    async def test_captures_are_reported_with_their_concrete_resolution_message(self):
        for engine, kind in [("postgresql", "cdc"), ("s3", "object_events"), ("dameng", "dameng_cdc")]:
            service, _, identifier, _, _ = fixture(engine=engine)
            with self.subTest(engine=engine), patch.object(sources, "require_unconfigured", new_callable=AsyncMock,
                    side_effect=HTTPException(409, "Remove the configured capture first")):
                response = await service.dependencies(identifier)
            self.assertEqual(response["dependencies"], [{"type": kind, "id": identifier,
                "name": "Saved connection", "message": "Remove the configured capture first"}])
            self.assertFalse(response["canDisconnect"])

    async def test_capture_verification_failure_never_permits_disconnect(self):
        for method in ("dependencies", "delete"):
            service, _, identifier, _, sql = fixture()
            with self.subTest(method=method), patch.object(sources, "require_unconfigured", new_callable=AsyncMock,
                    side_effect=HTTPException(503, "Kafka Connect is unavailable")):
                with self.assertRaises(HTTPException) as failure:
                    await getattr(service, method)(identifier)
            self.assertEqual(failure.exception.status_code, 503)
            self.assertFalse(any("DELETE FROM" in statement for statement, _ in sql))

    async def test_missing_connection_never_runs_dependency_or_capture_checks(self):
        service, session, identifier, _, _ = fixture()
        session.execute.side_effect = None
        session.execute.return_value = result()
        service._connection_dependencies = AsyncMock()
        for method in ("dependencies", "delete"):
            with self.subTest(method=method), self.assertRaises(HTTPException) as failure:
                await getattr(service, method)(identifier)
            self.assertEqual(failure.exception.status_code, 404)
        service._connection_dependencies.assert_not_awaited()


class OntologyConnectionLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_draft_locks_connection_before_definition_is_persisted_and_committed(self):
        identifier = uuid4()
        definition = Definition.model_validate({"id": "records", "name": "Records", "projectId": uuid4(),
            "source": {"mode": "existing", "schema": "public", "table": "records", "upstreamConnectionId": identifier},
            "security": {"mode": "inherit"}})
        events = []

        async def scalar(statement, params):
            events.append("source_lock")
            self.assertIn("FOR SHARE", str(statement))
            return identifier

        async def flush():
            events.append("flush")

        async def commit():
            events.append("commit")

        session = SimpleNamespace(scalar=AsyncMock(side_effect=scalar), add=MagicMock(),
                                  flush=AsyncMock(side_effect=flush), commit=AsyncMock(side_effect=commit))
        with patch.object(registry, "get_project", new_callable=AsyncMock), \
             patch.object(registry, "require_clearance", new_callable=AsyncMock):
            await registry.new_draft(definition, session, SimpleNamespace(id=uuid4(), is_superuser=True))
        self.assertEqual(events, ["source_lock", "flush", "commit"])
        self.assertEqual(session.add.call_args.args[0].draft_definition["source"]["upstreamConnectionId"], str(identifier))

    async def test_source_lock_uses_the_definitions_transaction(self):
        identifier = uuid4()
        source = Source(mode="existing", schema="public", table="records", upstreamConnectionId=identifier)
        session = SimpleNamespace(scalar=AsyncMock(return_value=identifier))
        self.assertTrue(await registry.lock_upstream_connection(session, source))
        statement, params = session.scalar.await_args.args
        self.assertIn("FROM app.helio_data_connections", str(statement))
        self.assertIn("FOR SHARE", str(statement))
        self.assertEqual(params, {"id": identifier})
        session.scalar.return_value = None
        self.assertFalse(await registry.lock_upstream_connection(session, source))

    async def test_object_managed_sources_need_no_connection_lock(self):
        session = SimpleNamespace(scalar=AsyncMock())
        self.assertTrue(await registry.lock_upstream_connection(session, Source(mode="none")))
        self.assertTrue(await registry.lock_upstream_connection(session, Source(mode="existing", schema="public", table="records")))
        session.scalar.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
