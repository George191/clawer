"""Verify exact immutable receiver contract against real disposable PostgreSQL."""

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import asyncpg
from pydantic import SecretStr

from app.web.services import table_export_snapshot_publication_receiver as receiver
from app.web.tests.test_table_export_snapshot_receiver import closing_connection

_SCHEMA = receiver.SNAPSHOT_PUBLICATION_SCHEMA
_SENDER = {"sender_port": 5432, "sender_database": "sender_tests", "sender_username": "sender_login"}
_SENDER_DSN = "postgresql+asyncpg://sender_login:unit@127.0.0.1:5432/sender_tests"
_PROVISION = Path(receiver.__file__).with_name("table-export-snapshot-publication-provision.sql")


def _settings(profile):
    return receiver.settings.model_copy(update={
        "ENVIRONMENT": "local",
        "TABLE_EXPORT_SANDBOX_ENABLED": True,
        "TABLE_EXPORT_SNAPSHOT_PUBLICATION_ENABLED": True,
        **{"TABLE_EXPORT_SNAPSHOT_PUBLICATION_" + key: val for key, val in {
            "CONFIG_REVISION": profile.deployment_revision, "HOST": profile.host, "PORT": profile.port,
            "DATABASE": profile.database, "USERNAME": profile.username, "PASSWORD": SecretStr(profile.password),
            "MARKER": profile.marker, "PROOF_SHA256": profile.proof_sha256,
        }.items()},
    })


class PublicationReceiverAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_dial_on_sender_or_preview_alias(self):
        profile = receiver.SnapshotPublicationProfile(
            uuid4(), "127.0.0.1", 56480, "publication_db", "publication_writer", "synthetic", uuid4(), "f" * 64,
        )
        with (
            patch.object(receiver, "settings", _settings(profile)),
            patch.object(type(receiver.settings), "SQLALCHEMY_RUNNABLE_DATABASE_URI", _SENDER_DSN),
            patch.object(receiver.asyncpg, "connect", new_callable=AsyncMock) as dial,
        ):
            self.assertEqual(receiver.configured_snapshot_publication_profile(), profile)
            for override in ({"sender_port": profile.port}, {"sender_database": profile.database},
                             {"sender_username": profile.username}, {"sender_marker": profile.marker}):
                params = {**_SENDER, "sender_marker": uuid4(), **override}
                with self.assertRaises(receiver.SnapshotPublicationReceiverUnavailable):
                    async with receiver.snapshot_publication_connection(profile, **params):
                        self.fail("alias was allowed")
            receiver.settings.TABLE_EXPORT_SNAPSHOT_PREVIEW_USERNAME = profile.username
            self.assertIsNone(receiver.configured_snapshot_publication_profile())
            receiver.settings.TABLE_EXPORT_SNAPSHOT_PREVIEW_USERNAME = ""
            with patch.object(type(receiver.settings), "SQLALCHEMY_RUNNABLE_DATABASE_URI",
                              f"postgresql+asyncpg://sender_login:unit@127.0.0.1:{profile.port}/sender_tests"):
                self.assertIsNone(receiver.configured_snapshot_publication_profile())
            dial.assert_not_awaited()


@unittest.skipUnless(os.environ.get("ISOLATED_SNAPSHOT_RECEIVER_PORT"), "requires a disposable local PostgreSQL cluster")
class PublicationReceiverPGTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_enrollment_writer_permissions_pins_and_lock_denial(self):
        port = int(os.environ["ISOLATED_SNAPSHOT_RECEIVER_PORT"])
        admin_name = os.environ.get("ISOLATED_SNAPSHOT_RECEIVER_USER", "agent")
        admin_password = os.environ["ISOLATED_SNAPSHOT_RECEIVER_ADMIN_PASSWORD"]
        suffix = uuid4().hex[:8]
        database, role, password, marker = f"test_published_{suffix}", f"test_publish_write_{suffix}", uuid4().hex, uuid4()

        async def connect(db, user, secret):
            return await asyncpg.connect(
                host="127.0.0.1", port=port, database=db, user=user, password=secret, ssl=False,
                server_settings={"search_path": "pg_catalog"},
            )

        async with closing_connection(await connect("postgres", admin_name, admin_password)) as admin:
            await admin.execute(f"CREATE ROLE {role} LOGIN NOINHERIT PASSWORD '{password}'")
            try:
                await admin.execute(f"CREATE DATABASE {database}")
                async with closing_connection(await connect(database, admin_name, admin_password)) as data:
                    await data.execute(f"REVOKE ALL ON DATABASE {database} FROM PUBLIC")
                    await data.execute(f"GRANT CONNECT ON DATABASE {database} TO {role}")
                    ddl = _PROVISION.read_text().replace(':"snapshot_writer"', f'"{role}"')
                    await data.execute(ddl.replace(":'snapshot_marker'", f"'{marker}'"))
                    async with data.transaction():
                        await receiver.lock_snapshot_publication_relations(data)
                        proof = await receiver.compute_snapshot_publication_proof(data)
                    profile = receiver.SnapshotPublicationProfile(
                        uuid4(), "127.0.0.1", port, database, role, password, marker, proof,
                    )
                    await data.execute("""
                        CREATE FUNCTION public.test_publication_trigger() RETURNS trigger
                        LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$;
                    """)
                    async with closing_connection(await connect(database, role, password)) as writer:
                        async with writer.transaction():
                            await receiver.verify_snapshot_publication_receiver(writer, profile)
                            current = await writer.fetchval(f"SELECT current_generation FROM {_SCHEMA}.state FOR UPDATE")
                            self.assertEqual(current, 0)
                            # A common non-rewrite trigger DDL lock must also be fenced;
                            # ACCESS SHARE or ROW EXCLUSIVE would not provide all fences.
                            for mode in ("SHARE ROW EXCLUSIVE", "SHARE UPDATE EXCLUSIVE"):
                                with self.assertRaises(asyncpg.LockNotAvailableError):
                                    async with data.transaction():
                                        await data.execute(f"LOCK TABLE {_SCHEMA}.state IN {mode} MODE NOWAIT")
                            await data.execute("SET lock_timeout='50ms'")
                            try:
                                with self.assertRaises(asyncpg.LockNotAvailableError):
                                    await data.execute(f"""
                                        CREATE TRIGGER late_trigger BEFORE UPDATE ON {_SCHEMA}.state
                                        FOR EACH ROW EXECUTE FUNCTION public.test_publication_trigger()
                                    """)
                                with self.assertRaises(asyncpg.LockNotAvailableError):
                                    await data.execute(f"REINDEX INDEX CONCURRENTLY {_SCHEMA}.records_pkey")
                            finally:
                                await data.execute("RESET lock_timeout")
                            await writer.execute(
                                f"INSERT INTO {_SCHEMA}.generations VALUES (1,0,$1,$2,1,clock_timestamp())",
                                uuid4(), "a" * 64,
                            )
                            await writer.execute(f"INSERT INTO {_SCHEMA}.records VALUES (1,42,'test')")
                            await writer.execute(f"UPDATE {_SCHEMA}.state SET current_generation=1")
                        for forbidden in (
                            f"UPDATE {_SCHEMA}.state SET marker=marker",
                            f"UPDATE {_SCHEMA}.generations SET row_count=0",
                            f"DELETE FROM {_SCHEMA}.records",
                            f"TRUNCATE {_SCHEMA}.records",
                            f"ALTER TABLE {_SCHEMA}.records ENABLE ROW LEVEL SECURITY",
                        ):
                            with self.assertRaises(asyncpg.InsufficientPrivilegeError):
                                await writer.execute(forbidden)
                        async with data.transaction():
                            await data.execute(f"LOCK TABLE {_SCHEMA}.records IN ACCESS EXCLUSIVE MODE")
                            with self.assertRaises(asyncpg.LockNotAvailableError):
                                async with writer.transaction():
                                    await receiver.verify_snapshot_publication_receiver(writer, profile)
                        await data.execute(f"GRANT UPDATE (caption) ON {_SCHEMA}.records TO {role}")
                        with self.assertRaises(receiver.SnapshotPublicationReceiverUnavailable):
                            async with writer.transaction():
                                await receiver.verify_snapshot_publication_receiver(writer, profile)
                        await data.execute(f"REVOKE UPDATE (caption) ON {_SCHEMA}.records FROM {role}")
                        await data.execute(f"GRANT SELECT ON {_SCHEMA}.records TO PUBLIC")
                        with self.assertRaises(receiver.SnapshotPublicationReceiverUnavailable):
                            async with writer.transaction():
                                await receiver.verify_snapshot_publication_receiver(writer, profile)
                        await data.execute(f"REVOKE SELECT ON {_SCHEMA}.records FROM PUBLIC")
                        with self.assertRaises(receiver.SnapshotPublicationReceiverUnavailable):
                            async with writer.transaction():
                                await receiver.verify_snapshot_publication_receiver(writer, replace(profile, marker=uuid4()))
                        await data.execute(f"REINDEX INDEX {_SCHEMA}.generations_request_id_key")
                        with self.assertRaises(receiver.SnapshotPublicationReceiverUnavailable):
                            async with writer.transaction():
                                await receiver.verify_snapshot_publication_receiver(writer, profile)
            finally:
                await admin.execute(f"DROP DATABASE IF EXISTS {database}")
                await admin.execute(f"DROP ROLE {role}")
