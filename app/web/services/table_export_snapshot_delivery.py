"""Internal one-shot delivery of an already sealed local synthetic intent.

A durably admitted request is never sent twice, even when no receiver receipt can
be established. This module has no source reader or scheduler.
"""

import asyncio
from collections.abc import Callable, Mapping
from hashlib import sha256
import hmac
from ipaddress import ip_address
import json
import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.core.config import settings
from app.web.services.table_export_snapshot import _sender_scope
from app.web.services.table_export_snapshot_intent import (
    _DELIVERY_RELATIONS, _PRIVATE, _PRIVATE_RELATIONS, _check_sender, _lock_scope, _prove_locked_scope, _verify_frozen,
)
from app.web.services.table_export_snapshot_publication import (
    SnapshotPublicationResult, SnapshotPublicationRow, publish_snapshot, reconcile_snapshot,
)
from app.web.services.table_export_snapshot_publication_receiver import configured_snapshot_publication_profile


class SnapshotDeliveryUnavailable(Exception):
    """Exact delivery unconfirmed. This result is not authority to resend."""


class SnapshotDeliveryAlreadyAdmitted(SnapshotDeliveryUnavailable):
    """First-only caller did not get the original admission."""


def _unavailable() -> SnapshotDeliveryUnavailable:
    return SnapshotDeliveryUnavailable("Synthetic snapshot delivery unconfirmed")


async def compute_snapshot_intent_delivery_proof(session: AsyncSession) -> str:
    """Operator enrollment for the exact private execution ledger relation."""
    await _lock_scope(session, _PRIVATE, _DELIVERY_RELATIONS)
    return await _prove_locked_scope(session, _PRIVATE, _DELIVERY_RELATIONS)


def _approval(actor, intent, request, previous, revision):
    if (settings.ENVIRONMENT != "local" or "ENVIRONMENT" not in settings.model_fields_set
        or not settings.TABLE_EXPORT_SANDBOX_ENABLED or not settings.TABLE_EXPORT_SNAPSHOT_DELIVERY_ENABLED
        or not isinstance(actor, UUID) or not isinstance(intent, UUID)
        or not isinstance(request, UUID) or request.version != 4
        or type(previous) is not int or not 0 <= previous <= 15 or not isinstance(revision, UUID)):
        raise _unavailable()
    grant = settings.TABLE_EXPORT_SNAPSHOT_DELIVERY_GRANTS.get(actor)
    profile = configured_snapshot_publication_profile()
    if (grant is None or profile is None or grant.intentId != intent or grant.requestId != request
        or grant.expectedPreviousGeneration != previous or not grant.configRevision == profile.config_revision == revision
        or grant.sourceMarker != settings.TABLE_EXPORT_SOURCE_MARKER
        or not grant.sourceMarker.int or not grant.privateMarker.int
        or len({grant.sourceMarker, grant.privateMarker, profile.marker}) != 3):
        raise _unavailable()
    return grant, profile


def _execution_digest(actor, grant, sender) -> str:
    binding = [
        "asiral:synthetic-snapshot-delivery-v1", str(actor), grant.model_dump(mode="json"),
        sender.address, sender.port, sender.database, sender.username,
    ]
    return sha256(json.dumps(binding, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def _approved_sender(session):
    sender = _sender_scope(session)
    approved = make_url(str(settings.SQLALCHEMY_RUNNABLE_DATABASE_URI))
    if (approved.drivername != "postgresql+asyncpg" or approved.query
        or (str(ip_address(approved.host)), approved.port or 5432, approved.database, approved.username)
           != (sender.address, sender.port, sender.database, sender.username)):
        raise _unavailable()
    return sender


async def _locked_delivery(session, grant, sender, actor_id, intent_id, request_id, execution_digest, *, read_only=False):
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" + (", READ ONLY" if read_only else "")))
    await session.execute(text("SET LOCAL search_path=pg_catalog,pg_temp"))
    await session.execute(text("SET LOCAL lock_timeout='400ms'"))
    await session.execute(text("SET LOCAL statement_timeout='1200ms'"))
    # Freeze and this service share the original ledger lock order. Acquire all
    # serialization locks before establishing a repeatable-read snapshot.
    if read_only:
        for names in (_PRIVATE_RELATIONS, _DELIVERY_RELATIONS):
            qualifier = ", ".join(f'ONLY "{_PRIVATE}"."{table}"' for table in names)
            await session.execute(text(f"LOCK TABLE {qualifier} IN ACCESS SHARE MODE NOWAIT"))
    else:
        await _lock_scope(session, _PRIVATE, _PRIVATE_RELATIONS)
        await _lock_scope(session, _PRIVATE, _DELIVERY_RELATIONS)
    await _check_sender(session, sender)
    origin_proof = await _prove_locked_scope(session, _PRIVATE, _PRIVATE_RELATIONS, read_only=read_only)
    delivery_proof = await _prove_locked_scope(session, _PRIVATE, _DELIVERY_RELATIONS, read_only=read_only)
    if not (hmac.compare_digest(origin_proof, grant.privateProofSha256)
            and hmac.compare_digest(delivery_proof, grant.deliveryProofSha256)):
        raise _unavailable()
    markers = (await session.scalars(text(f"""
        SELECT marker FROM ONLY {_PRIVATE}.identity WHERE singleton IS TRUE LIMIT 2
    """))).all()
    if markers != [grant.privateMarker]:
        raise _unavailable()
    origin = (await session.execute(text(f"""
        SELECT id,actor_id,request_id,config_revision,expected_previous_generation,scope_digest,payload_hash,row_count,created_at
        FROM ONLY {_PRIVATE}.intents WHERE id=:intent
          AND pg_catalog.isfinite(created_at) AND created_at <= pg_catalog.statement_timestamp()
    """), {"intent": intent_id})).mappings().one_or_none()
    if (origin is None or origin["actor_id"] != actor_id
        or origin["request_id"] != request_id or origin["config_revision"] != grant.configRevision
        or origin["expected_previous_generation"] != grant.expectedPreviousGeneration
        or not hmac.compare_digest(origin["scope_digest"], grant.scopeDigest)
        or not re.fullmatch(r"[0-9a-f]{64}", origin["payload_hash"], re.ASCII)
        or not 1 <= origin["row_count"] <= 100):
        raise _unavailable()
    prior = (await session.execute(text(f"""
        SELECT intent_id,request_id,scope_digest,execution_digest,payload_hash,expected_previous_generation,
               delivery_config_revision,row_count,receiver_generation,receiver_recorded_at,receiver_row_count
        FROM ONLY {_PRIVATE}.deliveries WHERE intent_id=:intent OR request_id=:request
        LIMIT 2
    """), {"intent": intent_id, "request": request_id})).mappings().all()
    if len(prior) > 1:
        raise _unavailable()
    if not prior:
        return origin, None
    row = prior[0]
    if (row["intent_id"] != intent_id or row["request_id"] != request_id
        or row["expected_previous_generation"] != grant.expectedPreviousGeneration
        or row["delivery_config_revision"] != grant.configRevision or row["row_count"] != origin["row_count"]
        or not hmac.compare_digest(row["scope_digest"], grant.scopeDigest)
        or not hmac.compare_digest(row["execution_digest"], execution_digest)
        or not hmac.compare_digest(row["payload_hash"], origin["payload_hash"])):
        raise _unavailable()
    return origin, row


async def deliver_snapshot_publication_intent(
    session: AsyncSession, *, actor_id: UUID, intent_id: UUID, request_id: UUID,
    expected_previous_generation: int, expected_config_revision: UUID,
    web_approval: Callable[[Mapping | None], None] | None = None,
) -> SnapshotPublicationResult:
    """Use only a fresh owned sender session and the authenticated internal actor."""
    keys = (actor_id, intent_id, request_id, expected_previous_generation, expected_config_revision)
    grant, profile = _approval(*keys)
    try:
        sender = _approved_sender(session)
        execution_digest = _execution_digest(actor_id, grant, sender)
        transport = dict(expected_config_revision=expected_config_revision, sender_port=sender.port,
                         sender_database=sender.database, sender_username=sender.username, sender_marker=grant.sourceMarker)
        payload = None
        async with asyncio.timeout(12):
            async with session.begin():
                origin, delivery = await _locked_delivery(
                    session, grant, sender, actor_id, intent_id, request_id, execution_digest,
                )
                digest, count = origin["payload_hash"], origin["row_count"]
                if web_approval is not None:
                    if delivery is not None:
                        raise SnapshotDeliveryAlreadyAdmitted("This delivery was already admitted")
                    web_approval(origin)
                if delivery is None:
                    payload = await _verify_frozen(session, origin)
                    if _approval(*keys) != (grant, profile):
                        raise _unavailable()
                    if web_approval is not None:
                        web_approval(origin)
                    await session.execute(text(f"""
                        INSERT INTO {_PRIVATE}.deliveries (
                            intent_id,request_id,scope_digest,execution_digest,payload_hash,expected_previous_generation,
                            delivery_config_revision,row_count,admitted_at
                        ) VALUES (:intent,:request,:scope,:execution,:digest,:previous,:revision,:count,pg_catalog.clock_timestamp())
                    """), {"intent": intent_id, "request": request_id, "scope": grant.scopeDigest, "execution": execution_digest,
                            "digest": digest, "previous": expected_previous_generation, "revision": expected_config_revision,
                            "count": count})
                    if web_approval is not None:
                        web_approval(origin)
            # Only this first call, AFTER an acknowledged admission commit, may
            # send the frozen payload. Any other path may confirm metadata only.
            if _approval(*keys) != (grant, profile):
                raise _unavailable()
            if payload is not None:
                if web_approval is not None:
                    web_approval(None)
                    transport.update(web_approval=lambda: web_approval(None))
                result = await publish_snapshot(
                    request_id, expected_previous_generation,
                    (SnapshotPublicationRow(id=str(key), caption=caption) for key, caption in payload), **transport,
                )
            else:
                result = await reconcile_snapshot(request_id, expected_previous_generation, digest, **transport)
            if (result.request_id != request_id or result.previous_generation != expected_previous_generation
                or result.generation != expected_previous_generation + 1 or result.row_count != count
                or result.recorded_at.tzinfo is None or result.recorded_at.year in (1, 9999)):
                raise _unavailable()
            async with session.begin():
                current, delivery = await _locked_delivery(
                    session, grant, sender, actor_id, intent_id, request_id, execution_digest,
                )
                if (delivery is None or _approval(*keys) != (grant, profile)
                    or current["payload_hash"] != digest or current["row_count"] != count):
                    raise _unavailable()
                if web_approval is not None:
                    web_approval(current)
                if delivery["receiver_generation"] is None:
                    mirrored = await session.scalar(text(f"""
                        UPDATE ONLY {_PRIVATE}.deliveries
                        SET receiver_generation=:generation,receiver_recorded_at=:recorded,receiver_row_count=:count
                        WHERE intent_id=:intent AND receiver_generation IS NULL RETURNING intent_id
                    """), {"intent": intent_id, "generation": result.generation,
                            "recorded": result.recorded_at, "count": count})
                    if mirrored != intent_id:
                        raise _unavailable()
                elif ((delivery["receiver_generation"], delivery["receiver_recorded_at"], delivery["receiver_row_count"])
                      != (result.generation, result.recorded_at, result.row_count)):
                    raise _unavailable()
            return result
    except SnapshotDeliveryAlreadyAdmitted:
        raise
    except Exception:
        # Includes local/remote COMMIT acknowledgement failure: never dispatch again.
        raise _unavailable() from None
