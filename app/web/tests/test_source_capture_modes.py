"""Contracts for DMDRS bindings, Milvus channels and MinIO notification ownership."""

from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from botocore.exceptions import ClientError
from fastapi import HTTPException
from pydantic import ValidationError

from app.web.api.models.source_cdc import DamengCdcBind, ObjectEventsCreate, ObjectEventsRemove, SourceCdcAction, SourceCdcCreate
from app.web.services import source_cdc as cdc
from app.web.services import source_dameng_cdc as dameng
from app.web.services import source_object_events as objects
from app.web.tests.test_source_cdc import IDENTIFIER, REVISION, registry, source


MILVUS = {"kafkaBrokers": "mq:9092", "etcdEndpoints": ["http://etcd:2379"],
          "etcdRoot": "by-dev", "pchannels": ["by-dev-rootcoord-dml_0", "by-dev-rootcoord-dml_1"]}


def milvus_config():
    payload = SourceCdcCreate(expectedUpdatedAt=REVISION, tables=["example.records"], snapshot="no_data", milvus=MILVUS)
    return cdc.build_config(IDENTIFIER, source("milvus"), "secret", payload, "a" * 32)


def object_payload(before=None):
    return ObjectEventsCreate(expectedUpdatedAt=REVISION, expectedConfigurationHash=objects.configuration_hash(before or {}),
                              targetArn="arn:minio:sqs::capture:kafka", events=["s3:ObjectCreated:*", "s3:ObjectRemoved:*"],
                              prefix="incoming/", suffix=".json")


class CaptureContractTests(unittest.TestCase):
    def test_capture_modes_do_not_claim_object_events_are_cdc(self):
        from app.web.services import data_connections
        with patch.object(data_connections, "_driver"):
            items = {item["engine"]: item for item in data_connections.connector_types()}
        self.assertEqual(items["dameng"]["captureKind"], "dmdrs")
        self.assertEqual(items["s3"]["captureKind"], "object_events")
        self.assertFalse(items["s3"]["cdcSupported"])
        self.assertIsNone(items["elasticsearch"]["captureKind"])
        self.assertIsNone(items["clickhouse"]["captureKind"])

    def test_milvus_has_one_connector_per_channel_with_shared_output_namespace(self):
        config = milvus_config()
        captures = cdc.channel_configs(cdc.connector_name(IDENTIFIER), config)
        self.assertEqual(len(captures), 2)
        self.assertEqual(captures[0][0], cdc.connector_name(IDENTIFIER))
        self.assertNotEqual(captures[0][0], captures[1][0])
        self.assertEqual({cfg["milvus.pchannel.name"] for _, cfg in captures}, set(MILVUS["pchannels"]))
        self.assertEqual(len({cfg["topic.prefix"] for _, cfg in captures}), 1)
        self.assertEqual(config["snapshot.mode"], "never")
        self.assertEqual(config["milvus.collection.include.list"], "records")
        self.assertEqual(config["milvus.token"], "capture:secret")
        self.assertNotIn("secret", config["spider.capture.selection"])

    def test_milvus_token_auth_is_not_converted_to_username_password(self):
        value = source("milvus", auth="token", username="")
        selection = SourceCdcCreate(expectedUpdatedAt=REVISION, tables=["example.records"], milvus=MILVUS)
        config = cdc.build_config(IDENTIFIER, value, "api-token", selection, "a" * 32)
        self.assertEqual(config["milvus.token"], "api-token")

    def test_milvus_fields_cannot_be_supplied_to_another_connector(self):
        selection = SourceCdcCreate(expectedUpdatedAt=REVISION, tables=["example.records"], publication="pub", milvus=MILVUS)
        with self.assertRaises(HTTPException):
            cdc.build_config(IDENTIFIER, source(), "secret", selection, "a" * 32)

    def test_milvus_invalid_topology_and_embedded_credentials_are_rejected(self):
        for changed in ({"etcdEndpoints": ["http://user:secret@etcd:2379"]},
                        {"etcdEndpoints": ["file:///etc/passwd"]},
                        {"etcdEndpoints": ["http://etcd:2379/private"]},
                        {"pchannels": ["channel", "channel"]}):
            with self.subTest(changed=changed), self.assertRaises(ValidationError):
                SourceCdcCreate(expectedUpdatedAt=REVISION, tables=["example.records"], milvus={**MILVUS, **changed})

    def test_minio_target_is_explicit_and_no_snapshot_is_accepted(self):
        for extra in ({"targetArn": "arn:aws:sqs:us-east-1:123:queue"}, {"snapshot": "initial"}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                ObjectEventsCreate.model_validate({**object_payload().model_dump(), **extra})

    def test_notification_merge_preserves_every_unrelated_destination(self):
        before = {"QueueConfigurations": [{"Id": "external", "QueueArn": "external-arn", "Events": ["s3:ObjectCreated:*"]}],
                  "TopicConfigurations": [{"Id": "topic", "TopicArn": "topic-arn", "Events": ["s3:ObjectCreated:*"]}],
                  "LambdaFunctionConfigurations": [{"Id": "function", "LambdaFunctionArn": "lambda-arn", "Events": ["s3:ObjectCreated:*"]}],
                  "EventBridgeConfiguration": {}}
        original = deepcopy(before)
        added = objects.merge_notification(before, IDENTIFIER, objects.event_rule(IDENTIFIER, object_payload()))
        removed = objects.merge_notification(added, IDENTIFIER, None)
        self.assertEqual(removed, original)
        self.assertEqual(before, original)
        self.assertEqual(added["QueueConfigurations"][-1]["Filter"]["Key"]["FilterRules"],
                         [{"Name": "prefix", "Value": "incoming/"}, {"Name": "suffix", "Value": ".json"}])

    def test_hash_ignores_response_metadata_order_and_empty_destination_lists(self):
        first = {"QueueConfigurations": [{"Id": "b"}, {"Id": "a"}], "ResponseMetadata": {"RequestId": "request"}}
        second = {"QueueConfigurations": [{"Id": "a"}, {"Id": "b"}], "TopicConfigurations": []}
        self.assertEqual(objects.configuration_hash(first), objects.configuration_hash(second))

    def test_dmdrs_rejects_regex_selection(self):
        with self.assertRaises(ValidationError):
            DamengCdcBind(expectedUpdatedAt=REVISION, topic="capture", tables=["PUBLIC.*"])


class MinioLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_notifications_match_the_real_s3_sdk_contract(self):
        import boto3
        from botocore.stub import Stubber
        from app.web.services import data_connections

        saved = registry()
        saved._credentials = AsyncMock(return_value={"secretAccessKey": "stub-secret"})
        row = {"id": IDENTIFIER, "name": "Objects", "engine": "s3", "config": {
            "host": "minio.example.com", "port": 443, "username": "stub-key", "auth": "access_key",
            "region": "us-east-1", "bucket": "saved-bucket",
        }}
        selection = object_payload()
        expected = {"QueueConfigurations": [objects.event_rule(IDENTIFIER, selection)]}
        client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="stub-key", aws_secret_access_key="stub-secret")
        with Stubber(client) as stub:
            stub.add_response("get_bucket_notification_configuration", {}, {"Bucket": "saved-bucket"})
            stub.add_response("put_bucket_notification_configuration", {}, {"Bucket": "saved-bucket", "NotificationConfiguration": expected})
            stub.add_response("get_bucket_notification_configuration", expected, {"Bucket": "saved-bucket"})
            driver = SimpleNamespace(Session=lambda: SimpleNamespace(client=lambda *args, **kwargs: client))
            with patch.object(data_connections, "_driver", return_value=driver), \
                 patch.object(objects.SourceObjectEvents, "_source", AsyncMock(return_value=row)):
                await objects.SourceObjectEvents(saved).change(IDENTIFIER, selection)
            stub.assert_no_pending_responses()

    async def apply(self, client, selection, *, remove=False):
        saved = registry()
        async def run_operation(_registry, _row, operation):
            return operation(client, "saved-bucket")
        with patch.object(objects.SourceObjectEvents, "_source", AsyncMock(return_value={"id": IDENTIFIER})), \
             patch.object(objects, "bucket_operation", run_operation):
            return await objects.SourceObjectEvents(saved).change(IDENTIFIER, selection, remove=remove)

    async def test_creating_subscription_writes_and_verifies_remote_configuration(self):
        client = MagicMock()
        selection = object_payload()
        client.get_bucket_notification_configuration.side_effect = [{}, {"QueueConfigurations": [objects.event_rule(IDENTIFIER, selection)]}]
        result = await self.apply(client, selection)
        self.assertEqual(result, {"configured": True, "deliveryVerified": False})
        client.put_bucket_notification_configuration.assert_called_once()
        self.assertEqual(client.put_bucket_notification_configuration.call_args.kwargs["Bucket"], "saved-bucket")

    async def test_stale_bucket_hash_rejects_without_overwriting(self):
        client = MagicMock()
        client.get_bucket_notification_configuration.return_value = {"EventBridgeConfiguration": {}}
        with self.assertRaises(HTTPException) as failure:
            await self.apply(client, object_payload())
        self.assertEqual(failure.exception.status_code, 409)
        client.put_bucket_notification_configuration.assert_not_called()

    async def test_removal_only_deletes_owned_notification(self):
        client = MagicMock()
        before = {"QueueConfigurations": [objects.event_rule(IDENTIFIER, object_payload()), {"Id": "other"}]}
        client.get_bucket_notification_configuration.side_effect = [before, {"QueueConfigurations": [{"Id": "other"}]}]
        selection = ObjectEventsRemove(expectedUpdatedAt=REVISION, expectedConfigurationHash=objects.configuration_hash(before))
        result = await self.apply(client, selection, remove=True)
        self.assertFalse(result["configured"])
        self.assertEqual(client.put_bucket_notification_configuration.call_args.kwargs["NotificationConfiguration"],
                         {"QueueConfigurations": [{"Id": "other"}]})

    async def test_successful_put_without_persisted_rule_is_a_failure(self):
        client = MagicMock()
        client.get_bucket_notification_configuration.return_value = {}
        with self.assertRaises(HTTPException) as failure:
            await self.apply(client, object_payload())
        self.assertEqual(failure.exception.status_code, 502)

    async def test_backend_dropping_an_unrelated_destination_is_a_failure(self):
        client = MagicMock()
        before = {"TopicConfigurations": [{"Id": "external-topic"}]}
        selection = object_payload(before)
        client.get_bucket_notification_configuration.side_effect = [before, {"QueueConfigurations": [objects.event_rule(IDENTIFIER, selection)]}]
        with self.assertRaises(HTTPException) as failure:
            await self.apply(client, selection)
        self.assertEqual(failure.exception.status_code, 502)

    async def test_source_mutation_is_blocked_by_owned_notification(self):
        saved = registry()
        config = {"QueueConfigurations": [objects.event_rule(IDENTIFIER, object_payload())]}
        with patch.object(objects, "bucket_operation", AsyncMock(return_value=config)), self.assertRaises(HTTPException):
            await objects.require_no_object_events(saved, {"id": IDENTIFIER})

    async def test_notification_errors_do_not_leak_saved_credentials(self):
        saved = registry()
        saved._credentials = AsyncMock(return_value={"secretAccessKey": "private-secret"})
        row = {"name": "Objects", "engine": "s3", "config": {
            "host": "minio.example.com", "port": 443, "username": "access-key", "database": "",
            "auth": "access_key", "region": "us-east-1", "bucket": "saved-bucket",
        }}
        client = MagicMock()
        client.get_bucket_notification_configuration.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "private-secret"}}, "GetBucketNotificationConfiguration")
        driver = SimpleNamespace(Session=lambda: SimpleNamespace(client=lambda *args, **kwargs: client))
        from app.web.services import data_connections
        with patch.object(data_connections, "_driver", return_value=driver), self.assertRaises(HTTPException) as failure:
            await objects.bucket_operation(saved, row, lambda value, bucket: value.get_bucket_notification_configuration(Bucket=bucket))
        self.assertNotIn("private-secret", str(failure.exception.detail))


class MilvusLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_or_multi_partition_channels_are_rejected(self):
        from app.web.api.models.source_cdc import MilvusCapture
        for partitions in (None, {0, 1}):
            consumer = MagicMock(start=AsyncMock(), stop=AsyncMock(), topics=AsyncMock(),
                                 partitions_for_topic=MagicMock(return_value=partitions))
            with patch.object(cdc, "AIOKafkaConsumer", return_value=consumer), self.assertRaises(HTTPException) as failure:
                await cdc.check_milvus_channels(MilvusCapture(**MILVUS))
            self.assertEqual(failure.exception.status_code, 422)
            consumer.stop.assert_awaited_once()

    async def test_all_channels_are_validated_before_any_registration(self):
        selection = SourceCdcCreate(expectedUpdatedAt=REVISION, tables=["example.records"], milvus=MILVUS)
        call = AsyncMock(side_effect=[None, {"error_count": 0}, {"error_count": 1}])
        with patch.object(cdc.settings, "cdc_connect_url", "http://connect"), patch.object(cdc, "request", call), \
             self.assertRaises(HTTPException):
            await cdc.SourceCdc(registry("milvus")).create(IDENTIFIER, selection)
        self.assertNotIn("POST", [item.args[0] for item in call.call_args_list])

    async def test_partial_channel_creation_is_visible_as_incomplete(self):
        config = milvus_config()
        call = AsyncMock(side_effect=[[{"class": cdc.CONNECTORS["milvus"]}], config,
                                     {"connector": {"state": "RUNNING"}, "tasks": [{"id": 0, "state": "RUNNING"}]}, None])
        with patch.object(cdc, "request", call):
            result = await cdc.SourceCdc(registry("milvus")).describe(IDENTIFIER)
        self.assertEqual(result["state"], "INCOMPLETE")

    async def test_removal_deletes_children_before_root(self):
        config = milvus_config()
        call = AsyncMock(side_effect=[config, {}, {}])
        with patch.object(cdc, "request", call):
            await cdc.SourceCdc(registry("milvus")).control(IDENTIFIER, SourceCdcAction(expectedUpdatedAt=REVISION, action="remove"))
        calls = [c.args for c in call.call_args_list if c.args[0] == "DELETE"]
        name = cdc.connector_name(IDENTIFIER)
        self.assertEqual(calls, [("DELETE", f"/connectors/{name}-channel-1"), ("DELETE", f"/connectors/{name}")])

    async def test_restart_recreates_missing_channel_from_root_config(self):
        config = milvus_config()
        call = AsyncMock(side_effect=[config, config, {}, None, {}])
        with patch.object(cdc, "request", call):
            await cdc.SourceCdc(registry("milvus")).control(IDENTIFIER, SourceCdcAction(expectedUpdatedAt=REVISION, action="restart"))
        self.assertEqual(call.call_args.args, ("POST", "/connectors"))
        self.assertEqual(call.call_args.kwargs["json"]["config"]["milvus.pchannel.name"], MILVUS["pchannels"][1])


class DamengLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def saved(self, scalar_values):
        saved = registry()
        saved._stored.return_value["engine"] = "dameng"
        db = SimpleNamespace(scalar=AsyncMock(side_effect=scalar_values), execute=AsyncMock())
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def session():
            yield db
        saved._session = session
        return saved, db

    async def test_unprepared_environment_has_no_fake_running_state(self):
        saved, _ = self.saved([False])
        result = await dameng.SourceDamengCdc(saved).describe(IDENTIFIER)
        self.assertEqual(result["state"], "unconfigured")
        self.assertFalse(result["schemaReady"])
        self.assertFalse(result["deliveryVerified"])

    async def test_binding_requires_existing_topic_before_persisting(self):
        saved, db = self.saved([True, True, False])
        with patch.object(dameng, "topic_status", AsyncMock(return_value=False)), self.assertRaises(HTTPException):
            await dameng.SourceDamengCdc(saved).bind(IDENTIFIER, DamengCdcBind(expectedUpdatedAt=REVISION, topic="dmdrs", tables=["PUBLIC.ITEMS"]))
        db.execute.assert_not_awaited()

    async def test_successful_binding_is_not_reported_as_running_capture(self):
        saved, db = self.saved([True, True, False])
        with patch.object(dameng, "topic_status", AsyncMock(return_value=True)):
            result = await dameng.SourceDamengCdc(saved).bind(IDENTIFIER, DamengCdcBind(expectedUpdatedAt=REVISION, topic="dmdrs", tables=["PUBLIC.ITEMS"]))
        self.assertEqual(result, {"bound": True, "deliveryVerified": False})
        db.execute.assert_awaited_once()

    async def test_binding_blocks_source_mutation(self):
        saved, db = self.saved([True, True])
        with self.assertRaises(HTTPException):
            await cdc.require_unconfigured(IDENTIFIER, "dameng", registry=saved, session=db)
