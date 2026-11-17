"""Restricted receiver proofs; opt-in tests use an isolated synthetic PG cluster."""

import os
import unittest
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import asyncpg
from pydantic import SecretStr

from app.web.services import table_export_snapshot_receiver as receiver

_SCHEMA = "ts_table_export_snapshot_receiver"


@asynccontextmanager
async def closing_connection(connection):
    try:
        yield connection
    finally:
        await connection.close()


def profile_settings(profile):
    return receiver.settings.model_copy(update={
        "ENVIRONMENT": "local",
        "TABLE_EXPORT_SANDBOX_ENABLED": True,
        "TABLE_EXPORT_SNAPSHOT_PREVIEW_ENABLED": True,
        "TABLE_EXPORT_SNAPSHOT_PREVIEW_CONFIG_REVISION": profile.deployment_revision,
        "TABLE_EXPORT_SNAPSHOT_PREVIEW_HOST": profile.host,
        "TABLE_EXPORT_SNAPSHOT_PREVIEW_PORT": profile.port,
        "TABLE_EXPORT_SNAPSHOT_PREVIEW_DATABASE": profile.database,
        "TABLE_EXPORT_SNAPSHOT_PREVIEW_USERNAME": profile.username,
        "TABLE_EXPORT_SNAPSHOT_PREVIEW_PASSWORD": SecretStr(profile.password),
        "TABLE_EXPORT_SNAPSHOT_PREVIEW_MARKER": profile.marker,
        "TABLE_EXPORT_SNAPSHOT_PREVIEW_PROOF_SHA256": profile.proof_sha256,
    })


class SnapshotReceiverNonDialTests(unittest.IsolatedAsyncioTestCase):
    async def test_alias_and_implicit_local_never_dial(self):
        profile = receiver.SnapshotReceiverProfile(
            uuid4(), "127.0.0.1", 56480, "test_snapshot_receiver", "test_snapshot_reader", "unit-dummy",
            uuid4(), "f" * 64,
        )
        with patch.object(receiver, "settings", profile_settings(profile)), patch.object(
            receiver.asyncpg, "connect", new_callable=AsyncMock,
        ) as dial:
            self.assertEqual(receiver.configured_snapshot_profile(), profile)
            for port, marker in ((profile.port, uuid4()), (56481, profile.marker), (0, uuid4())):
                with self.assertRaises(receiver.SnapshotReceiverUnavailable):
                    await receiver.read_snapshot_preview(
                        profile, sender_port=port, sender_marker=marker,
                        sender_database="sender_database", sender_username="sender_user",
                    )
            for database, username in ((profile.database, "sender_user"), ("sender_database", profile.username)):
                with self.assertRaises(receiver.SnapshotReceiverUnavailable):
                    await receiver.read_snapshot_preview(
                        profile, sender_port=56481, sender_marker=uuid4(),
                        sender_database=database, sender_username=username,
                    )
            self.assertNotEqual(profile.config_revision, profile.deployment_revision)
            self.assertNotEqual(profile.config_revision, replace(profile, marker=uuid4()).config_revision)
            self.assertNotEqual(profile.config_revision, replace(profile, proof_sha256="a" * 64).config_revision)
            self.assertEqual(profile.config_revision, replace(profile, password="rotated").config_revision)
            receiver.settings.TABLE_EXPORT_RECEIVER_PORT = profile.port
            self.assertIsNone(receiver.configured_snapshot_profile())
            receiver.settings.TABLE_EXPORT_RECEIVER_PORT = 5432
            receiver.settings.model_fields_set.remove("ENVIRONMENT")
            self.assertIsNone(receiver.configured_snapshot_profile())
            dial.assert_not_awaited()


@unittest.skipUnless(os.environ.get("ISOLATED_SNAPSHOT_RECEIVER_PORT"), "requires a disposable local PostgreSQL cluster")
class SnapshotReceiverPGTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_select_only_role_caps_locks_and_pins(self):
        port = int(os.environ["ISOLATED_SNAPSHOT_RECEIVER_PORT"])
        admin_name = os.environ.get("ISOLATED_SNAPSHOT_RECEIVER_USER", "agent")
        admin_password = os.environ["ISOLATED_SNAPSHOT_RECEIVER_ADMIN_PASSWORD"]
        suffix = uuid4().hex[:8]
        database = "test_snapshot_" + suffix
        role = "test_preview_" + suffix
        password = uuid4().hex
        marker = uuid4()
        admin = await asyncpg.connect(
            host="127.0.0.1", port=port, user=admin_name, password=admin_password, database="postgres", ssl=False,
        )
        async with closing_connection(admin):
            await admin.execute(f"CREATE ROLE {role} LOGIN NOINHERIT PASSWORD '{password}'")
            try:
                await admin.execute(f"CREATE DATABASE {database}")
                data = await asyncpg.connect(
                    host="127.0.0.1", port=port, user=admin_name, password=admin_password, database=database,
                    ssl=False, server_settings={"search_path": "pg_catalog"},
                )
                async with closing_connection(data):
                    await data.execute(f"""
                        REVOKE ALL ON DATABASE {database} FROM PUBLIC;
                        GRANT CONNECT ON DATABASE {database} TO {role};
                        CREATE SCHEMA {_SCHEMA};
                        CREATE TABLE {_SCHEMA}.identity(marker uuid PRIMARY KEY);
                        CREATE TABLE {_SCHEMA}.records(business_key bigint PRIMARY KEY, caption text);
                        GRANT USAGE ON SCHEMA {_SCHEMA} TO {role};
                        GRANT SELECT ON {_SCHEMA}.identity, {_SCHEMA}.records TO {role};
                    """)
                    await data.execute(f"INSERT INTO {_SCHEMA}.identity VALUES ($1)", marker)
                    await data.execute(
                        f"INSERT INTO {_SCHEMA}.records VALUES (9223372036854775807, '甲'),(-9223372036854775808, NULL)",
                    )
                    async with data.transaction():
                        await data.execute(f"LOCK TABLE ONLY {_SCHEMA}.identity, ONLY {_SCHEMA}.records IN ACCESS SHARE MODE")
                        proof = await receiver.compute_snapshot_relation_proof(data)
                    profile = receiver.SnapshotReceiverProfile(
                        uuid4(), "127.0.0.1", port, database, role, password, marker, proof,
                    )

                    async def read(selected=profile):
                        with patch.object(receiver, "settings", profile_settings(selected)):
                            return await receiver.read_snapshot_preview(
                                selected, sender_port=port + 1, sender_marker=uuid4(),
                                sender_database="sender_database", sender_username="sender_user",
                            )

                    async def rejected(selected=profile):
                        with self.assertRaises(receiver.SnapshotReceiverUnavailable):
                            await read(selected)

                    self.assertEqual(await read(), (
                        receiver.SnapshotReceiverRow(-9223372036854775808, None),
                        receiver.SnapshotReceiverRow(9223372036854775807, "甲"),
                    ))
                    await rejected(replace(profile, marker=uuid4()))
                    await rejected(replace(profile, proof_sha256="a" * 64))

                    async with data.transaction():
                        await data.execute(f"LOCK TABLE {_SCHEMA}.records IN ACCESS EXCLUSIVE MODE")
                        await rejected()
                    await data.execute(f"GRANT UPDATE (caption) ON {_SCHEMA}.records TO {role}")
                    await rejected()
                    await data.execute(f"REVOKE UPDATE (caption) ON {_SCHEMA}.records FROM {role}")
                    if data.get_server_version().major >= 17:
                        await data.execute(f"GRANT MAINTAIN ON {_SCHEMA}.records TO {role}")
                        await rejected()
                        await data.execute(f"REVOKE MAINTAIN ON {_SCHEMA}.records FROM {role}")
                    await data.execute(f"ALTER TABLE {_SCHEMA}.records ENABLE ROW LEVEL SECURITY")
                    await rejected()
                    await data.execute(f"ALTER TABLE {_SCHEMA}.records DISABLE ROW LEVEL SECURITY")
                    await data.execute(f"GRANT TEMP ON DATABASE {database} TO {role}")
                    await rejected()
                    await data.execute(f"REVOKE TEMP ON DATABASE {database} FROM {role}")
                    await data.execute(f"INSERT INTO {_SCHEMA}.identity VALUES ($1)", uuid4())
                    await rejected()
                    await data.execute(f"DELETE FROM {_SCHEMA}.identity WHERE marker <> $1", marker)

                    await data.execute(f"DELETE FROM {_SCHEMA}.records")
                    await data.execute(f"""
                        INSERT INTO {_SCHEMA}.records
                        SELECT i, repeat('x', 8184) FROM generate_series(1,8) i;
                    """)
                    self.assertEqual(len(await read()), 8)  # Exactly 65,536 bytes, including keys.
                    await data.execute(f"UPDATE {_SCHEMA}.records SET caption = caption || 'x' WHERE business_key = 1")
                    await rejected()
                    await data.execute(f"DELETE FROM {_SCHEMA}.records")
                    await data.execute(f"INSERT INTO {_SCHEMA}.records VALUES (0, repeat('甲',2731))")
                    await rejected()  # UTF-8 >8 KiB in just one caption
                    await data.execute(f"DELETE FROM {_SCHEMA}.records")
                    await data.execute(f"""
                        INSERT INTO {_SCHEMA}.records SELECT i,NULL FROM generate_series(1,101) i;
                    """)
                    await rejected()
                    await data.execute(f"DELETE FROM {_SCHEMA}.records WHERE business_key = 101")
                    self.assertEqual(len(await read()), 100)
                    await data.execute(f"REINDEX INDEX {_SCHEMA}.records_pkey")
                    await rejected()  # Only the pinned index physical file changed.
                    async with data.transaction():
                        await data.execute(f"LOCK TABLE ONLY {_SCHEMA}.identity, ONLY {_SCHEMA}.records IN ACCESS SHARE MODE")
                        new_proof = await receiver.compute_snapshot_relation_proof(data)
                    reenrolled = replace(profile, proof_sha256=new_proof, deployment_revision=uuid4())
                    self.assertEqual(len(await read(reenrolled)), 100)
                    await data.execute(f"TRUNCATE {_SCHEMA}.records")
                    await rejected(reenrolled)  # Same OID and schema, new pinned physical files.
            finally:
                await admin.execute(f"DROP DATABASE IF EXISTS {database}")
                await admin.execute(f"DROP ROLE {role}")
