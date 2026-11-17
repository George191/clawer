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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.web.core.config import settings
from app.web.push_inbox_ingress import PATH, _payload, create_ingress, signing_message
from app.web.services.push_inbox import local_inbox_database, strict_base64
from app.web.tests.test_transfer_plans import api

_DB = "ontology_push_inbox_regression"
_URL = os.getenv("ONTOLOGY_PUSH_INBOX_TEST_URL")
_BODY = b'{"schemaVersion":1,"entityId":"9223372036854775807","value":"fixture-a"}'
_ADMIN = "/etl/push-inbox-sandbox"


def headers(key, body, *, identity=None, stamp=None):
    identity = identity or uuid4()
    stamp = str(int(time.time())) if stamp is None else str(stamp)
    proof = key.sign(signing_message(stamp, identity, sha256(body).hexdigest()))
    return {"X-Inbox-Request-Id": str(identity), "X-Inbox-Timestamp": stamp,
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

    async def _remove_fault_fn(self):
        async with self.engine.begin() as conn:
            await conn.execute(text("DROP FUNCTION IF EXISTS public.fail_synthetic_inbox() CASCADE"))
