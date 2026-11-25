"""Saved-source draft identity, metadata reads, and isolation from the sandbox runner."""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.web.api.models.transfer_plans import TransferPlanPayload, TransferPlanRow, TransferRunRequest
from app.web.services import transfer_plans as plans, transfer_runs as runs, transfer_schedules as schedules
from app.web.services import asset_freshness


REVISION = datetime(2026, 11, 25, tzinfo=timezone.utc)
CONNECTION = uuid4()


def payload(**changes):
    return TransferPlanPayload.model_validate({"name": "Connected flow", "sourceSchema": "public", "sourceTable": "orders",
        "sourceNode": {"connectionId": CONNECTION, "kind": "schema", "name": "public"}, "sourceUpdatedAt": REVISION,
        "targetSchema": "ts_ods", "targetTable": "orders", "writeMode": "append", "trigger": "manual", **changes})


def session_for_source(row):
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    return SimpleNamespace(execute=AsyncMock(return_value=result), add=MagicMock(), commit=AsyncMock(), get=AsyncMock())


class TransferSourceNodeTests(unittest.IsolatedAsyncioTestCase):
    def test_source_identity_is_required_and_names_are_not_limited_to_warehouse_identifiers(self):
        for kind, name in [("schema", "public"), ("database", "sales"), ("index", "audit-2026.11"), ("schema", " quoted schema ")]:
            value = payload(sourceSchema=name, sourceTable=name,
                sourceNode={"connectionId": CONNECTION, "kind": kind, "name": name})
            self.assertEqual(value.sourceNode.name, name)
            self.assertEqual(value.sourceSchema, name)
            self.assertEqual(value.sourceTable, name)
            self.assertEqual(value.sourceUpdatedAt, REVISION)
        for changes in ({"sourceUpdatedAt": None}, {"sourceNode": None}, {"sourceSchema": "another"},
                        {"sourceNode": None, "sourceUpdatedAt": None}, {"sourceUpdatedAt": "2026-11-25T00:00:00"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                payload(**changes)

    async def test_save_uses_exact_source_revision_and_stores_node_reference(self):
        session = session_for_source({"updated_at": REVISION, "paused_at": None})
        events = []
        result = session.execute.return_value
        session.execute.side_effect = lambda *_: events.append("lock") or result
        warehouse = SimpleNamespace(transfer_endpoints=AsyncMock(return_value={("ts_ods", "orders"): True}))
        with patch.object(plans, "_external_columns", new_callable=AsyncMock, side_effect=lambda _: events.append("catalog")) as columns, \
             patch.object(plans, "get_etl_layers", return_value=warehouse):
            saved = await plans.save_transfer_plan(session, payload())
        columns.assert_awaited_once()
        self.assertEqual(events, ["catalog", "lock"])
        self.assertIn("FOR SHARE", str(session.execute.await_args.args[0]))
        self.assertEqual(session.execute.await_args.args[1], {"id": CONNECTION})
        warehouse.transfer_endpoints.assert_awaited_once_with(("ts_ods", "orders"), ("ts_ods", "orders"))
        self.assertEqual(saved.sourceNode.connectionId, CONNECTION)
        self.assertEqual(saved.sourceUpdatedAt, REVISION)
        self.assertEqual(session.add.call_args.args[0].source_node["name"], "public")
        session.commit.assert_awaited_once()

    async def test_changed_missing_and_paused_source_fail_before_persistence(self):
        for row, status in [(None, 404), ({"updated_at": REVISION, "paused_at": REVISION}, 409),
                            ({"updated_at": datetime(2020, 1, 1, tzinfo=timezone.utc), "paused_at": None}, 409)]:
            session = session_for_source(row)
            with self.subTest(row=row), patch.object(plans, "_external_columns", new_callable=AsyncMock) as columns:
                with self.assertRaises(HTTPException) as failure:
                    await plans.save_transfer_plan(session, payload())
            self.assertEqual(failure.exception.status_code, status)
            columns.assert_awaited_once()
            session.add.assert_not_called()
            session.commit.assert_not_awaited()

    async def test_external_preflight_reads_real_columns_without_claiming_cross_engine_compatibility(self):
        source = SimpleNamespace(hasMore=False, columns=[SimpleNamespace(name="id", nullable=False, type="int")])
        warehouse = SimpleNamespace(transfer_endpoint_columns=AsyncMock(return_value={
            ("ts_ods", "orders"): {"columns": {"id": {"not_null": True}}}}))
        with patch.object(plans, "_external_columns", new_callable=AsyncMock, return_value=source) as columns, \
             patch.object(plans, "get_etl_layers", return_value=warehouse):
            response = await plans.preflight_transfer_plan(payload())
        columns.assert_awaited_once()
        self.assertEqual(response.status, "blocked")
        self.assertEqual(response.checkedColumns, 1)
        self.assertIn("external_type_mapping_unverified", [issue.code for issue in response.issues])

    async def test_list_filters_exact_connection_kind_and_node_name(self):
        session = SimpleNamespace(scalars=AsyncMock(return_value=[]))
        await plans.list_transfer_plans(session, "public", connection_id=CONNECTION, node_kind="schema")
        statement = str(session.scalars.await_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        self.assertIn(str(CONNECTION), statement)
        self.assertIn("'connectionId'", statement)
        self.assertIn("'kind'", statement)
        self.assertIn("'name'", statement)
        self.assertIn("'public'", statement)
        self.assertNotIn("target_schema = 'public'", statement)
        await plans.list_transfer_plans(session, "ts_ods")
        statement = str(session.scalars.await_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        self.assertIn("source_node IS NULL", statement)
        self.assertIn("target_schema = 'ts_ods'", statement)

    async def test_external_same_named_sandbox_node_cannot_execute_or_arm_a_schedule(self):
        row = TransferPlanRow(id=uuid4(), name="External sandbox name", source_schema=runs.SANDBOX,
            source_table="source", target_schema=runs.SANDBOX, target_table="target", write_mode="upsert",
            trigger="scheduled", schedule_time="09:00", updated_at=REVISION,
            source_node={"connectionId": str(CONNECTION), "kind": "schema", "name": runs.SANDBOX}, source_updated_at=REVISION)
        self.assertFalse(runs.is_sandbox_upsert(row, "scheduled"))
        self.assertFalse(schedules._eligible(row))
        self.assertFalse(asset_freshness._eligible(row, "target"))
        row.trigger = "manual"
        row.schedule_time = None
        session = SimpleNamespace(get=AsyncMock(return_value=row), add=MagicMock(), commit=AsyncMock())
        request = TransferRunRequest(requestId=uuid4(), expectedUpdatedAt=REVISION)
        with patch.object(runs, "transfer_run_capabilities", return_value=SimpleNamespace(enabled=True)), \
             patch.object(runs, "_pin_attempt_scope", new_callable=AsyncMock) as pin:
            with self.assertRaises(HTTPException) as failure:
                await runs.run_sandbox_transfer(session, row.id, request, uuid4())
        self.assertEqual(failure.exception.status_code, 422)
        pin.assert_not_awaited()
        session.add.assert_not_called()
        session.commit.assert_not_awaited()

    async def test_removing_an_unused_external_draft_requires_its_current_revision(self):
        value = payload()
        row = TransferPlanRow(name=value.name, source_schema=value.sourceSchema, source_table=value.sourceTable,
            source_node=value.sourceNode.model_dump(mode="json"), source_updated_at=REVISION,
            target_schema=value.targetSchema, target_table=value.targetTable, write_mode="append", trigger="manual", updated_at=REVISION)
        selected = MagicMock()
        selected.one_or_none.return_value = row
        session = SimpleNamespace(scalars=AsyncMock(return_value=selected), delete=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
        with self.assertRaises(HTTPException) as failure:
            await plans.delete_external_transfer_plan(session, row.id, datetime(2020, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(failure.exception.status_code, 409)
        session.delete.assert_not_awaited()
        response = await plans.delete_external_transfer_plan(session, row.id, REVISION)
        self.assertEqual(response, {"deleted": True, "id": str(row.id)})
        self.assertIn("FOR UPDATE", str(session.scalars.await_args.args[0]))
        session.delete.assert_awaited_once_with(row)
        session.commit.assert_awaited_once()

    async def test_removing_a_draft_preserves_history_and_internal_plans(self):
        row = SimpleNamespace(source_node=None, updated_at=REVISION)
        selected = MagicMock()
        selected.one_or_none.return_value = row
        session = SimpleNamespace(scalars=AsyncMock(return_value=selected), delete=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock())
        with self.assertRaises(HTTPException) as failure:
            await plans.delete_external_transfer_plan(session, uuid4(), REVISION)
        self.assertEqual(failure.exception.status_code, 409)
        session.delete.assert_not_awaited()
        row.source_node = {"connectionId": str(CONNECTION), "kind": "schema", "name": "public"}
        session.commit.side_effect = IntegrityError("DELETE", {}, SimpleNamespace(sqlstate="23503"))
        with self.assertRaises(HTTPException) as failure:
            await plans.delete_external_transfer_plan(session, uuid4(), REVISION)
        self.assertEqual(failure.exception.status_code, 409)
        self.assertIn("execution history", failure.exception.detail)
        session.rollback.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
