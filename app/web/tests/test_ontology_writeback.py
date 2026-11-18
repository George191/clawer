"""Real PostgreSQL, signed HTTP delivery and a receiver with durable deduplication."""

import asyncio
import importlib
import importlib.util
import json
import sqlite3
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from uuid import uuid4

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models import RoleTypes, TeamUserJoin
from app.web.api.models.ontology_markings import OntologyMarking
from app.web.api.models.ontology_registry import OntologyDefinitionRow
from app.web.api.models.ontology_runtime import OntologyTransaction
from app.web.core.config import settings
from app.web.services import ontology_transactions as tx
from app.web.services import ontology_writeback as wb
from app.web.services.ontology_action_runtime import ActionPayload, submit_action

from app.web.tests import test_ontology_action_runtime_integration as action_tests

runtime = action_tests.runtime


@pytest.mark.asyncio
async def test_signed_delivery_retry_privacy_and_revocation(runtime, tmp_path, monkeypatch):
    engine, user, definition_id, member = runtime

    def migrate(conn):
        migration = importlib.import_module(
            "app.web.alembic.versions.77a1b2c3d4e5_ontology_writeback"
        )
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.downgrade()
            migration.upgrade()

    async with engine.begin() as conn:
        await conn.run_sync(migrate)
    store = tmp_path / "receiver.sqlite3"
    secret = "only-for-isolated-writeback-test-" + uuid4().hex
    monkeypatch.setenv("QA_WRITEBACK_STORE", str(store))
    monkeypatch.setenv("QA_WRITEBACK_SECRET", secret)
    scope = tmp_path / "allowed-project"
    monkeypatch.setenv("QA_WRITEBACK_PROJECT_FILE", str(scope))
    monkeypatch.setenv("QA_WRITEBACK_FAIL_FIRST_ACK", "1")
    receiver_source = (
        Path(__file__).resolve().parents[3] / "web-panel/tests/ontology/writeback_receiver.py"
    )
    spec = importlib.util.spec_from_file_location("writeback_test_receiver", receiver_source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with sqlite3.connect(store) as db:
        db.execute(
            "CREATE TABLE deliveries(id TEXT PRIMARY KEY, digest TEXT, receipt TEXT, body BLOB, requests INTEGER)"
        )
    server = ThreadingHTTPServer(("127.0.0.1", 0), module.Receiver)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    config = tmp_path / "receivers.json"
    monkeypatch.setattr(settings, "ONTOLOGY_WRITEBACK_RECEIVERS_FILE", str(config))
    try:
        async with AsyncSession(engine, expire_on_commit=False) as s:
            row = await s.get(OntologyDefinitionRow, definition_id)
            project = row.project_id
            scope.write_text(str(project))
            endpoint = "http://127.0.0.1:" + str(server.server_port) + "/deliver"
            config.write_text(
                json.dumps(
                    [
                        {
                            "projectId": str(project),
                            "key": "receiver",
                            "name": "Order desk",
                            "endpoint": endpoint,
                            "secret": secret,
                        }
                    ]
                )
            )
            other_id = uuid4()
            assert wb.receivers(other_id) == {}
            defn = deepcopy(row.published_definition)
            await s.execute(
                update(TeamUserJoin).where(TeamUserJoin.id == member).values(role=RoleTypes.OWNER)
            )
            await s.commit()
            original = await submit_action(
                s, user, definition_id, "create", ActionPayload(values={"key": "WO-1"})
            )

            async def modify(revision, note):
                return await tx.commit_operation(
                    s,
                    tx.execute(
                        s,
                        user,
                        project,
                        tx.TransactionRequest(
                            name="Internal note never exported",
                            requestId=uuid4(),
                            steps=[
                                {
                                    "kind": "modify",
                                    "definitionId": definition_id,
                                    "recordId": original.id,
                                    "expectedRevision": revision,
                                    "values": {"note": note},
                                }
                            ],
                        ),
                    ),
                )

            obsolete = await modify(1, "old secret")
            current = await modify(2, "new private value")
            # Remove read access to a previously written field before sending.
            defn["security"]["fieldReaders"] = {"note": [{"kind": "user", "id": str(other_id)}]}
            row.published_definition = defn
            await s.commit()
            assert (await wb.list_status(s, user, project, current.id))["receivers"] == [
                {"key": "receiver", "name": "Order desk"}
            ]
            with pytest.raises(HTTPException) as stale:
                await wb.deliver(s, user, project, obsolete.id, "receiver")
            assert stale.value.status_code == 409
            await s.rollback()
            tx_id = current.id

        async def send():
            async with AsyncSession(engine) as s:
                return await wb.deliver(s, user, project, tx_id, "receiver")

        first = await send()
        assert first["status"] == "failed" and first["attempts"] == 1
        a, b = await asyncio.gather(send(), send())
        assert (
            a == b and a["id"] == first["id"] and a["status"] == "delivered" and a["attempts"] == 2
        )
        with sqlite3.connect(store) as db:
            [(stored_id, digest, receipt, body, requests)] = db.execute(
                "SELECT * FROM deliveries"
            ).fetchall()
        assert stored_id == a["id"] and receipt == a["receiptId"] and requests == 2
        assert wb.hash_of(body) == digest
        assert json.loads(body)["records"][0]["properties"] == {"key": "WO-1"}
        assert (
            b"private value" not in body
            and b"old secret" not in body
            and b"Internal note" not in body
        )
        async with httpx.AsyncClient(trust_env=False) as client:
            assert (await client.post(endpoint, content=b"{}")).status_code in (400, 401)
        async with AsyncSession(engine) as s:
            await s.execute(
                update(TeamUserJoin).where(TeamUserJoin.id == member).values(role=RoleTypes.MEMBER)
            )
            await s.commit()
            with pytest.raises(HTTPException) as denied:
                await wb.list_status(s, user, project, tx_id)
            assert denied.value.status_code == 403
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.asyncio
async def test_writeback_keeps_original_transaction_clearance_after_type_downgrade(runtime):
    engine, user, definition_id, member = runtime
    async with AsyncSession(engine, expire_on_commit=False) as s:
        row = await s.get(OntologyDefinitionRow, definition_id)
        project = row.project_id
        rule = OntologyMarking(
            project_id=project,
            name="Protected orders",
            readers=[{"kind": "user", "id": str(user.id)}],
        )
        s.add(rule)
        await s.flush()
        await s.execute(
            update(TeamUserJoin).where(TeamUserJoin.id == member).values(role=RoleTypes.OWNER)
        )
        definition = deepcopy(row.published_definition)
        definition["security"]["markings"] = [str(rule.id)]
        row.published_definition = definition
        await s.commit()
        batch = await tx.commit_operation(
            s,
            tx.execute(
                s,
                user,
                project,
                tx.TransactionRequest(
                    name="Restricted",
                    requestId=uuid4(),
                    steps=[
                        {"kind": "create", "definitionId": definition_id, "values": {"key": "M-1"}}
                    ],
                ),
            ),
        )
        assert str(rule.id) in (await s.get(OntologyTransaction, batch.id)).markings
        lowered = deepcopy(row.published_definition)
        lowered["security"]["markings"] = []
        row.published_definition = lowered
        rule.readers = []
        await s.commit()
        for op in (
            wb.list_status(s, user, project, batch.id),
            wb.deliver(s, user, project, batch.id, "receiver"),
        ):
            with pytest.raises(HTTPException) as denied:
                await op
            assert denied.value.status_code == 404
