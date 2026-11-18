"""Exact sender-local metadata only; no source, publisher, or receiver access."""

import asyncio
import hmac
from ipaddress import ip_address
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.snapshot_delivery_evidence import (
    SnapshotDeliveryEvidence, SnapshotDeliveryEvidenceCapability, SnapshotDeliveryEvidenceRequest,
)
from app.web.core.config import settings
from app.web.services.table_export_snapshot import _sender_scope
from app.web.services.table_export_snapshot_intent import (
    _DELIVERY_RELATIONS, _PRIVATE, _PRIVATE_RELATIONS, _check_sender, _prove_locked_scope,
)


class SnapshotDeliveryEvidenceUnavailable(Exception):
    """There is no authorized verified readback for this exact scope."""


def _approval(actor, session):
    if (not isinstance(actor, UUID) or settings.ENVIRONMENT != "local" or "ENVIRONMENT" not in settings.model_fields_set
        or not settings.TABLE_EXPORT_SANDBOX_ENABLED or not settings.TABLE_EXPORT_SNAPSHOT_DELIVERY_EVIDENCE_ENABLED):
        raise SnapshotDeliveryEvidenceUnavailable()
    grant = settings.TABLE_EXPORT_SNAPSHOT_DELIVERY_EVIDENCE_GRANTS.get(actor)
    if (grant is None or not grant.privateMarker.int or not grant.configRevision.int
        or grant.intentId.version != 4 or grant.requestId.version != 4):
        raise SnapshotDeliveryEvidenceUnavailable()
    sender = _sender_scope(session)
    approved = make_url(str(settings.SQLALCHEMY_RUNNABLE_DATABASE_URI))
    if (approved.drivername != "postgresql+asyncpg" or approved.query
        or (str(ip_address(approved.host)), approved.port or 5432, approved.database, approved.username)
           != (sender.address, sender.port, sender.database, sender.username)):
        raise SnapshotDeliveryEvidenceUnavailable()
    scope = json.dumps(
        ["asiral:synthetic-snapshot-delivery-evidence-v1", str(actor), grant.model_dump(mode="json"),
         sender.address, sender.port, sender.database, sender.username],
        separators=(",", ":"), sort_keys=True,
    )
    return grant, sender, uuid5(NAMESPACE_URL, scope)


async def _verify_scope(session, grant, sender):
    await _check_sender(session, sender)
    if await session.scalar(text("""
        SELECT pg_catalog.has_schema_privilege(:schema,'USAGE')
           AND pg_catalog.has_table_privilege(:identity,'SELECT')
           AND pg_catalog.has_table_privilege(:intents,'SELECT')
           AND pg_catalog.has_table_privilege(:deliveries,'SELECT')
    """), {"schema": _PRIVATE, "identity": f"{_PRIVATE}.identity", "intents": f"{_PRIVATE}.intents",
            "deliveries": f"{_PRIVATE}.deliveries"}) is not True:
        raise SnapshotDeliveryEvidenceUnavailable()
    origin_proof = await _prove_locked_scope(session, _PRIVATE, _PRIVATE_RELATIONS, read_only=True)
    delivery_proof = await _prove_locked_scope(session, _PRIVATE, _DELIVERY_RELATIONS, read_only=True)
    if not (hmac.compare_digest(origin_proof, grant.privateProofSha256)
            and hmac.compare_digest(delivery_proof, grant.deliveryProofSha256)):
        raise SnapshotDeliveryEvidenceUnavailable()
    markers = (await session.scalars(text(f"""
        SELECT marker FROM ONLY {_PRIVATE}.identity WHERE singleton IS TRUE LIMIT 2
    """))).all()
    if markers != [grant.privateMarker]:
        raise SnapshotDeliveryEvidenceUnavailable()


async def _start_read(session, grant, sender):
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    await session.execute(text("SET LOCAL search_path=pg_catalog,pg_temp"))
    await session.execute(text("SET LOCAL lock_timeout='300ms'"))
    await session.execute(text("SET LOCAL statement_timeout='900ms'"))
    relations = ", ".join(f'ONLY "{_PRIVATE}"."{table}"' for table in (*_PRIVATE_RELATIONS, *_DELIVERY_RELATIONS))
    await session.execute(text(f"LOCK TABLE {relations} IN ACCESS SHARE MODE NOWAIT"))
    await _verify_scope(session, grant, sender)


async def read_snapshot_delivery_evidence_capability(session: AsyncSession, actor_id: UUID) -> SnapshotDeliveryEvidenceCapability:
    """Capability checks enrollment and physical scope but never enumerates intent metadata."""
    try:
        grant, sender, revision = _approval(actor_id, session)
        async with asyncio.timeout(3), session.begin():
            await _start_read(session, grant, sender)
        if _approval(actor_id, session) == (grant, sender, revision):
            return SnapshotDeliveryEvidenceCapability(available=True, configRevision=revision)
    except Exception:
        pass
    return SnapshotDeliveryEvidenceCapability(available=False)


async def read_snapshot_delivery_evidence(
    session: AsyncSession, actor_id: UUID, request: SnapshotDeliveryEvidenceRequest,
) -> SnapshotDeliveryEvidence:
    try:
        grant, sender, revision = _approval(actor_id, session)
        if (request.intentId, request.requestId, request.expectedConfigRevision) != (
            grant.intentId, grant.requestId, revision,
        ):
            raise SnapshotDeliveryEvidenceUnavailable()
        async with asyncio.timeout(3), session.begin():
            await _start_read(session, grant, sender)
            binds = {
                "intent": grant.intentId, "request": grant.requestId, "owner": grant.ownerActorId,
                "previous": grant.expectedPreviousGeneration, "revision": grant.configRevision,
                "scope": grant.scopeDigest, "execution": grant.executionDigest,
            }
            origin_count = await session.scalar(text(f"""
                SELECT row_count FROM ONLY {_PRIVATE}.intents
                WHERE id=:intent AND request_id=:request AND actor_id=:owner
                  AND expected_previous_generation=:previous AND config_revision=:revision
                  AND scope_digest=:scope AND row_count BETWEEN 1 AND 100 AND pg_catalog.isfinite(created_at)
                  AND created_at <= pg_catalog.statement_timestamp()
                  AND payload_hash ~ '^[0-9a-f]{{64}}$'
            """), binds)
            if origin_count is None:
                raise SnapshotDeliveryEvidenceUnavailable()
            candidates = (await session.execute(text(f"""
                SELECT COALESCE(
                    intent_id=:intent AND request_id=:request AND expected_previous_generation=:previous
                    AND delivery_config_revision=:revision AND scope_digest=:scope AND execution_digest=:execution
                    AND row_count=:count AND pg_catalog.isfinite(admitted_at)
                    AND admitted_at <= pg_catalog.statement_timestamp()
                    AND admitted_at >= (SELECT created_at FROM ONLY {_PRIVATE}.intents WHERE id=:intent)
                    AND payload_hash=(SELECT payload_hash FROM ONLY {_PRIVATE}.intents WHERE id=:intent), false
                ) AS valid, receiver_generation AS generation, receiver_recorded_at AS recorded_at,
                receiver_row_count AS rows_recorded,
                pg_catalog.isfinite(receiver_recorded_at)
                    AND receiver_recorded_at <= pg_catalog.statement_timestamp() AS credible_clock
                FROM ONLY {_PRIVATE}.deliveries
                WHERE intent_id=:intent OR request_id=:request LIMIT 2
            """), {**binds, "count": origin_count})).mappings().all()
            if not candidates:
                result = SnapshotDeliveryEvidence(state="not_admitted")
            elif len(candidates) != 1 or not candidates[0]["valid"]:
                raise SnapshotDeliveryEvidenceUnavailable()
            else:
                row = candidates[0]
                gen, recorded, count = row["generation"], row["recorded_at"], row["rows_recorded"]
                if gen is None and recorded is None and count is None:
                    result = SnapshotDeliveryEvidence(state="unconfirmed")
                elif (gen == grant.expectedPreviousGeneration + 1 and count == origin_count and recorded is not None
                      and row["credible_clock"] and recorded.tzinfo is not None and recorded.year not in (1, 9999)):
                    result = SnapshotDeliveryEvidence(
                        state="confirmed_recorded_receipt", generation=gen, rowCount=count, recordedAt=recorded,
                    )
                else:
                    raise SnapshotDeliveryEvidenceUnavailable()
            await _verify_scope(session, grant, sender)
            if _approval(actor_id, session) != (grant, sender, revision):
                raise SnapshotDeliveryEvidenceUnavailable()
        return result
    except Exception:
        raise SnapshotDeliveryEvidenceUnavailable() from None
