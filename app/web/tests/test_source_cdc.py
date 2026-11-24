"""Saved-source CDC isolation, lifecycle and secret boundaries; no live services."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID

from fastapi import HTTPException
import httpx
from pydantic import ValidationError

from app.web.api.models.data_connections import DataConnectionPayload
from app.web.api.models.source_cdc import SourceCdcAction, SourceCdcCreate
from app.web.services import source_cdc as cdc
from app.web.services import data_connections
from app.web.services import cdc_runtime


IDENTIFIER = UUID("9e8527e4-56d3-4ab4-8878-25322c5261f2")
REVISION = datetime(2026, 11, 24, tzinfo=timezone.utc)


def payload(**options):
    return SourceCdcCreate(expectedUpdatedAt=REVISION, tables=["example.records"], **options)


def source(engine="postgresql", **config):
    value = DataConnectionPayload(name="External", engine=engine, config={
        "host": "db.example.com", "port": 5432, "database": "example", "username": "capture", **config,
    })
    return SimpleNamespace(engine=engine, config=value.config, cdc_revision=REVISION.isoformat())


@asynccontextmanager
async def session():
    yield object()


def registry(engine="postgresql"):
    value = source(engine)
    row = {"name": "External", "engine": engine, "updated_at": REVISION,
           "config": value.config.model_dump(), "credentials_ciphertext": "encrypted"}
    return SimpleNamespace(_session=session, _stored=AsyncMock(return_value=row),
                           _credentials=AsyncMock(return_value={"password": "private-password"}))


class SourceCdcConfigTests(unittest.TestCase):
    def test_supported_types_are_explicit_and_independent_of_driver(self):
        with patch.object(data_connections, "_driver"):
            items = {item["engine"]: item["cdcSupported"] for item in data_connections.connector_types()}
        self.assertEqual({engine for engine, supported in items.items() if supported},
                         {"postgresql", "mongodb", "mysql", "oracle", "milvus"})
        self.assertFalse(items["dameng"])

    def test_postgres_capture_does_not_use_internal_etl_schemas(self):
        config = cdc.build_config(IDENTIFIER, source(), "secret", payload(publication="external_pub"), "a" * 32)
        self.assertEqual(config["table.include.list"], r"example\.records")
        self.assertEqual(config["publication.autocreate.mode"], "disabled")
        self.assertEqual(config["database.sslmode"], "verify-full")
        self.assertEqual(config["snapshot.mode"], "initial")
        self.assertEqual(config["slot.name"], "spider_" + "a" * 32)
        self.assertEqual(config["tombstones.on.delete"], "true")
        self.assertNotIn("schema.include.list", config)

    def test_new_capture_has_new_topics_and_slot(self):
        first = cdc.build_config(IDENTIFIER, source(), "secret", payload(publication="pub"), "a" * 32)
        second = cdc.build_config(IDENTIFIER, source(), "secret", payload(publication="pub"), "b" * 32)
        self.assertNotEqual(first["topic.prefix"], second["topic.prefix"])
        self.assertNotEqual(first["slot.name"], second["slot.name"])

    def test_mongodb_credentials_and_ipv6_are_encoded_and_topology_discovered(self):
        config = cdc.build_config(IDENTIFIER, source("mongodb", host="2001:db8::1", username="a@b"),
                                  "p:/?@", payload(), "a" * 32)
        uri = config["mongodb.connection.string"]
        self.assertIn("a%40b:p%3A%2F%3F%40@[2001:db8::1]", uri)
        self.assertNotIn("directConnection", uri)
        self.assertEqual(config["capture.mode"], "change_streams_update_full")
        self.assertEqual(config["collection.include.list"], r"example\.records")

    def test_mysql_history_inherits_broker_security(self):
        with patch.object(cdc.settings, "kafka_security_protocol", "SASL_SSL"), \
             patch.object(cdc.settings, "kafka_sasl_mechanism", "SCRAM-SHA-512"), \
             patch.object(cdc.settings, "kafka_sasl_username", 'user"'), \
             patch.object(cdc.settings, "kafka_sasl_password", "secret\\"), \
             patch.object(cdc.settings, "kafka_ssl_cafile", "/etc/kafka/kafka.crt"):
            config = cdc.build_config(IDENTIFIER, source("mysql"), "secret", payload(serverId=42), "a" * 32)
        self.assertEqual(config["database.server.id"], "42")
        self.assertEqual(config["database.ssl.mode"], "verify_identity")
        self.assertEqual(config["schema.history.internal.consumer.security.protocol"], "SASL_SSL")
        self.assertIn('username="user\\""', config["schema.history.internal.producer.sasl.jaas.config"])
        self.assertEqual(config["schema.history.internal.kafka.topic"], config["topic.prefix"] + ".schema-history")

    def test_oracle_uses_saved_service_and_cdb_pdb(self):
        config = cdc.build_config(IDENTIFIER, source("oracle"), "secret",
                                  payload(oracleDatabase="ORCLCDB", oraclePdb="ORCLPDB1"), "a" * 32)
        self.assertEqual(config["database.dbname"], "ORCLCDB")
        self.assertEqual(config["database.pdb.name"], "ORCLPDB1")
        self.assertIn("PROTOCOL=TCPS", config["database.url"])
        self.assertIn("SERVICE_NAME=example", config["database.url"])
        self.assertIn("SSL_SERVER_DN_MATCH=YES", config["database.url"])

    def test_engine_specific_required_fields(self):
        for engine in ("postgresql", "mysql", "oracle"):
            with self.subTest(engine=engine), self.assertRaises(HTTPException):
                cdc.build_config(IDENTIFIER, source(engine), "secret", payload(), "a" * 32)

    def test_source_database_scope_cannot_be_widened(self):
        for engine in ("mongodb", "mysql"):
            with self.subTest(engine=engine), self.assertRaises(HTTPException):
                cdc.build_config(IDENTIFIER, source(engine, database="other"), "secret", payload(), "a" * 32)

    def test_arbitrary_regex_and_duplicate_selection_are_rejected(self):
        for names in (["example.*"], ["example.a|other.b"], ["example.a", "example.a"], ["unqualified"]):
            with self.subTest(names=names), self.assertRaises(ValidationError):
                SourceCdcCreate(expectedUpdatedAt=REVISION, tables=names)


class SourceCdcLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_monitor_includes_saved_source_tasks(self):
        name = cdc.connector_name(IDENTIFIER)
        response = httpx.Response(200, request=httpx.Request("GET", "http://connect/connectors"), json={name: {"status": {
            "connector": {"state": "RUNNING"}, "tasks": [{"state": "FAILED"}],
        }}})
        with patch.object(cdc.settings, "cdc_connect_url", "http://connect"), \
             patch.object(cdc.settings, "db_url", ""), patch.object(cdc.settings, "etl_output_mode", "direct"), \
             patch.object(httpx.AsyncClient, "get", AsyncMock(return_value=response)):
            result = await cdc_runtime._connect()
        self.assertEqual([item.name for item in result.connectors], [name])
        self.assertEqual(result.state, "not_ready")

    async def test_create_uses_saved_secret_and_checks_config_before_registration(self):
        saved = registry()
        call = AsyncMock(side_effect=[None, {"error_count": 0}, {"name": "created"}])
        with patch.object(cdc, "request", call), patch.object(cdc.settings, "cdc_connect_url", "http://connect"):
            self.assertEqual(await cdc.SourceCdc(saved).create(IDENTIFIER, payload(publication="pub")), {"accepted": True})
        self.assertEqual([c.args[0] for c in call.call_args_list], ["GET", "PUT", "POST"])
        config = call.call_args_list[-1].kwargs["json"]["config"]
        self.assertEqual(config["database.password"], "private-password")
        self.assertEqual(saved._stored.call_args.kwargs["lock"], " FOR UPDATE")

    async def test_existing_task_blocks_source_mutation(self):
        with patch.object(cdc, "request", AsyncMock(return_value={})), \
             patch.object(cdc.settings, "cdc_connect_url", "http://connect"), self.assertRaises(HTTPException) as failure:
            await cdc.require_unconfigured(IDENTIFIER, "postgresql")
        self.assertEqual(failure.exception.status_code, 409)

    async def test_stale_revision_never_calls_connect(self):
        call = AsyncMock()
        with patch.object(cdc, "request", call), self.assertRaises(HTTPException) as failure:
            await cdc.SourceCdc(registry()).create(IDENTIFIER, payload(publication="pub").model_copy(
                update={"expectedUpdatedAt": datetime(2020, 1, 1, tzinfo=timezone.utc)}))
        self.assertEqual(failure.exception.status_code, 409)
        call.assert_not_awaited()

    async def test_failed_validation_never_registers_and_does_not_echo_secrets(self):
        call = AsyncMock(side_effect=[None, {"error_count": 1, "configs": ["private-password"]}])
        with patch.object(cdc, "request", call), patch.object(cdc.settings, "cdc_connect_url", "http://connect"), \
             self.assertRaises(HTTPException) as failure:
            await cdc.SourceCdc(registry()).create(IDENTIFIER, payload(publication="pub"))
        self.assertEqual(call.await_count, 2)
        self.assertNotIn("private-password", str(failure.exception.detail))

    async def test_status_redacts_connector_config_and_failure_trace(self):
        config = {"database.password": "private-password", "topic.prefix": "prefix",
                  "spider.capture.selection": payload(publication="pub").model_dump_json(exclude={"expectedUpdatedAt"}),
                  "spider.source.revision": REVISION.isoformat()}
        call = AsyncMock(side_effect=[[{"class": cdc.CONNECTORS["postgresql"]}], config,
                                     {"connector": {"state": "RUNNING"}, "tasks": [{"id": 0, "state": "FAILED", "trace": "private-password"}]}])
        with patch.object(cdc, "request", call):
            status = await cdc.SourceCdc(registry()).describe(IDENTIFIER)
        self.assertEqual(status["tasks"], [{"id": 0, "state": "FAILED"}])
        self.assertNotIn("private-password", json.dumps(status))

    async def test_controls_only_address_this_sources_connector(self):
        for action, method, suffix in (("pause", "PUT", "/pause"), ("resume", "PUT", "/resume"),
                                       ("restart", "POST", "/restart"), ("remove", "DELETE", "")):
            with self.subTest(action=action):
                call = AsyncMock(return_value={"spider.source.revision": REVISION.isoformat()})
                with patch.object(cdc, "request", call):
                    await cdc.SourceCdc(registry()).control(IDENTIFIER, SourceCdcAction(expectedUpdatedAt=REVISION, action=action))
                self.assertEqual(call.call_args.args, (method, f"/connectors/{cdc.connector_name(IDENTIFIER)}{suffix}"))

    async def test_connect_failure_is_explicit_and_redacted(self):
        response = httpx.Response(500, text="password=secret", request=httpx.Request("GET", "http://connect"))
        with patch.object(cdc.settings, "cdc_connect_url", "http://connect"), \
             patch.object(httpx.AsyncClient, "request", AsyncMock(return_value=response)), self.assertRaises(HTTPException) as failure:
            await cdc.request("GET", "/connectors")
        self.assertEqual(failure.exception.status_code, 502)
        self.assertNotIn("secret", str(failure.exception.detail))

    async def test_changed_source_cannot_resume_but_can_remove_capture(self):
        for action in ("resume", "restart"):
            call = AsyncMock(return_value={"spider.source.revision": "old"})
            with patch.object(cdc, "request", call), self.assertRaises(HTTPException) as failure:
                await cdc.SourceCdc(registry()).control(IDENTIFIER, SourceCdcAction(expectedUpdatedAt=REVISION, action=action))
            self.assertEqual(failure.exception.status_code, 409)
            self.assertEqual(call.await_count, 1)
        call = AsyncMock(return_value={"spider.source.revision": "old"})
        with patch.object(cdc, "request", call):
            await cdc.SourceCdc(registry()).control(IDENTIFIER, SourceCdcAction(expectedUpdatedAt=REVISION, action="remove"))
        self.assertEqual(call.call_args.args[0], "DELETE")

    async def test_only_404_is_treated_as_no_task(self):
        response = httpx.Response(401, request=httpx.Request("GET", "http://connect"))
        with patch.object(cdc.settings, "cdc_connect_url", "http://connect"), \
             patch.object(httpx.AsyncClient, "request", AsyncMock(return_value=response)), self.assertRaises(HTTPException):
            await cdc.require_unconfigured(IDENTIFIER, "postgresql")
