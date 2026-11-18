"""Actual head promotion, reconciliation, and rollback in the disposable receiver only."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import asyncpg

from app.web.services import table_export_snapshot_publication as service
from app.web.services import table_export_snapshot_publication_receiver as provider
from app.web.tests.test_table_export_snapshot_receiver import closing_connection

_SCHEMA = provider.SNAPSHOT_PUBLICATION_SCHEMA


class SnapshotPublicationManifestTests(unittest.TestCase):
    def test_bounded_canonical_manifest(self):
        Row = service.SnapshotPublicationRow
        rows = [Row(id="9223372036854775807", caption=None), Row(id="-9223372036854775808", caption="甲")]
        self.assertEqual(service._normalize(rows), [(-(2**63), "甲"), (2**63 - 1, None)])
        self.assertEqual(service._seal(service._normalize(rows)), service._seal(service._normalize(reversed(rows))))
        self.assertNotEqual(service._seal([(0, None)]), service._seal([(0, "")]))
        for manifest in (
            [], [Row(id="1", caption=None)] * 2, [Row(id="-0", caption=None)],
            [Row(id="9223372036854775808", caption=None)], [Row(id="01", caption=None)],
            [Row(id="1", caption="甲" * 2731)],
            [Row(id=str(i), caption="x" * 8185) for i in range(8)],
            [Row(id=str(i), caption=None) for i in range(101)],
        ):
            with self.assertRaises(ValueError):
                service._normalize(manifest)


@unittest.skipUnless(os.environ.get("ISOLATED_SNAPSHOT_RECEIVER_PORT"), "requires disposable local publication PG")
class SnapshotPublicationPGTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_role_atomic_promotion_concurrent_and_lost_ack(self):
        port = int(os.environ["ISOLATED_SNAPSHOT_RECEIVER_PORT"])
        admin_user = os.environ.get("ISOLATED_SNAPSHOT_RECEIVER_USER", "agent")
        admin_password = os.environ["ISOLATED_SNAPSHOT_RECEIVER_ADMIN_PASSWORD"]
        suffix = uuid4().hex[:8]
        database, role = "test_publication_" + suffix, "test_publisher_" + suffix
        password, marker = uuid4().hex, uuid4()
        common = {"host": "127.0.0.1", "port": port, "ssl": False}
        admin = await asyncpg.connect(**common, user=admin_user, password=admin_password, database="postgres")
        async with closing_connection(admin):
            await admin.execute(f"CREATE ROLE {role} LOGIN NOINHERIT PASSWORD '{password}'")
            try:
                await admin.execute(f"CREATE DATABASE {database}")
                data = await asyncpg.connect(**common, user=admin_user, password=admin_password, database=database)
                async with closing_connection(data):
                    await data.execute(f"""
                        REVOKE ALL ON DATABASE {database} FROM PUBLIC;
                        GRANT CONNECT ON DATABASE {database} TO {role};
                        CREATE SCHEMA {_SCHEMA};
                        CREATE TABLE {_SCHEMA}.state(
                            singleton boolean PRIMARY KEY CHECK(singleton), marker uuid NOT NULL,
                            current_generation bigint NOT NULL CHECK(current_generation >= 0)
                        );
                        CREATE TABLE {_SCHEMA}.generations(
                            generation bigint PRIMARY KEY, previous_generation bigint NOT NULL,
                            request_id uuid NOT NULL UNIQUE,payload_hash text NOT NULL,row_count integer NOT NULL,
                            recorded_at timestamptz NOT NULL
                        );
                        CREATE TABLE {_SCHEMA}.records(
                            generation bigint NOT NULL,id bigint NOT NULL,caption text NULL,
                            PRIMARY KEY(generation,id)
                        );
                        GRANT USAGE ON SCHEMA {_SCHEMA} TO {role};
                        GRANT SELECT, MAINTAIN ON ALL TABLES IN SCHEMA {_SCHEMA} TO {role};
                        GRANT UPDATE(current_generation) ON {_SCHEMA}.state TO {role};
                        GRANT INSERT ON {_SCHEMA}.generations, {_SCHEMA}.records TO {role};
                    """)
                    await data.execute(f"INSERT INTO {_SCHEMA}.state VALUES (true,$1,0)", marker)
                    proof = await provider.compute_snapshot_publication_proof(data)
                    profile = provider.SnapshotPublicationProfile(
                        uuid4(), "127.0.0.1", port, database, role, password, marker, proof,
                    )
                    rows = [
                        service.SnapshotPublicationRow(id="-9223372036854775808", caption="甲"),
                        service.SnapshotPublicationRow(id="9223372036854775807", caption=None),
                    ]

                    async def run_publish(request, previous, payload, deployed, revision=None, **extra):
                        return await service.publish_snapshot(
                            request, previous, payload,
                            expected_config_revision=revision or deployed.config_revision, sender_port=port+1,
                            sender_database="test_other_sender", sender_username="test_other_sender",
                            sender_marker=uuid4(), **extra,
                        )

                    async def publish(request, previous, payload=rows, *, deployed=profile, revision=None, **extra):
                        with (
                            patch.object(service, "configured_snapshot_publication_profile", return_value=deployed),
                            patch.object(provider, "configured_snapshot_publication_profile", return_value=deployed),
                        ):
                            return await run_publish(request, previous, payload, deployed, revision, **extra)

                    async def reconcile(request, previous, payload=rows, *, deployed=profile,
                                        revision=None, digest=None):
                        with (
                            patch.object(service, "configured_snapshot_publication_profile", return_value=deployed),
                            patch.object(provider, "configured_snapshot_publication_profile", return_value=deployed),
                        ):
                            return await service.reconcile_snapshot(
                                request, previous, service._seal(service._normalize(payload)) if digest is None else digest,
                                expected_config_revision=revision or deployed.config_revision, sender_port=port+1,
                                sender_database="test_other_sender", sender_username="test_other_sender",
                                sender_marker=uuid4(),
                            )

                    async def head(*, revision=None):
                        with (
                            patch.object(service, "configured_snapshot_publication_profile", return_value=profile),
                            patch.object(provider, "configured_snapshot_publication_profile", return_value=profile),
                        ):
                            return await service.read_snapshot_publication_head(
                                expected_config_revision=revision or profile.config_revision,
                                sender_port=port+1, sender_database="test_other_sender",
                                sender_username="test_other_sender", sender_marker=uuid4(),
                            )

                    self.assertEqual((await head()).model_dump(),
                                     {"generation": 0, "row_count": None, "recorded_at": None})

                    for revoke_at in (1, 2, 3):
                        checkpoint = 0

                        def grant_revoked():
                            nonlocal checkpoint
                            checkpoint += 1
                            if checkpoint == revoke_at:
                                raise RuntimeError("isolated fixture revoked web approval")

                        with self.assertRaises(service.SnapshotPublicationUnavailable):
                            await publish(uuid4(), 0, web_approval=grant_revoked)
                        self.assertEqual(checkpoint, revoke_at)
                        self.assertEqual(await data.fetchval(f"SELECT current_generation FROM {_SCHEMA}.state"), 0)
                        self.assertEqual(await data.fetchval(f"SELECT count(*) FROM {_SCHEMA}.generations"), 0)
                        self.assertEqual(await data.fetchval(f"SELECT count(*) FROM {_SCHEMA}.records"), 0)
                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await head(revision=uuid4())
                    await data.execute(f"INSERT INTO {_SCHEMA}.records VALUES (1,1,NULL)")
                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await head()
                    await data.execute(f"DELETE FROM {_SCHEMA}.records")
                    await data.execute(f"""
                        INSERT INTO {_SCHEMA}.generations VALUES (1,0,gen_random_uuid(),repeat('a',64),1,clock_timestamp())
                    """)
                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await head()
                    await data.execute(f"DELETE FROM {_SCHEMA}.generations")

                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await publish(uuid4(), 0, revision=uuid4())
                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await publish(uuid4(), 0, deployed=replace(profile, marker=uuid4()))
                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await publish(uuid4(), 0, deployed=replace(profile, proof_sha256="f"*64))
                    request = uuid4()
                    # A real constraint error after the header INSERT rolls everything back.
                    actual_connection = service.snapshot_publication_connection

                    class FaultAtRows:
                        def __init__(self, connection):
                            self.connection = connection

                        def __getattr__(self, key):
                            return getattr(self.connection, key)

                        async def executemany(self, sql, args):
                            await self.connection.execute(
                                f"INSERT INTO {_SCHEMA}.records (generation, id) VALUES (1, NULL)"
                            )

                    @asynccontextmanager
                    async def sql_fault(*args, **kwargs):
                        async with actual_connection(*args, **kwargs) as conn:
                            yield FaultAtRows(conn)

                    with patch.object(service, "snapshot_publication_connection", new=sql_fault):
                        with self.assertRaises(service.SnapshotPublicationUnavailable):
                            await publish(request, 0)
                    self.assertEqual(await data.fetchval(f"SELECT current_generation FROM {_SCHEMA}.state"), 0)
                    self.assertEqual(await data.fetchval(f"SELECT count(*) FROM {_SCHEMA}.generations"), 0)
                    self.assertEqual(await data.fetchval(f"SELECT count(*) FROM {_SCHEMA}.records"), 0)

                    with (
                        patch.object(service, "configured_snapshot_publication_profile", return_value=profile),
                        patch.object(provider, "configured_snapshot_publication_profile", return_value=profile),
                    ):
                        attempts = await asyncio.gather(
                            run_publish(request, 0, rows, profile), run_publish(request, 0, rows, profile),
                            return_exceptions=True,
                        )
                    committed = [result for result in attempts if isinstance(result, service.SnapshotPublicationResult)]
                    self.assertTrue(committed, attempts)
                    first = committed[0]
                    self.assertTrue(all(result == first or isinstance(result, service.SnapshotPublicationUnavailable)
                                        for result in attempts), attempts)
                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await publish(request, 0, web_approval=lambda: None)
                    self.assertEqual(await publish(request, 0), first)
                    self.assertEqual(await data.fetchval(f"SELECT count(*) FROM {_SCHEMA}.generations"), 1)
                    self.assertEqual((first.generation, first.previous_generation, first.row_count), (1, 0, 2))
                    with self.assertRaises(service.SnapshotPublicationConflict):
                        await publish(request, 1)
                    with self.assertRaises(service.SnapshotPublicationConflict):
                        await publish(request, 0, rows[:1])
                    with self.assertRaises(service.SnapshotPublicationConflict):
                        await publish(uuid4(), 0)

                    original = service.snapshot_publication_connection

                    second_id = uuid4()
                    uncommitted, release = asyncio.Event(), asyncio.Event()

                    class HoldBeforeCommit:
                        def __init__(self, connection):
                            self.connection = connection

                        def __getattr__(self, key):
                            return getattr(self.connection, key)

                        async def execute(self, sql, *args):
                            result = await self.connection.execute(sql, *args)
                            if sql.lstrip().startswith(f"UPDATE ONLY {_SCHEMA}.state"):
                                uncommitted.set()
                                await release.wait()
                            return result

                    @asynccontextmanager
                    async def hold_then_lose_writer_ack(*args, **kwargs):
                        async with original(*args, **kwargs) as conn:
                            yield HoldBeforeCommit(conn)
                            if asyncio.current_task() is writer:
                                raise OSError("fixture after actual COMMIT")

                    with patch.object(service, "snapshot_publication_connection", new=hold_then_lose_writer_ack):
                        with (
                            patch.object(service, "configured_snapshot_publication_profile", return_value=profile),
                            patch.object(provider, "configured_snapshot_publication_profile", return_value=profile),
                        ):
                            writer = asyncio.create_task(run_publish(second_id, 1, rows[:1], profile))
                            try:
                                await asyncio.wait_for(uncommitted.wait(), 2)
                                with self.assertRaises(service.SnapshotPublicationUnavailable):
                                    await reconcile(second_id, 1, rows[:1])
                                self.assertEqual(await reconcile(request, 0), first)
                                self.assertEqual((await head()).generation, 1)
                            finally:
                                release.set()
                                with self.assertRaises(service.SnapshotPublicationUnavailable):
                                    await writer
                    second = await reconcile(second_id, 1, rows[:1])
                    self.assertEqual(await publish(second_id, 1, rows[:1]), second)
                    for request_to_verify in (
                        reconcile(uuid4(), 1), reconcile(second_id, 0, rows[:1]), reconcile(second_id, 1),
                        reconcile(second_id, 1, rows[:1], digest="A"*64),
                        reconcile(second_id, 1, rows[:1], revision=uuid4()),
                        reconcile(second_id, 1, rows[:1], deployed=replace(profile, marker=uuid4())),
                    ):
                        with self.assertRaisesRegex(service.SnapshotPublicationUnavailable, "unconfirmed"):
                            await request_to_verify
                    async with data.transaction():
                        await data.execute(f"LOCK TABLE {_SCHEMA}.records IN ACCESS EXCLUSIVE MODE")
                        with self.assertRaises(service.SnapshotPublicationUnavailable):
                            await reconcile(second_id, 1, rows[:1])
                        with self.assertRaises(service.SnapshotPublicationUnavailable):
                            await head()
                    self.assertEqual(second.generation, 2)
                    self.assertEqual((await head()).model_dump(),
                                     {"generation": 2, "row_count": second.row_count, "recorded_at": second.recorded_at})
                    self.assertEqual(await publish(request, 0, list(reversed(rows))), first)
                    self.assertEqual(await data.fetchval(f"SELECT current_generation FROM {_SCHEMA}.state"), 2)
                    for predecessor in range(2, 16):
                        self.assertEqual((await publish(uuid4(), predecessor, rows[:1])).generation, predecessor+1)
                    with self.assertRaises(service.SnapshotPublicationConflict):
                        await publish(uuid4(), 16)
                    self.assertEqual(await publish(request, 0), first)
                    self.assertEqual(await data.fetchval(f"SELECT count(*) FROM {_SCHEMA}.generations"), 16)
                    self.assertEqual(await reconcile(request, 0), first)
                    await data.execute(f"UPDATE {_SCHEMA}.records SET caption='tampered' WHERE generation=1")
                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await reconcile(request, 0)
                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await publish(request, 0)
                    self.assertEqual((await head()).generation, 16)  # historical values aren't reported or read
                    await data.execute(f"UPDATE {_SCHEMA}.generations SET previous_generation=0 WHERE generation=2")
                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await head()
                    await data.execute(f"UPDATE {_SCHEMA}.generations SET previous_generation=1 WHERE generation=2")
                    await data.execute(f"UPDATE {_SCHEMA}.generations SET recorded_at='infinity' WHERE generation=2")
                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await head()
                    await data.execute(f"UPDATE {_SCHEMA}.generations SET recorded_at=$1 WHERE generation=2", second.recorded_at)
                    await data.execute(f"UPDATE {_SCHEMA}.records SET caption='tampered' WHERE generation=16")
                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await head()
                    await data.execute(f"UPDATE {_SCHEMA}.records SET caption='甲' WHERE generation=16")
                    self.assertEqual((await head()).generation, 16)
                    for orphan in (0, 17):
                        await data.execute(f"INSERT INTO {_SCHEMA}.records VALUES ($1,1,NULL)", orphan)
                        with self.assertRaises(service.SnapshotPublicationUnavailable):
                            await head()
                        await data.execute(f"DELETE FROM {_SCHEMA}.records WHERE generation=$1", orphan)
                    await data.execute(f"DELETE FROM {_SCHEMA}.generations WHERE generation=2")
                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await head()
                    await data.execute(f"REINDEX INDEX {_SCHEMA}.records_pkey")
                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await reconcile(second_id, 1, rows[:1])
                    self.assertEqual(await data.fetchval(f"SELECT current_generation FROM {_SCHEMA}.state"), 16)
            finally:
                await admin.execute(f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")
                await admin.execute(f"DROP ROLE {role}")
