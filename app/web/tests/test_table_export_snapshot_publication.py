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

                    async def run_publish(request, previous, payload, deployed, revision=None):
                        return await service.publish_snapshot(
                            request, previous, payload,
                            expected_config_revision=revision or deployed.config_revision, sender_port=port+1,
                            sender_database="test_other_sender", sender_username="test_other_sender",
                            sender_marker=uuid4(),
                        )

                    async def publish(request, previous, payload=rows, *, deployed=profile, revision=None):
                        with (
                            patch.object(service, "configured_snapshot_publication_profile", return_value=deployed),
                            patch.object(provider, "configured_snapshot_publication_profile", return_value=deployed),
                        ):
                            return await run_publish(request, previous, payload, deployed, revision)

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

                    @asynccontextmanager
                    async def lost_ack(*args, **kwargs):
                        async with original(*args, **kwargs) as conn:
                            yield conn
                            raise OSError("fixture after actual COMMIT")

                    second_id = uuid4()
                    with patch.object(service, "snapshot_publication_connection", new=lost_ack):
                        with self.assertRaises(service.SnapshotPublicationUnavailable):
                            await publish(second_id, 1, rows[:1])
                    second = await publish(second_id, 1, rows[:1])
                    self.assertEqual(second.generation, 2)
                    self.assertEqual(await publish(request, 0, list(reversed(rows))), first)
                    self.assertEqual(await data.fetchval(f"SELECT current_generation FROM {_SCHEMA}.state"), 2)
                    for predecessor in range(2, 16):
                        self.assertEqual((await publish(uuid4(), predecessor, rows[:1])).generation, predecessor+1)
                    with self.assertRaises(service.SnapshotPublicationConflict):
                        await publish(uuid4(), 16)
                    self.assertEqual(await publish(request, 0), first)
                    self.assertEqual(await data.fetchval(f"SELECT count(*) FROM {_SCHEMA}.generations"), 16)
                    await data.execute(f"UPDATE {_SCHEMA}.records SET caption='tampered' WHERE generation=1")
                    with self.assertRaises(service.SnapshotPublicationUnavailable):
                        await publish(request, 0)
                    self.assertEqual(await data.fetchval(f"SELECT current_generation FROM {_SCHEMA}.state"), 16)
            finally:
                await admin.execute(f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")
                await admin.execute(f"DROP ROLE {role}")
