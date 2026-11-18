"""Send current, authorized transaction effects to an operator-configured receiver."""

import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from sqlalchemy import select

from app.web.api.models import User
from app.web.api.models.ontology_graph import OntologyLink, OntologyLinkType, OntologyRemovedLink
from app.web.api.models.ontology_runtime import OntologyRuntimeRecord as Record
from app.web.api.models.ontology_runtime import OntologyTransaction
from app.web.api.models.ontology_writeback import OntologyDelivery
from app.web.core.config import settings
from app.web.services.ontology_graph import access, manage
from app.web.services.ontology_markings import require_clearance
from app.web.services.ontology_record_policy import authorize_record_values
from app.web.services.ontology_transactions import require_member


class Receiver(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projectId: UUID
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=100)
    endpoint: HttpUrl
    secret: SecretStr = Field(min_length=32)

    @model_validator(mode="after")
    def secure_endpoint(self):
        url = urlsplit(str(self.endpoint))
        if (
            url.username
            or url.password
            or url.fragment
            or (url.scheme != "https" and url.hostname not in {"127.0.0.1", "::1"})
        ):
            raise ValueError("Receiver requires HTTPS or a loopback test endpoint")
        return self


def receivers(project_id):
    filename = settings.ONTOLOGY_WRITEBACK_RECEIVERS_FILE
    if not filename:
        return {}
    try:
        entries = TypeAdapter(list[Receiver]).validate_json(Path(filename).read_bytes())
        matches = [v for v in entries if v.projectId == project_id]
        if len({v.key for v in matches}) != len(matches):
            raise ValueError("duplicate receiver")
        return {v.key: v for v in matches}
    except (OSError, ValueError, ValidationError):
        raise HTTPException(503, "Writeback receiver configuration is invalid") from None


async def project_access(session, user, project_id):
    await require_member(session, user, project_id, lock=True)
    current = await session.get(User, user.id, populate_existing=True)
    await manage(session, current, project_id)


def unavailable():
    raise HTTPException(404, "Transaction data is unavailable")


def stale():
    raise HTTPException(
        409, "Transaction effects have changed. Commit a new transaction to deliver current data."
    )


async def batch_for(session, user, project_id, tx_id):
    await project_access(session, user, project_id)
    batch = await session.scalar(
        select(OntologyTransaction)
        .where(
            OntologyTransaction.id == tx_id,
            OntologyTransaction.actor_id == user.id,
            OntologyTransaction.project_id == project_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if batch is None:
        unavailable()
    await require_clearance(session, user, project_id, batch.markings)
    return batch


async def effects(session, user, batch, *, sending):
    if sending and batch.undone_at:
        stale()
    records, endpoints, edges = {}, {}, []
    for step in batch.steps:
        if step["kind"] in ("create", "modify", "delete"):
            record = UUID(step["recordId"])
            records[record] = step
            endpoints[record] = UUID(step["definitionId"])
        else:
            typ = await session.scalar(
                select(OntologyLinkType)
                .where(
                    OntologyLinkType.id == UUID(step["linkTypeId"]),
                    OntologyLinkType.project_id == batch.project_id,
                )
                .with_for_update(read=True)
            )
            if not typ:
                unavailable()
            endpoints[UUID(step["source"])] = typ.source_definition_id
            endpoints[UUID(step["target"])] = typ.target_definition_id
            edges.append(step)
    definitions = {}
    for typ in sorted(set(endpoints.values())):
        row, definition, ctx, fields = await access(session, user, typ)
        if row.project_id != batch.project_id or not fields:
            unavailable()
        definitions[typ] = (definition, ctx, fields)
    projected = []
    for identifier, typ in sorted(endpoints.items()):
        definition, ctx, fields = definitions[typ]
        record = await session.scalar(
            select(Record)
            .where(Record.id == identifier, Record.definition_id == typ)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if record is None or not await authorize_record_values(
            session, typ, definition, ctx, identifier, record.values
        ):
            unavailable()
        step = records.get(identifier)
        if not step:
            if sending and record.deleted:
                stale()
            continue
        if sending and (
            record.revision != step["revision"] or record.deleted != (step["kind"] == "delete")
        ):
            stale()
        if sending:
            value = {
                "recordId": str(identifier),
                "definitionId": str(typ),
                "revision": record.revision,
                "deleted": record.deleted,
            }
            if not record.deleted:
                value["properties"] = {k: record.values[k] for k in fields if k in record.values}
            projected.append(value)
    links = []
    if sending:
        for step in edges:
            keys = [UUID(step[k]) for k in ("linkTypeId", "source", "target")]
            model = OntologyLink if step["kind"] == "link" else OntologyRemovedLink
            current = await session.scalar(
                select(model).where(
                    model.link_type_id == keys[0],
                    model.source_record_id == keys[1],
                    model.target_record_id == keys[2],
                )
            )
            if not current or (current.id if step["kind"] == "link" else current.link_id) != UUID(
                step["edgeId"]
            ):
                stale()
            links.append({k: step[k] for k in ("kind", "linkTypeId", "source", "target", "edgeId")})
    return {
        "version": 1,
        "projectId": str(batch.project_id),
        "transactionId": str(batch.id),
        "records": projected,
        "relationships": links,
    }


def receipt(row):
    return {
        "id": str(row.id),
        "receiver": row.receiver,
        "status": row.state,
        "attempts": row.attempts,
        "receiptId": str(row.receipt_id) if row.receipt_id else None,
        "failure": row.failure,
    }


async def list_status(session, user, project_id, tx_id):
    batch = await batch_for(session, user, project_id, tx_id)
    await effects(session, user, batch, sending=False)
    options = receivers(project_id)
    rows = (
        await session.scalars(
            select(OntologyDelivery).where(OntologyDelivery.transaction_id == tx_id)
        )
    ).all()
    return {
        "receivers": [{"key": o.key, "name": o.name} for o in options.values()],
        "deliveries": [receipt(row) for row in rows],
    }


def send_options(project_id, key):
    receiver = receivers(project_id).get(key)
    if not receiver:
        raise HTTPException(404, "Receiver is unavailable for this project")
    return receiver


async def send_http(receiver, delivery, payload):
    timestamp = str(int(time.time()))
    key = str(delivery.id)
    signed = f"v1.{timestamp}.{key}.{delivery.payload_hash}".encode()
    signature = hmac.digest(receiver.secret.get_secret_value().encode(), signed, "sha256").hex()
    headers = {
        "Content-Type": "application/json",
        "Accept-Encoding": "identity",
        "Idempotency-Key": key,
        "X-Ontology-Timestamp": timestamp,
        "X-Ontology-SHA256": delivery.payload_hash,
        "X-Ontology-Signature": "v1=" + signature,
    }
    try:
        async with (
            asyncio.timeout(8),
            httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=5) as client,
            client.stream("POST", str(receiver.endpoint), headers=headers, content=payload) as res,
        ):
            if res.status_code not in {200, 201}:
                return None, f"Receiver returned HTTP {res.status_code}"
            data = bytearray()
            async for part in res.aiter_raw(chunk_size=4096):
                data.extend(part)
                if len(data) > 4096:
                    return None, "Receiver acknowledgement exceeded the size limit"
            ack = json.loads(data)
            if (
                not isinstance(ack, dict)
                or ack.get("deliveryId") != key
                or ack.get("payloadSha256") != delivery.payload_hash
            ):
                return None, "Receiver acknowledgement did not match the delivery"
            return UUID(str(ack["receiptId"])), None
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError):
        return None, "No valid receiver acknowledgement; retry with the same delivery ID"


def encoded(payload):
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def hash_of(data):
    return hashlib.sha256(data).hexdigest()


async def deliver(session, user, project_id, tx_id, key):
    # Commit the identity and body digest before any ambiguous HTTP side effect.
    actor = user.id
    for phase in ("prepare", "send"):
        batch = await batch_for(session, user, project_id, tx_id)
        receiver = send_options(project_id, key)
        row = await session.scalar(
            select(OntologyDelivery)
            .where(OntologyDelivery.transaction_id == tx_id, OntologyDelivery.receiver == key)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row and row.state == "delivered":
            await effects(session, user, batch, sending=False)
            return receipt(row)
        payload = encoded(await effects(session, user, batch, sending=True))
        body_hash = hash_of(payload)
        endpoint_hash = hash_of(str(receiver.endpoint).encode())
        if row is None:
            row = OntologyDelivery(
                id=uuid4(),
                transaction_id=tx_id,
                receiver=key,
                endpoint_hash=endpoint_hash,
                payload_hash=body_hash,
            )
            session.add(row)
        elif row.payload_hash != body_hash or row.endpoint_hash != endpoint_hash:
            raise HTTPException(
                409, "Current data or destination differs from the prepared delivery"
            )
        if phase == "prepare":
            await session.commit()
            user = await session.get(User, actor, populate_existing=True)
            if user is None:
                unavailable()
            continue
        row.attempts += 1
        row.receipt_id, row.failure = await send_http(receiver, row, payload)
        row.state = "failed" if row.failure else "delivered"
        result = receipt(row)
        await session.commit()
        return result
