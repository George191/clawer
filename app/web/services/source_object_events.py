"""MinIO bucket event subscriptions, distinct from database row CDC.

The bucket owns durable notification configuration. Only our notification ID is
changed; other notification targets and EventBridge settings are preserved.
"""

from contextlib import closing
from copy import deepcopy
import hashlib
import json
from uuid import UUID

from anyio import to_thread
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException

from app.web.api.models.data_connections import DataConnectionPayload


KEYS = ("QueueConfigurations", "TopicConfigurations", "LambdaFunctionConfigurations", "EventBridgeConfiguration")


def notification_id(identifier):
    return f"spider-source-{UUID(str(identifier)).hex}"


def notification_config(response):
    return {key: deepcopy(response[key]) for key in KEYS if key in response and (response[key] or key == "EventBridgeConfiguration")}


def configuration_hash(config):
    def canonical(value):
        if isinstance(value, dict):
            return {key: canonical(item) for key, item in value.items()}
        if isinstance(value, list):
            return sorted((canonical(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
        return value
    return hashlib.sha256(json.dumps(canonical(notification_config(config)), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def owned_notification(config, identifier):
    found = [item for key in KEYS[:3] for item in config.get(key, []) if item.get("Id") == notification_id(identifier)]
    if len(found) > 1:
        raise HTTPException(409, "Duplicate source notification IDs; reconcile bucket configuration before editing")
    return found[0] if found else None


def merge_notification(config, identifier, replacement):
    updated = deepcopy(config)
    for key in KEYS[:3]:
        if key in updated:
            updated[key] = [item for item in updated[key] if item.get("Id") != notification_id(identifier)]
    if replacement is not None:
        updated.setdefault("QueueConfigurations", []).append(replacement)
    return updated


def event_rule(identifier, payload):
    rule = {"Id": notification_id(identifier), "QueueArn": payload.targetArn, "Events": payload.events}
    filters = [{"Name": name, "Value": value} for name, value in (("prefix", payload.prefix), ("suffix", payload.suffix)) if value]
    if filters:
        rule["Filter"] = {"Key": {"FilterRules": filters}}
    return rule


async def bucket_operation(registry, row, operation):
    # Local import avoids a registry -> mutation guard -> registry import cycle.
    from app.web.services.data_connections import _driver, _http_endpoint

    cfg = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
    source = DataConnectionPayload(name=row["name"], engine=row["engine"], config=cfg)
    credentials = await registry._credentials(source, row)

    def run():
        options = Config(signature_version="s3v4", s3={"addressing_style": source.config.addressingStyle or "path"},
                         proxies={}, connect_timeout=3, read_timeout=3, retries={"total_max_attempts": 1})
        with closing(_driver("s3").Session().client(
            "s3", endpoint_url=_http_endpoint(source.config), region_name=source.config.region,
            aws_access_key_id=source.config.username, aws_secret_access_key=credentials["secretAccessKey"], config=options,
        )) as client:
            return operation(client, source.config.bucket)
    try:
        return await to_thread.run_sync(run)
    except (BotoCoreError, ClientError, ValueError, OSError):
        raise HTTPException(502, "Bucket notification operation failed; verify MinIO target ARN, TLS and notification permissions") from None


async def require_no_object_events(registry, row):
    config = await bucket_operation(registry, row, lambda client, bucket: notification_config(
        client.get_bucket_notification_configuration(Bucket=bucket)))
    if owned_notification(config, row["id"]) is not None:
        raise HTTPException(409, "Remove this source's object event subscription before editing or deleting the connection")


class SourceObjectEvents:
    def __init__(self, registry):
        self.registry = registry

    async def _source(self, identifier, session, expected=None):
        row = await self.registry._stored(str(identifier), session=session, lock=" FOR UPDATE")
        if row["engine"] != "s3":
            raise HTTPException(422, "Object events require an S3 / MinIO source")
        if expected is not None and row["updated_at"] != expected:
            raise HTTPException(409, "The source changed; reload before configuring object events")
        return row

    async def describe(self, identifier):
        async with self.registry._session() as session:
            row = await self._source(identifier, session)
            config = await bucket_operation(self.registry, row, lambda client, bucket: notification_config(
                client.get_bucket_notification_configuration(Bucket=bucket)))
            rule = owned_notification(config, identifier)
            return {"kind": "object_events", "state": "configured" if rule else "unconfigured",
                    "configurationHash": configuration_hash(config), "notification": rule,
                    "deliveryVerified": False}

    async def change(self, identifier, payload, *, remove=False):
        async with self.registry._session() as session:
            row = await self._source(identifier, session, payload.expectedUpdatedAt)

            def apply(client, bucket):
                before = notification_config(client.get_bucket_notification_configuration(Bucket=bucket))
                if configuration_hash(before) != payload.expectedConfigurationHash:
                    raise HTTPException(409, "Bucket notifications changed; refresh before saving")
                existing = owned_notification(before, identifier)
                if remove and not existing:
                    raise HTTPException(404, "No object event subscription is configured")
                if not remove and existing:
                    raise HTTPException(409, "Remove the existing subscription before creating another")
                replacement = None if remove else event_rule(identifier, payload)
                updated = merge_notification(before, identifier, replacement)
                client.put_bucket_notification_configuration(Bucket=bucket, NotificationConfiguration=updated)
                after = notification_config(client.get_bucket_notification_configuration(Bucket=bucket))
                actual = owned_notification(after, identifier)
                same_rule = configuration_hash({"QueueConfigurations": [actual] if actual else []}) == configuration_hash({"QueueConfigurations": [replacement] if replacement else []})
                if not same_rule or configuration_hash(merge_notification(after, identifier, None)) != configuration_hash(merge_notification(before, identifier, None)):
                    raise HTTPException(502, "Bucket did not retain the requested notification; refresh before retrying")
                return {"configured": not remove, "deliveryVerified": False}

            return await bucket_operation(self.registry, row, apply)
