"""Read-only recorded evidence; independent viewer on disposable sender PostgreSQL."""

from contextlib import ExitStack
import importlib
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.web.core.config.snapshot_intent import SnapshotDeliveryEvidenceGrant
from app.web.services import table_export_snapshot_delivery_evidence as evidence
from app.web.services.table_export_snapshot_delivery import compute_snapshot_intent_delivery_proof
from app.web.services.table_export_snapshot_intent import compute_snapshot_intent_private_proof
from app.web.tests.test_transfer_plans import TEST_ACTOR, api

_PATH = "/etl/table-export-sandbox/snapshot-delivery-evidence"
_SCHEMA = "snapshot"
_URL = os.getenv("ONTOLOGY_TABLE_EXPORT_TEST_URL")


@unittest.skipUnless(_URL, "requires disposable local Table Export PostgreSQL")
class SnapshotDeliveryEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_local_read_with_sender_receiver_execution_disabled(self):
        url = make_url(_URL)
        if url.database != "ontology_table_export_regression" or url.host not in {"127.0.0.1", "::1"} or url.query:
            self.fail("Only the exact disposable sender database may be used")
        engine = create_async_engine(url)
        self.addAsyncCleanup(engine.dispose)
        marker, intent, request, owner, revision = (uuid4() for _ in range(5))
        migration = importlib.import_module("app.web.alembic.versions.fb804e491f6d_ontology_private_snapshot_intent")
        delivery = importlib.import_module("app.web.alembic.versions.e57b4216c9a8_ontology_snapshot_intent_delivery_ledger")

        def provision(conn):
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                delivery.upgrade()
                conn.execute(text("ALTER SCHEMA ontology_snapshot_intent_private RENAME TO snapshot"))

        async with engine.begin() as db:
            await db.run_sync(provision)
            await db.execute(text(f"INSERT INTO {_SCHEMA}.identity VALUES (true,:marker)"), {"marker": marker})
            await db.execute(text(f"""
                INSERT INTO {_SCHEMA}.intents
                (id,request_id,actor_id,config_revision,expected_previous_generation,scope_digest,payload_hash,row_count,created_at)
                VALUES (:intent,:request,:owner,:rev,0,repeat('a',64),repeat('b',64),1,pg_catalog.clock_timestamp())
            """), {"intent": intent, "request": request, "owner": owner, "rev": revision})

        async def cleanup():
            async with engine.begin() as db:
                await db.execute(text(f"DROP SCHEMA {_SCHEMA} CASCADE"))

        self.addAsyncCleanup(cleanup)
        async with AsyncSession(engine) as db, db.begin():
            await db.execute(text("SET LOCAL search_path=pg_catalog,pg_temp"))
            private_proof = await compute_snapshot_intent_private_proof(db)
            delivery_proof = await compute_snapshot_intent_delivery_proof(db)
        grant = SnapshotDeliveryEvidenceGrant(
            intentId=intent, requestId=request, ownerActorId=owner, configRevision=revision, expectedPreviousGeneration=0,
            privateMarker=marker, privateProofSha256=private_proof, deliveryProofSha256=delivery_proof,
            scopeDigest="a"*64, executionDigest="c"*64,
        )
        sql_seen = []

        class Capture(AsyncSession):
            async def execute(self, statement, params=None, **kwargs):
                sql_seen.append(str(statement))
                return await super().execute(statement, params, **kwargs)

        async def session():
            async with Capture(engine) as db:
                yield db

        with ExitStack() as patches:
            for field, val in {
                "ENVIRONMENT": "local", "TABLE_EXPORT_SANDBOX_ENABLED": True,
                "TABLE_EXPORT_SNAPSHOT_PUBLICATION_ENABLED": False, "TABLE_EXPORT_SNAPSHOT_INTENT_ENABLED": False,
                "TABLE_EXPORT_SNAPSHOT_DELIVERY_ENABLED": False, "TABLE_EXPORT_SNAPSHOT_DELIVERY_EVIDENCE_ENABLED": True,
                "TABLE_EXPORT_SNAPSHOT_DELIVERY_EVIDENCE_GRANTS": {TEST_ACTOR: grant},
            }.items():
                patches.enter_context(patch.object(evidence.settings, field, val))
            patches.enter_context(patch("app.web.core.config._resolved_pg_url", return_value=_URL))
            async with api(True, session) as admin, api(False, session) as reader:
                self.assertEqual((await reader.get(_PATH)).status_code, 403)
                cap = await admin.get(_PATH)
                self.assertEqual(cap.status_code, 200)
                self.assertEqual(cap.headers["cache-control"], "private, no-store")
                self.assertEqual(set(cap.json()), {"available", "configRevision"})
                self.assertTrue(cap.json()["available"])
                self.assertNotEqual(cap.json()["configRevision"], str(revision))
                dto = {"intentId": str(intent), "requestId": str(request), "expectedConfigRevision": cap.json()["configRevision"]}
                misses = (
                    await admin.post(_PATH, json={**dto, "requestId": str(uuid4())}),
                    await admin.post(_PATH, json={**dto, "expectedConfigRevision": str(revision)}),
                    await admin.post(_PATH, json={**dto, "unknown": "never echo this"}),
                    await admin.post(_PATH, json={**dto, "intentId": "not-a-uuid"}),
                )
                for miss in misses:
                    self.assertEqual((miss.status_code, miss.json(), miss.headers["cache-control"]),
                                     (404, {"detail": "Recorded snapshot delivery evidence unavailable"}, "private, no-store"))
                self.assertEqual((await admin.post(_PATH, json=dto)).json(), {"state": "not_admitted"})
                async with engine.begin() as db:
                    await db.execute(text(f"""
                        INSERT INTO {_SCHEMA}.deliveries
                        (intent_id,request_id,scope_digest,payload_hash,expected_previous_generation,
                         delivery_config_revision,execution_digest,row_count,admitted_at)
                        VALUES (:intent,:request,repeat('a',64),repeat('b',64),0,:rev,repeat('c',64),1,clock_timestamp())
                    """), {"intent": intent, "request": request, "rev": revision})
                self.assertEqual((await admin.post(_PATH, json=dto)).json(), {"state": "unconfirmed"})
                async with engine.begin() as db:
                    await db.execute(text(f"""
                        UPDATE {_SCHEMA}.deliveries SET receiver_generation=1,receiver_recorded_at=clock_timestamp(),
                        receiver_row_count=1
                    """))
                result = await admin.post(_PATH, json=dto)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(set(result.json()), {"state", "generation", "rowCount", "recordedAt"})
                self.assertEqual((result.json()["state"], result.json()["generation"], result.json()["rowCount"]),
                                 ("confirmed_recorded_receipt", 1, 1))
                for set_clock, restore_clock in (
                    ("receiver_recorded_at=clock_timestamp()+interval '1 day'", "receiver_recorded_at=clock_timestamp()"),
                    ("admitted_at=clock_timestamp()+interval '1 day'", "admitted_at=clock_timestamp()"),
                    ("admitted_at=clock_timestamp()-interval '1 day'", "admitted_at=clock_timestamp()"),
                ):
                    async with engine.begin() as db:
                        await db.execute(text(f"UPDATE {_SCHEMA}.deliveries SET {set_clock}"))
                    self.assertEqual((await admin.post(_PATH, json=dto)).status_code, 404)
                    async with engine.begin() as db:
                        await db.execute(text(f"UPDATE {_SCHEMA}.deliveries SET {restore_clock}"))
                async with engine.begin() as db:
                    await db.execute(text(f"UPDATE {_SCHEMA}.deliveries SET execution_digest=repeat('d',64)"))
                self.assertEqual((await admin.post(_PATH, json=dto)).status_code, 404)
                async with engine.begin() as db:
                    await db.execute(text(f"LOCK TABLE {_SCHEMA}.deliveries IN ACCESS EXCLUSIVE MODE"))
                    self.assertEqual((await admin.post(_PATH, json=dto)).status_code, 404)
                with patch.object(evidence.settings, "TABLE_EXPORT_SNAPSHOT_DELIVERY_EVIDENCE_GRANTS", {}):
                    self.assertEqual((await admin.get(_PATH)).json(), {"available": False})
                    self.assertEqual((await admin.post(_PATH, json=dto)).status_code, 404)
        self.assertTrue(any("REPEATABLE READ, READ ONLY" in sql for sql in sql_seen))
        self.assertFalse(any(f"FROM ONLY {_SCHEMA}.records" in sql or "ts_table_export_sandbox" in sql for sql in sql_seen))
        self.assertFalse(any(sql.lstrip().startswith(("INSERT", "UPDATE", "DELETE")) for sql in sql_seen))
