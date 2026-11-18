"""Explicit web authority to make only the first exact authorized synthetic admission."""

import asyncio
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.snapshot_delivery_web import (
    SnapshotDeliveryWebCapability, SnapshotDeliveryWebLookup, SnapshotDeliveryWebReceipt, SnapshotDeliveryWebReview,
    SnapshotDeliveryWebRun,
)
from app.web.core.config import settings
from app.web.services.table_export_snapshot_delivery import (
    SnapshotDeliveryAlreadyAdmitted, _approval, _approved_sender, _execution_digest, _locked_delivery,
    deliver_snapshot_publication_intent,
)


class SnapshotWebDeliveryUnavailable(Exception):
    """The request is outside the exact approved one-shot review."""


def _scope(session, actor_id, intent_id=None, request_id=None):
    if not settings.TABLE_EXPORT_SNAPSHOT_DELIVERY_WEB_ENABLED:
        raise SnapshotWebDeliveryUnavailable
    grant = settings.TABLE_EXPORT_SNAPSHOT_DELIVERY_GRANTS.get(actor_id)
    if (grant is None or (intent_id is not None and grant.intentId != intent_id)
            or (request_id is not None and grant.requestId != request_id)):
        raise SnapshotWebDeliveryUnavailable
    grant, profile = _approval(actor_id, grant.intentId, grant.requestId, grant.expectedPreviousGeneration,
                               grant.configRevision)
    sender = _approved_sender(session)
    return grant, profile, sender, _execution_digest(actor_id, grant, sender)


def _revision(scope, origin) -> UUID:
    # Bind the execution scope, pinned physical ledgers and exact frozen manifest
    # without transmitting the payload digest or the grant as web metadata.
    return uuid5(NAMESPACE_URL, "asiral:synthetic-snapshot-delivery-web-v1:"
                 f'{scope[-1]}:{origin["payload_hash"]}:{origin["row_count"]}')


async def _read_review(session, actor, requested_scope):
    grant, _, sender, execution = requested_scope
    async with asyncio.timeout(3), session.begin():
        origin, prior = await _locked_delivery(
            session, grant, sender, actor, grant.intentId, grant.requestId, execution, read_only=True,
        )
        if prior is not None or _scope(session, actor, grant.intentId, grant.requestId) != requested_scope:
            raise SnapshotWebDeliveryUnavailable
    return SnapshotDeliveryWebReview(rowCount=origin["row_count"],
                                    expectedPreviousGeneration=grant.expectedPreviousGeneration,
                                    webRevision=_revision(requested_scope, origin))


async def snapshot_delivery_web_capability(session: AsyncSession, actor: UUID) -> SnapshotDeliveryWebCapability:
    try:
        await _read_review(session, actor, _scope(session, actor))
    except Exception:
        return SnapshotDeliveryWebCapability(available=False)
    return SnapshotDeliveryWebCapability(available=True)


async def snapshot_delivery_web_review(
    session: AsyncSession, actor: UUID, request: SnapshotDeliveryWebLookup,
) -> SnapshotDeliveryWebReview:
    try:
        return await _read_review(session, actor, _scope(session, actor, request.intentId, request.requestId))
    except Exception:
        raise SnapshotWebDeliveryUnavailable from None


async def snapshot_delivery_web_run(
    session: AsyncSession, actor: UUID, request: SnapshotDeliveryWebRun,
) -> SnapshotDeliveryWebReceipt:
    try:
        scope = _scope(session, actor, request.intentId, request.requestId)
        reviewed = await _read_review(session, actor, scope)
        if reviewed.webRevision != request.expectedWebRevision:
            raise SnapshotWebDeliveryUnavailable
    except Exception:
        raise SnapshotWebDeliveryUnavailable from None
    grant, profile, _, _ = scope

    def revalidate(origin) -> None:
        if (_scope(session, actor, request.intentId, request.requestId) != scope
                or (origin is not None and _revision(scope, origin) != request.expectedWebRevision)):
            raise SnapshotWebDeliveryUnavailable

    try:
        result = await deliver_snapshot_publication_intent(
            session, actor_id=actor, intent_id=request.intentId, request_id=request.requestId,
            expected_previous_generation=grant.expectedPreviousGeneration, expected_config_revision=profile.config_revision,
            web_approval=revalidate,
        )
    except SnapshotDeliveryAlreadyAdmitted:
        raise SnapshotWebDeliveryUnavailable from None
    return SnapshotDeliveryWebReceipt(intentId=request.intentId, requestId=result.request_id, generation=result.generation,
                                      rowCount=result.row_count, recordedAt=result.recorded_at)
