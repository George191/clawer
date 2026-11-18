"""Producer and admin boundaries with optional isolated numeric-loopback PostgreSQL."""

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from hashlib import sha256
import importlib
import os
import time
from unittest import IsolatedAsyncioTestCase, skipUnless
from unittest.mock import patch
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.web.core.config import settings
from app.web.push_inbox_ingress import PATH, _payload, create_ingress, signing_message
from app.web.services.push_inbox import local_inbox_database, strict_base64
from app.web.tests.test_transfer_plans import api

_DB = "ontology_push_inbox_regression"
_URL = os.getenv("ONTOLOGY_PUSH_INBOX_TEST_URL")
_BODY = b'{"schemaVersion":1,"entityId":"9223372036854775807","value":"fixture-a"}'
_ADMIN = "/etl/push-inbox-sandbox"
_APPLY = _ADMIN + "/apply"
_INTAKE = _ADMIN + "/intake"


def headers(key, body, *, identity=None, stamp=None):
    identity = identity or uuid4()
    stamp = str(int(time.time())) if stamp is None else str(stamp)
    proof = key.sign(signing_message(stamp, identity, sha256(body).hexdigest()))
    return {"X-Inbox-Request-Id": str(identity), "X-Inbox-Timestamp": stamp,
            "X-Inbox-Signature": base64.b64encode(proof).decode("ascii"), "Content-Type": "application/json"}


def secondary_headers(key, body, *, identity=None, stamp=None):
    identity = identity or uuid4()
    stamp = str(int(time.time())) if stamp is None else str(stamp)
    # Independent canonical byte construction; avoid coupling the signer to its verifier.
    digest = sha256(body).hexdigest()
    proof = key.sign(f"synthetic-push-v2\nPOST\n/synthetic-push/v1/inbox\nsecondary\n{stamp}\n{identity}\n{digest}\n".encode())
    return {"X-Inbox-Request-Id": str(identity), "X-Inbox-Timestamp": stamp, "X-Inbox-Producer": "secondary",
            "X-Inbox-Signature": base64.b64encode(proof).decode("ascii"), "Content-Type": "application/json"}


def public_key(key):
    return SecretStr(base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii"))


class PushInboxContract(IsolatedAsyncioTestCase):
    def test_schema_and_canonical_encoding(self):
        self.assertEqual(_payload(_BODY), (2**63-1, "fixture-a"))
        for body in (
            b'{"schemaVersion":true,"entityId":"1","value":"fixture-a"}',
            b'{"schemaVersion":1,"entityId":"01","value":"fixture-a"}',
            b'{"schemaVersion":1,"entityId":"9223372036854775808","value":"fixture-a"}',
            b'{"schemaVersion":1,"schemaVersion":1,"entityId":"1","value":"fixture-a"}',
            b'{"schemaVersion":1,"entityId":"1","value":"fixture-a","unknown":"x"}',
            b'{"schemaVersion":1,"entityId":"1","value":"arbitrary-content"}',
        ):
            with self.subTest(body=body):
                self.assertIsNone(_payload(body))
        self.assertIsNone(strict_base64(b"AA", 1))
        self.assertIsNone(strict_base64(b"AB==", 1))
        self.assertEqual(strict_base64(b"AA==", 1), b"\x00")

    def test_database_url_rejects_dns_external_unix_and_connection_overrides(self):
        class Configured:
            def __init__(self, dsn):
                self.dsn = dsn
            def unicode_string(self):
                return self.dsn

        for dsn in (
            "postgresql+asyncpg://reader@localhost/fixture",
            "postgresql+asyncpg://reader@192.0.2.1/fixture",
            "postgresql+asyncpg://reader@/fixture",
            "postgresql+asyncpg://reader@127.0.0.1/fixture?host=untrusted",
        ):
            with patch("app.web.services.push_inbox.settings",
                       SQLALCHEMY_RUNNABLE_DATABASE_URI=Configured(dsn)):
                self.assertIsNone(local_inbox_database(), dsn)
        with patch("app.web.services.push_inbox.settings",
                   SQLALCHEMY_RUNNABLE_DATABASE_URI=Configured("postgresql+asyncpg://reader@127.0.0.1/fixture")):
            self.assertEqual(local_inbox_database().host, "127.0.0.1")

    async def test_current_signature_and_actual_scope_reject_before_database(self):
        key, other = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        calls = []

        def sessions():
            calls.append(True)
            raise AssertionError("unauthorized ingress reached the database factory")

        async def chunks():
            yield b" " * 3000
            yield b" " * 2000

        with (
            patch.object(settings, "ENVIRONMENT", "local"),
            patch.object(settings, "PUSH_INBOX_SANDBOX_ENABLED", True),
            patch.object(settings, "PUSH_INBOX_SANDBOX_DATABASE_MARKER", uuid4()),
            patch.object(settings, "PUSH_INBOX_SANDBOX_PUBLIC_KEY", public_key(key)),
            patch("app.web.services.push_inbox.local_inbox_database", return_value=object()),
        ):
            app = create_ingress(sessions)
            async with AsyncClient(transport=ASGITransport(app=app, client=("127.0.0.1", 41000)),
                                   base_url="http://127.0.0.1") as client:
                for evidence in (
                    headers(other, _BODY), headers(key, _BODY, stamp=int(time.time())-301),
                    {**headers(key, _BODY), "X-Forwarded-For": "127.0.0.1"},
                    {**headers(key, _BODY), "Content-Encoding": "identity"},
                    [*headers(key, _BODY).items(), ("X-Inbox-Signature", "duplicate")],
                ):
                    result = await client.post(PATH, content=_BODY, headers=evidence)
                    self.assertEqual((result.status_code, result.json()),
                                     (401, {"detail": "Unauthorized synthetic producer"}))
                self.assertEqual((await client.post(PATH + "?flag=1", content=_BODY, headers=headers(key, _BODY))).status_code,
                                 404)
                self.assertEqual((await client.post(PATH, content=chunks(), headers=headers(key, b" " * 5000))).status_code,
                                 413)
            async with AsyncClient(transport=ASGITransport(app=app, client=("192.0.2.10", 41000)),
                                   base_url="http://127.0.0.1") as client:
                result = await client.post(PATH, content=_BODY, headers=headers(key, _BODY))
                self.assertEqual(result.status_code, 401)
        self.assertEqual(calls, [])

    async def test_ordinary_app_never_mounts_producer_and_decouples_admin(self):
        with patch.object(settings, "PUSH_INBOX_SANDBOX_ENABLED", False):
            async with api(True) as normal:
                self.assertEqual((await normal.post(PATH)).status_code, 404)
                self.assertEqual((await normal.post(_ADMIN)).status_code, 405)
                self.assertEqual((await normal.get(_ADMIN)).json()["state"], "disabled")
        async with api(False) as public:
            self.assertEqual((await public.get(_ADMIN)).status_code, 403)


@skipUnless(_URL, "requires disposable local Push inbox PostgreSQL")
class PushInboxPostgres(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        url = make_url(_URL)
        if url.database != _DB or url.query or url.host not in {"127.0.0.1", "::1"}:
            self.fail("Use only the exact isolated Push inbox test database")
        self.engine = create_async_engine(url, pool_size=4, max_overflow=0)
        self.addAsyncCleanup(self.engine.dispose)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.marker = uuid4()
        m = importlib.import_module("app.web.alembic.versions.e61c9af524d7_ontology_push_inbox")

        def provision(conn):
            with Operations.context(MigrationContext.configure(conn)):
                m.upgrade()

        async with self.engine.begin() as conn:
            self.assertEqual(await conn.scalar(text("SELECT current_database()")), _DB)
            await conn.run_sync(provision)
            await conn.execute(text("UPDATE ontology_push_inbox_state SET database_marker=:marker"), {"marker": self.marker})
        self.addAsyncCleanup(self._cleanup)
        self.key = Ed25519PrivateKey.generate()
        for setting, value in (
            ("ENVIRONMENT", "local"), ("PUSH_INBOX_SANDBOX_ENABLED", True),
            ("PUSH_INBOX_SANDBOX_DATABASE_MARKER", self.marker), ("PUSH_INBOX_SANDBOX_PUBLIC_KEY", public_key(self.key)),
        ):
            fixture = patch.object(settings, setting, value)
            fixture.start()
            self.addCleanup(fixture.stop)
        binding = patch("app.web.services.push_inbox.local_inbox_database", return_value=url)
        binding.start()
        self.addCleanup(binding.stop)

    async def _cleanup(self):
        async with self.engine.begin() as conn:
            await conn.execute(text("DROP TABLE ontology_push_inbox_receipts, ontology_push_inbox_state"))

    async def _admin_session(self):
        async with self.session() as session:
            yield session

    def _producer(self, sessions=None):
        app = create_ingress(sessions or self.session)
        return AsyncClient(transport=ASGITransport(app=app, client=("127.0.0.1", 47201)), base_url="http://127.0.0.1")

    async def test_deduplication_rotation_receipts_and_normalized_payload_not_exposed(self):
        async with self._producer() as client, api(True, self._admin_session) as admin:
            identity = uuid4()
            self.assertEqual((await admin.get(_ADMIN)).json()["acceptedCount"], 0)
            attempt = await asyncio.gather(*(
                client.post(PATH, headers=headers(self.key, _BODY, identity=identity), content=_BODY) for _ in range(5)
            ))
            self.assertTrue(all(result.status_code == 201 for result in attempt), [r.text for r in attempt])
            original = attempt[0].json()
            self.assertTrue(all(result.json() == original for result in attempt))
            changed = _BODY.replace(b"fixture-a", b"fixture-b")
            self.assertEqual((await client.post(PATH, headers=headers(self.key, changed, identity=identity),
                                                content=changed)).status_code, 409)
            self.assertEqual((await client.post(PATH, headers=headers(self.key, b"{}"), content=b"{}")).status_code, 422)
            rotated = Ed25519PrivateKey.generate()
            with patch.object(settings, "PUSH_INBOX_SANDBOX_PUBLIC_KEY", public_key(rotated)):
                self.assertEqual((await client.post(PATH, headers=headers(self.key, _BODY, identity=identity),
                                                    content=_BODY)).status_code, 401)
                accepted = await client.post(PATH, headers=headers(rotated, _BODY, identity=identity), content=_BODY)
                self.assertEqual(accepted.json(), original)
            with patch.object(settings, "PUSH_INBOX_SANDBOX_DATABASE_MARKER", uuid4()):
                self.assertEqual((await admin.get(_ADMIN)).json()["state"], "unconfigured")
                self.assertEqual((await client.post(PATH, headers=headers(self.key, _BODY), content=_BODY)).status_code, 503)
            status = (await admin.get(_ADMIN)).json()
            self.assertEqual(status["recent"], [original])
            self.assertEqual(set(original), {"id", "ordinal", "acceptedAt"})
            self.assertNotIn("fixture-a", str(status))
            self.assertNotIn(str(identity), str(status))
            async with self.engine.connect() as conn:
                stored = (await conn.execute(text("""
                    SELECT entity_id, fixture_value, body_digest, schema_version FROM ontology_push_inbox_receipts
                """))).one()
                self.assertEqual(tuple(stored), (2**63-1, "fixture-a", sha256(_BODY).hexdigest(), 1))

    async def test_secondary_current_signature_and_shared_immutable_acceptance(self):
        other, rotated = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        primary_id, secondary_id = uuid4(), uuid4()
        async with self._producer() as client, api(True, self._admin_session) as admin:
            async def send(values, body=_BODY):
                return await client.post(PATH, content=body, headers=values)

            denied = {"detail": "Unauthorized synthetic producer"}
            for flag, public in ((False, public_key(other)), (True, public_key(self.key)), (True, SecretStr("bad"))):
                with (patch.object(settings, "PUSH_INBOX_SANDBOX_SECONDARY_ENABLED", flag),
                      patch.object(settings, "PUSH_INBOX_SANDBOX_SECONDARY_PUBLIC_KEY", public)):
                    response = await send(secondary_headers(other, _BODY, identity=secondary_id))
                    self.assertEqual((response.status_code, response.json()), (401, denied))
                    self.assertNotIn("secondaryProducerAvailable", (await admin.get(_ADMIN)).json())

            primary = await send(headers(self.key, _BODY, identity=primary_id))
            self.assertEqual(primary.status_code, 201)
            with (patch.object(settings, "PUSH_INBOX_SANDBOX_SECONDARY_ENABLED", True),
                  patch.object(settings, "PUSH_INBOX_SANDBOX_SECONDARY_PUBLIC_KEY", public_key(other))):
                self.assertIs((await admin.get(_ADMIN)).json()["secondaryProducerAvailable"], True)
                signed = secondary_headers(other, _BODY, identity=secondary_id)
                missing = {key: value for key, value in signed.items() if key != "X-Inbox-Producer"}
                for values in (missing, {**signed, "X-Inbox-Producer": "primary"},
                               {**signed, "X-Inbox-Producer": "Secondary"},
                               [*signed.items(), ("x-inbox-producer", "secondary")],
                               {**headers(self.key, _BODY, identity=secondary_id), "X-Inbox-Producer": "secondary"},
                               secondary_headers(self.key, _BODY, identity=secondary_id),
                               secondary_headers(other, _BODY, identity=secondary_id, stamp=int(time.time())-301)):
                    failure = await send(values)
                    self.assertEqual((failure.status_code, failure.json()), (401, denied))

                self.assertEqual((await send(secondary_headers(other, _BODY, identity=primary_id))).status_code, 409)
                first, retry = await asyncio.gather(send(signed), send(signed))
                self.assertEqual((first.status_code, retry.status_code), (201, 201))
                self.assertEqual(first.json(), retry.json())
                self.assertEqual(first.json()["ordinal"], "2")
                self.assertEqual((await send(headers(self.key, _BODY, identity=secondary_id))).status_code, 409)
                changed = _BODY.replace(b"fixture-a", b"fixture-b")
                self.assertEqual((await send(secondary_headers(other, changed, identity=secondary_id), changed)).status_code, 409)
                with patch.object(settings, "PUSH_INBOX_SANDBOX_SECONDARY_PUBLIC_KEY", public_key(rotated)):
                    self.assertEqual((await send(signed)).status_code, 401)
                    self.assertEqual((await send(secondary_headers(rotated, _BODY, identity=secondary_id))).json(), first.json())
                self.assertEqual((await send(headers(self.key, _BODY, identity=primary_id))).json(), primary.json())
            self.assertEqual((await send(signed)).status_code, 401)
            self.assertEqual((await admin.get(_ADMIN)).json()["acceptedCount"], 2)
            async with self.engine.connect() as conn:
                values = (await conn.execute(text(
                    "SELECT body_digest FROM public.ontology_push_inbox_receipts ORDER BY ordinal"
                ))).scalars().all()
                self.assertEqual(values, [sha256(_BODY).hexdigest(),
                                          sha256(b"synthetic-push-secondary-receipt-v1\0" + sha256(_BODY).digest()).hexdigest()])

    async def test_fault_rolls_back_all_and_exact_retry_reconciles_after_ack_loss_and_quota(self):
        # Force an actual SQL failure at the immutable receipt insertion.
        async with self.engine.begin() as conn:
            await conn.execute(text("""
                CREATE FUNCTION public.fail_synthetic_inbox() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'fixture-only test fault'; END; $$
            """))
            await conn.execute(text("""
                CREATE TRIGGER fail_synthetic_inbox BEFORE INSERT ON ontology_push_inbox_receipts
                FOR EACH ROW EXECUTE FUNCTION public.fail_synthetic_inbox()
            """))
        self.addAsyncCleanup(self._remove_fault_fn)
        identity = uuid4()
        async with self._producer() as client, api(True, self._admin_session) as admin:
            failed = await client.post(PATH, headers=headers(self.key, _BODY, identity=identity), content=_BODY)
            self.assertEqual(failed.status_code, 503)
            self.assertEqual((await admin.get(_ADMIN)).json()["acceptedCount"], 0)
            async with self.engine.begin() as conn:
                self.assertEqual(await conn.scalar(text("SELECT window_accepted FROM ontology_push_inbox_state")), 0)
                await conn.execute(text("DROP TRIGGER fail_synthetic_inbox ON ontology_push_inbox_receipts"))

            @asynccontextmanager
            async def lost_ack():
                async with self.session() as session:
                    yield session
                raise OSError("allowed test-only lost acknowledgement after real commit")

            async with self._producer(lost_ack) as lossy:
                missed = await lossy.post(PATH, headers=headers(self.key, _BODY, identity=identity), content=_BODY)
                self.assertEqual(missed.status_code, 503)
            observed = (await admin.get(_ADMIN)).json()
            self.assertEqual(observed["acceptedCount"], 1)
            replay = await client.post(PATH, headers=headers(self.key, _BODY, identity=identity), content=_BODY)
            self.assertEqual(replay.json(), observed["recent"][0])

            @asynccontextmanager
            async def fixed_database_clock():
                async with self.session() as session:
                    original = session.scalar

                    async def scalar(statement, *args, **kwargs):
                        if str(statement) == "SELECT pg_catalog.clock_timestamp()":
                            return moment
                        return await original(statement, *args, **kwargs)

                    with patch.object(session, "scalar", scalar):
                        yield session

            moment = datetime.now(timezone.utc)
            async with self.engine.begin() as conn:
                await conn.execute(text("""
                    UPDATE ontology_push_inbox_state SET window_epoch=:epoch,window_accepted=20
                """), {"epoch": int(moment.timestamp()) // 60})
            async with self._producer(fixed_database_clock) as fixed:
                result = await fixed.post(PATH, headers=headers(self.key, _BODY), content=_BODY)
                self.assertEqual(result.status_code, 429, result.text)
                self.assertEqual((await fixed.post(PATH, headers=headers(self.key, _BODY, identity=identity),
                                                   content=_BODY)).json(), replay.json())
            self.assertEqual((await admin.get(_ADMIN)).json()["acceptedCount"], 1)

    async def test_total_capacity_blocks_new_acceptance_but_not_the_exact_old_receipt(self):
        identity = uuid4()
        async with self._producer() as client, api(True, self._admin_session) as admin:
            first = await client.post(PATH, headers=headers(self.key, _BODY, identity=identity), content=_BODY)
            self.assertEqual(first.status_code, 201, first.text)
            async with self.engine.begin() as conn:
                await conn.execute(text("""
                    INSERT INTO ontology_push_inbox_receipts
                        (id,request_id,ordinal,body_digest,schema_version,entity_id,fixture_value,accepted_at)
                    SELECT gen_random_uuid(),gen_random_uuid(),n,repeat('a',64),1,n,'fixture-seeded',clock_timestamp()
                    FROM generate_series(2,1000) AS n
                """))
                await conn.execute(text("UPDATE ontology_push_inbox_state SET accepted_count=1000"))
            ready = (await admin.get(_ADMIN)).json()
            self.assertEqual((ready["state"], ready["acceptedCount"], len(ready["recent"])), ("full", 1000, 10))
            self.assertEqual([item["ordinal"] for item in ready["recent"]], [str(n) for n in range(1000, 990, -1)])
            self.assertEqual((await client.post(PATH, headers=headers(self.key, _BODY), content=_BODY)).status_code, 429)
            replay = await client.post(PATH, headers=headers(self.key, _BODY, identity=identity), content=_BODY)
            self.assertEqual(replay.json(), first.json())

    async def test_reviewed_intake_pause_is_sticky_keeps_replay_and_fences_legacy_writers(self):
        await self._prepare_apply_fixture()
        migration = importlib.import_module("app.web.alembic.versions.a9fcc74e95b1_ontology_push_intake_control")

        def ddl(conn, action):
            with Operations.context(MigrationContext.configure(conn)):
                action()

        async with self.engine.begin() as conn:
            await conn.run_sync(ddl, migration.upgrade)
            # Empty/unreviewed state can safely round-trip for a rolling deployment.
            await conn.run_sync(ddl, migration.downgrade)
        async with self.engine.begin() as conn:
            await conn.run_sync(ddl, migration.upgrade)

        async def drop_guard_function():
            async with self.engine.begin() as conn:
                await conn.execute(text("DROP FUNCTION public.push_inbox_intake_fence() CASCADE"))

        self.addAsyncCleanup(drop_guard_function)
        async with self._producer() as producer, api(True, self._admin_session) as admin, api(False) as reader:
            self.assertNotIn("intakeGeneration", (await admin.get(_ADMIN)).json())
            desired = {"expectedGeneration": 0, "paused": True}
            self.assertEqual((await reader.put(_INTAKE, json=desired)).status_code, 403)
            self.assertEqual((await admin.put(_INTAKE, json=desired)).status_code, 403)
            request_id = uuid4()
            accepted = await producer.post(PATH, headers=headers(self.key, _BODY, identity=request_id), content=_BODY)
            self.assertEqual(accepted.status_code, 201)
            with (
                patch.object(settings, "PUSH_INBOX_SANDBOX_INTAKE_CONTROL_ENABLED", True),
                patch.object(settings, "PUSH_INBOX_SANDBOX_APPLY_ENABLED", True),
            ):
                initial = (await admin.get(_ADMIN)).json()
                self.assertEqual((initial["intakeControlAvailable"], initial["intakeGeneration"]), (True, 0))
                for invalid in ({**desired, "expectedGeneration": True}, {**desired, "paused": 1}, {**desired, "extra": 0}):
                    self.assertEqual((await admin.put(_INTAKE, json=invalid)).status_code, 422)
                with patch.object(settings, "PUSH_INBOX_SANDBOX_DATABASE_MARKER", uuid4()):
                    self.assertEqual((await admin.put(_INTAKE, json=desired)).status_code, 503)
                self.assertEqual((await admin.put(_APPLY, json={"expectedGeneration": None})).status_code, 200)

                # Hold the same durable singleton lock as acceptance. A control change
                # cannot report success until an in-flight lock holder has settled.
                async with self.engine.begin() as owner:
                    await owner.execute(text("SELECT singleton FROM ontology_push_inbox_state FOR UPDATE"))
                    self.assertEqual((await admin.put(_INTAKE, json=desired)).status_code, 503)
                    self.assertEqual((await admin.get(_ADMIN)).json()["intakeGeneration"], 0)

                paused = await admin.put(_INTAKE, json=desired)
                self.assertEqual(paused.status_code, 200, paused.text)
                paused = paused.json()
                self.assertEqual((paused["state"], paused["intakeGeneration"], paused["recent"]),
                                 ("paused", 1, [accepted.json()]))
                self.assertIn("pausedAt", paused)
                self.assertEqual((await admin.put(_INTAKE, json=desired)).json()["detail"]["code"],
                                 "intake_generation_changed")
                self.assertEqual((await admin.put(_INTAKE, json={**desired, "expectedGeneration": 1})).json(), paused)

                async with self.engine.connect() as conn:
                    clock = (await conn.execute(text(
                        "SELECT accepted_count, window_epoch, window_accepted FROM ontology_push_inbox_state"))).one()
                with patch.object(settings, "PUSH_INBOX_SANDBOX_INTAKE_CONTROL_ENABLED", False):
                    status = (await admin.get(_ADMIN)).json()
                    self.assertEqual(status["state"], "paused")
                    self.assertNotIn("intakeControlAvailable", status)
                    self.assertEqual((await producer.post(PATH, headers=headers(self.key, _BODY), content=_BODY)).status_code, 503)
                    stale = headers(self.key, _BODY, identity=request_id, stamp=int(time.time())-301)
                    self.assertEqual((await producer.post(PATH, headers=stale, content=_BODY)).status_code, 401)
                    replay = await producer.post(PATH, headers=headers(self.key, _BODY, identity=request_id), content=_BODY)
                    self.assertEqual(replay.json(), accepted.json())
                    changed = _BODY.replace(b"fixture-a", b"fixture-b")
                    self.assertEqual((await producer.post(PATH, headers=headers(self.key, changed, identity=request_id),
                                                          content=changed)).status_code, 409)
                # The older protocol writes receipt + counters together. Ignoring the
                # new fields still rolls that whole transaction back while paused.
                with self.assertRaises(DBAPIError):
                    async with self.engine.begin() as conn:
                        await conn.execute(text("SELECT singleton FROM ontology_push_inbox_state FOR UPDATE"))
                        await conn.execute(text("""
                            INSERT INTO ontology_push_inbox_receipts
                            (id,request_id,ordinal,body_digest,schema_version,entity_id,fixture_value,accepted_at)
                            VALUES (gen_random_uuid(),gen_random_uuid(),2,repeat('a',64),1,1,'fixture-legacy',clock_timestamp())
                        """))
                        await conn.execute(text("UPDATE ontology_push_inbox_state SET accepted_count=2"))
                async with self.engine.connect() as conn:
                    self.assertEqual(await conn.scalar(text("SELECT count(*) FROM ontology_push_inbox_receipts")), 1)
                    self.assertEqual((await conn.execute(text(
                        "SELECT accepted_count, window_epoch, window_accepted FROM ontology_push_inbox_state"))).one(), clock)

                applied = await admin.post(_APPLY + "/runs", json={
                    "requestId": str(uuid4()), "expectedGeneration": 1,
                    "expectedAppliedThrough": "0", "expectedAcceptedThrough": "1",
                })
                self.assertEqual(applied.json()["eventsApplied"], 1)
                self.assertEqual((await admin.get(_ADMIN)).json()["state"], "paused")
                resumed = await admin.put(_INTAKE, json={"expectedGeneration": 1, "paused": False})
                self.assertEqual((resumed.json()["state"], resumed.json()["intakeGeneration"]), ("ready", 2))
                self.assertNotIn("pausedAt", resumed.json())
                next_receipt = await producer.post(PATH, headers=headers(self.key, _BODY), content=_BODY)
                self.assertEqual(next_receipt.json()["ordinal"], "2")

        with self.assertRaisesRegex(RuntimeError, "Cannot remove a reviewed"):
            async with self.engine.begin() as conn:
                await conn.run_sync(ddl, migration.downgrade)
        async with self.engine.connect() as conn:
            self.assertEqual(await conn.scalar(text("SELECT intake_generation FROM ontology_push_inbox_state")), 2)

    async def _prepare_apply_fixture(self):
        migration = importlib.import_module("app.web.alembic.versions.af02d96bc517_ontology_push_inbox_apply")

        def provision(conn):
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()

        async with self.engine.begin() as conn:
            await conn.run_sync(provision)
            await conn.execute(text("CREATE SCHEMA ts_push_inbox_sandbox"))
            await conn.execute(text("""
                CREATE TABLE ts_push_inbox_sandbox.applied_fixture(
                    ordinal bigint PRIMARY KEY, acceptance_id uuid NOT NULL, entity_id bigint NOT NULL,
                    fixture_value text NOT NULL
                )
            """))

        async def cleanup():
            async with self.engine.begin() as conn:
                await conn.execute(text("DROP TABLE ontology_push_inbox_apply_receipts, ontology_push_inbox_apply_binding"))
                await conn.execute(text("DROP SCHEMA ts_push_inbox_sandbox CASCADE"))

        self.addAsyncCleanup(cleanup)

    async def test_reviewed_apply_preserves_each_event_and_honest_empty_check_with_exact_replay(self):
        await self._prepare_apply_fixture()

        def request(start, end, identity=None):
            return {"requestId": str(identity or uuid4()), "expectedGeneration": 1,
                    "expectedAppliedThrough": str(start), "expectedAcceptedThrough": str(end)}

        async def accept(producer, suffix):
            body = _BODY.replace(b"fixture-a", ("fixture-" + suffix).encode())
            response = await producer.post(PATH, headers=headers(self.key, body), content=body)
            self.assertEqual(response.status_code, 201, response.text)
            return response.json()

        with patch.object(settings, "PUSH_INBOX_SANDBOX_APPLY_ENABLED", True):
            async with self._producer() as producer, api(True, self._admin_session) as admin:
                async with api(False, self._admin_session) as reader:
                    self.assertEqual((await reader.get(_APPLY)).status_code, 403)
                initial = (await admin.get(_APPLY)).json()
                self.assertEqual(initial["state"], "unreviewed", initial)
                self.assertEqual((await admin.post(_APPLY + "/runs", json=request(0, 0))).status_code, 409)
                armed = await admin.put(_APPLY, json={"expectedGeneration": None})
                self.assertEqual(armed.status_code, 200, armed.text)
                self.assertEqual(armed.json()["state"], "ready")
                self.assertEqual((await admin.put(_APPLY, json={"expectedGeneration": None})).status_code, 409)
                check = request(0, 0)
                empty = await admin.post(_APPLY + "/runs", json=check)
                self.assertEqual(empty.status_code, 200, empty.text)
                self.assertEqual(empty.json()["status"], "no_new_data")
                self.assertIsNone(empty.json()["appliedAt"])
                self.assertEqual(empty.json()["eventsApplied"], 0)

                once, twice = await accept(producer, "first"), await accept(producer, "second")
                self.assertEqual((await admin.get(_APPLY)).json()["acceptedThrough"], "2")
                await accept(producer, "out-of-range")
                self.assertEqual((await admin.post(_APPLY + "/runs", json=check)).json(), empty.json())
                self.assertEqual((await admin.post(_APPLY + "/runs", json=request(0, 0))).status_code, 409)
                self.assertEqual((await admin.post(_APPLY + "/runs", json={**check, "expectedAcceptedThrough": "2"})).status_code,
                                 409)

                async with self.engine.begin() as conn:
                    # Only the apply result ledger is faulted, after actual target inserts.
                    await conn.execute(text("""
                        CREATE FUNCTION ts_push_inbox_sandbox.reject_apply_result() RETURNS trigger LANGUAGE plpgsql AS $$
                        BEGIN RAISE EXCEPTION 'test-only apply receipt fault'; END; $$
                    """))
                    await conn.execute(text("""
                        CREATE TRIGGER reject_apply_result BEFORE INSERT ON ontology_push_inbox_apply_receipts
                        FOR EACH ROW EXECUTE FUNCTION ts_push_inbox_sandbox.reject_apply_result()
                    """))
                original = request(0, 2)
                failed = await admin.post(_APPLY + "/runs", json=original)
                self.assertEqual(failed.status_code, 503, failed.text)
                self.assertEqual((await admin.get(_APPLY)).json()["appliedThrough"], "0")
                async with self.engine.begin() as conn:
                    self.assertEqual(await conn.scalar(text("SELECT count(*) FROM ts_push_inbox_sandbox.applied_fixture")), 0)
                    await conn.execute(text("DROP TRIGGER reject_apply_result ON ontology_push_inbox_apply_receipts"))
                applied = await admin.post(_APPLY + "/runs", json=original)
                self.assertEqual(applied.status_code, 200, applied.text)
                self.assertEqual((applied.json()["status"], applied.json()["eventsApplied"]), ("applied", 2))
                self.assertEqual(applied.json()["reviewedAcceptedThrough"], "2")
                async with self.engine.begin() as conn:
                    items = (await conn.execute(text("""
                        SELECT ordinal, acceptance_id, entity_id, fixture_value
                        FROM ts_push_inbox_sandbox.applied_fixture ORDER BY ordinal
                    """))).all()
                    self.assertEqual([(o, str(a), entity, v) for o, a, entity, v in items], [
                        (1, once["id"], 2**63-1, "fixture-first"), (2, twice["id"], 2**63-1, "fixture-second"),
                    ])
                    await conn.execute(text("TRUNCATE ts_push_inbox_sandbox.applied_fixture"))
                self.assertEqual((await admin.post(_APPLY + "/runs", json=original)).json(), applied.json())
                self.assertEqual((await admin.post(_APPLY + "/runs", json=request(2, 3))).status_code, 409)
                blocked = (await admin.get(_APPLY)).json()
                self.assertEqual((blocked["state"], blocked["canReplay"]), ("blocked", True))
                self.assertNotIn("fixture-first", str(blocked))

    async def test_event_path_exact_receipt_and_read_only_opaque_failures(self):
        await self._prepare_apply_fixture()
        route = _ADMIN + "/event-path"
        with (patch.object(settings, "PUSH_INBOX_SANDBOX_APPLY_ENABLED", True),
              patch.object(settings, "PUSH_INBOX_SANDBOX_EVENT_PATH_ENABLED", True)):
            async with self._producer() as producer, api(True, self._admin_session) as admin:
                original = await producer.post(PATH, headers=headers(self.key, _BODY), content=_BODY)
                self.assertEqual(original.status_code, 201, original.text)
                receipt = original.json()
                body = {"receiptId": receipt["id"]}
                self.assertTrue((await admin.get(_ADMIN)).json()["eventPathAvailable"])
                absent = await admin.post(route, json=body)
                self.assertEqual(absent.status_code, 404, absent.text)
                self.assertEqual(absent.headers["Cache-Control"], "private, no-store")
                with patch.object(settings, "PUSH_INBOX_SANDBOX_EVENT_PATH_ENABLED", False):
                    self.assertNotIn("eventPathAvailable", (await admin.get(_ADMIN)).json())
                    self.assertEqual((await admin.post(route, json=body)).json(), absent.json())
                async with api(False, self._admin_session) as reader:
                    self.assertEqual((await reader.post(route, json=body)).status_code, 403)
                self.assertEqual((await admin.put(_APPLY, json={"expectedGeneration": None})).status_code, 200)
                pending = await admin.post(route, json=body)
                self.assertEqual(pending.status_code, 200, pending.text)
                self.assertEqual(pending.headers["Cache-Control"], "private, no-store")
                self.assertEqual(set(pending.json()), {"status", "receiptId", "ordinal", "acceptedAt", "observedAt"})
                self.assertEqual(pending.json()["status"], "accepted_not_applied_at_observation")
                self.assertEqual(pending.json()["ordinal"], "1")
                contradictory_id = uuid4()
                async with self.engine.begin() as conn:
                    await conn.execute(text("""
                        INSERT INTO ontology_push_inbox_apply_receipts
                        (id,request_id,database_marker,generation,outcome,from_exclusive,through_inclusive,
                         reviewed_accepted_through,events_applied,recorded_by,recorded_at)
                        VALUES (:id,:request,:marker,1,'applied',0,1,1,1,:actor,clock_timestamp())
                    """), {"id": contradictory_id, "request": uuid4(), "marker": uuid4(), "actor": uuid4()})
                self.assertEqual((await admin.post(route, json=body)).json(), absent.json())
                async with self.engine.begin() as conn:
                    await conn.execute(text("DELETE FROM ontology_push_inbox_apply_receipts WHERE id=:id"),
                                       {"id": contradictory_id})
                self.assertEqual(
                    (await admin.post(route, json={"receiptId": str(uuid4())})).json(), absent.json(),
                )
                self.assertEqual((await admin.post(route + "?x=1", json=body)).json(), absent.json())
                for malformed in (
                    {**body, "extra": "ignored"}, {"receiptId": receipt["id"].upper()},
                ):
                    self.assertEqual((await admin.post(route, json=malformed)).json(), absent.json())
                duplicated = f'{{"receiptId":"{receipt["id"]}","receiptId":"{receipt["id"]}"}}'
                self.assertEqual((await admin.post(route, content=duplicated,
                                                   headers={"Content-Type": "application/json"})).json(), absent.json())
                applied = await admin.post(_APPLY + "/runs", json={
                    "requestId": str(uuid4()), "expectedGeneration": 1,
                    "expectedAppliedThrough": "0", "expectedAcceptedThrough": "1",
                })
                self.assertEqual(applied.status_code, 200, applied.text)
                with (patch.object(settings, "PUSH_INBOX_SANDBOX_APPLY_ENABLED", False),
                      patch.object(settings, "PUSH_INBOX_SANDBOX_PUBLIC_KEY", None)):
                    result = await admin.post(route, json=body)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()["status"], "applied_recorded")
                self.assertEqual(result.json()["applyReceiptId"], applied.json()["id"])
                self.assertEqual(result.json()["acceptedAt"], receipt["acceptedAt"])
                async with self.engine.begin() as conn:
                    await conn.execute(text("ALTER INDEX uq_push_apply_ordinal RENAME TO renamed_apply_ordinal"))
                self.assertEqual((await admin.post(route, json=body)).json(), absent.json())
                async with self.engine.begin() as conn:
                    await conn.execute(text("ALTER INDEX renamed_apply_ordinal RENAME TO uq_push_apply_ordinal"))
                self.assertNotIn("fixture-a", result.text)
                async with self.engine.begin() as conn:
                    await conn.execute(text("UPDATE ts_push_inbox_sandbox.applied_fixture SET acceptance_id=:wrong"),
                                       {"wrong": uuid4()})
                self.assertEqual((await admin.post(route, json=body)).json(), absent.json())
                async with self.engine.begin() as conn:
                    await conn.execute(text("UPDATE ts_push_inbox_sandbox.applied_fixture SET acceptance_id=:right"),
                                       {"right": receipt["id"]})
                    await conn.execute(text("UPDATE ontology_push_inbox_apply_receipts SET database_marker=:wrong"),
                                       {"wrong": uuid4()})
                self.assertEqual((await admin.post(route, json=body)).json(), absent.json())
                async with self.engine.begin() as conn:
                    await conn.execute(text("UPDATE ontology_push_inbox_apply_receipts SET database_marker=:right"),
                                       {"right": self.marker})
                    await conn.execute(text("TRUNCATE ts_push_inbox_sandbox.applied_fixture"))
                self.assertEqual((await admin.post(route, json=body)).json(), absent.json())

    async def test_reviewed_apply_caps_exact_batch_detects_missing_ordinals_and_reconciles_without_target(self):
        from app.web.services.push_inbox_apply import apply_push_range

        await self._prepare_apply_fixture()
        async with self.engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO ontology_push_inbox_receipts
                    (id,request_id,ordinal,body_digest,schema_version,entity_id,fixture_value,accepted_at)
                SELECT gen_random_uuid(),gen_random_uuid(),n,repeat('a',64),1,n,'fixture-reviewed',clock_timestamp()
                FROM generate_series(1,53) AS n
            """))
            await conn.execute(text("UPDATE ontology_push_inbox_state SET accepted_count=53"))

        async def lose_ack(session, request, actor):
            await apply_push_range(session, request, actor)
            raise OSError("fixture-only lost acknowledgement after the transaction committed")

        with patch.object(settings, "PUSH_INBOX_SANDBOX_APPLY_ENABLED", True):
            async with api(True, self._admin_session) as admin:
                reviewed = await admin.put(_APPLY, json={"expectedGeneration": None})
                self.assertEqual(reviewed.status_code, 200, reviewed.text)
                body = {"requestId": str(uuid4()), "expectedGeneration": 1,
                        "expectedAppliedThrough": "0", "expectedAcceptedThrough": "53"}
                with patch("app.web.api.routes.etl.apply_push_range", side_effect=lose_ack):
                    pending = await admin.post(_APPLY + "/runs", json=body)
                self.assertEqual(pending.status_code, 503, pending.text)
                preserved = (await admin.get(_APPLY)).json()
                first, = preserved["recent"]
                self.assertEqual((first["eventsApplied"], first["throughInclusive"], first["hasMoreReviewed"]),
                                 (50, "50", True))
                self.assertEqual((await admin.post(_APPLY + "/runs", json=body)).json(), first)
                async with self.engine.begin() as conn:
                    await conn.execute(text("DELETE FROM ontology_push_inbox_receipts WHERE ordinal=52"))
                gap = await admin.post(_APPLY + "/runs", json={**body, "requestId": str(uuid4()),
                                                               "expectedAppliedThrough": "50"})
                self.assertEqual(gap.status_code, 409, gap.text)
                self.assertEqual((await admin.get(_APPLY)).json()["appliedThrough"], "50")
                async with self.engine.begin() as conn:
                    self.assertEqual(await conn.scalar(text("SELECT count(*) FROM ts_push_inbox_sandbox.applied_fixture")), 50)
                    await conn.execute(text("TRUNCATE ontology_push_inbox_receipts"))
                archived = (await admin.get(_APPLY)).json()
                self.assertEqual((archived["state"], archived["canReplay"]), ("blocked", True))
                self.assertEqual((await admin.post(_APPLY + "/runs", json=body)).json(), first)
                self.assertEqual((await admin.put(_APPLY, json={"expectedGeneration": None})).status_code, 409)

    async def _remove_fault_fn(self):
        async with self.engine.begin() as conn:
            await conn.execute(text("DROP FUNCTION IF EXISTS public.fail_synthetic_inbox() CASCADE"))
