"""Separate admin web scope for explicit head review and native sender-only preparation."""

import asyncio
import hmac
from ipaddress import ip_address
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.snapshot_intent_web import (
    SnapshotIntentWebCapability, SnapshotIntentWebHead, SnapshotIntentWebHeadRequest, SnapshotIntentWebPrepared,
    SnapshotIntentWebPrepareRequest,
)
from app.web.core.config import settings
from app.web.services.table_export_snapshot import _sender_scope
from app.web.services.table_export_snapshot_intent import (
    _PRIVATE, _PRIVATE_RELATIONS, _check_sender, _grant_for, _prove_locked_scope, _scope_digest, _unavailable,
    SnapshotPublicationIntentPredecessorChanged, freeze_snapshot_publication_intent,
)
from app.web.services.table_export_snapshot_publication import read_snapshot_publication_head


def _approval(session, actor_id):
    if not settings.TABLE_EXPORT_SNAPSHOT_INTENT_WEB_ENABLED:
        raise _unavailable()
    # The native checker also verifies explicit local, base/intent/publication
    # flags, approval existence & exact fixed profile before any database call.
    grant = settings.TABLE_EXPORT_SNAPSHOT_INTENT_GRANTS.get(actor_id)
    if grant is None:
        raise _unavailable()
    grant, profile = _grant_for(actor_id, grant.configRevision)
    sender = _sender_scope(session)
    approved = make_url(str(settings.SQLALCHEMY_RUNNABLE_DATABASE_URI))
    if (approved.drivername != "postgresql+asyncpg" or approved.query
        or (str(ip_address(approved.host)), approved.port or 5432, approved.database, approved.username)
           != (sender.address, sender.port, sender.database, sender.username)):
        raise _unavailable()
    revision = uuid5(NAMESPACE_URL, "asiral:synthetic-snapshot-intent-web-v1:" + _scope_digest(actor_id, grant, sender))
    return grant, profile, sender, revision


def _eligible(session, actor_id, expected_revision=None):
    approval = _approval(session, actor_id)
    if expected_revision is not None and expected_revision != approval[-1]:
        raise _unavailable()
    return approval


async def _check_private_scope(session, grant, sender):
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    await session.execute(text("SET LOCAL search_path=pg_catalog,pg_temp"))
    await session.execute(text("SET LOCAL lock_timeout='300ms'"))
    await session.execute(text("SET LOCAL statement_timeout='900ms'"))
    names = ", ".join(f'ONLY "{_PRIVATE}"."{table}"' for table in _PRIVATE_RELATIONS)
    await session.execute(text(f"LOCK TABLE {names} IN ACCESS SHARE MODE NOWAIT"))
    await _check_sender(session, sender)
    proof = await _prove_locked_scope(session, _PRIVATE, _PRIVATE_RELATIONS, read_only=True)
    if not hmac.compare_digest(proof, grant.privateProofSha256):
        raise _unavailable()
    markers = (await session.scalars(text(f"""
        SELECT marker FROM ONLY {_PRIVATE}.identity WHERE singleton IS TRUE LIMIT 2
    """))).all()
    if markers != [grant.privateMarker]:
        raise _unavailable()


async def snapshot_intent_web_capability(session: AsyncSession, actor_id: UUID) -> SnapshotIntentWebCapability:
    try:
        approval = _eligible(session, actor_id)
        grant, _, sender, revision = approval
        async with asyncio.timeout(3), session.begin():
            await _check_private_scope(session, grant, sender)
        if _eligible(session, actor_id) == approval:
            return SnapshotIntentWebCapability(available=True, configRevision=revision)
    except Exception:
        pass
    return SnapshotIntentWebCapability(available=False)


async def snapshot_intent_web_head(
    session: AsyncSession, actor_id: UUID, request: SnapshotIntentWebHeadRequest,
) -> SnapshotIntentWebHead:
    try:
        approval = _eligible(session, actor_id, request.expectedConfigRevision)
        grant, profile, sender, _ = approval
        async with asyncio.timeout(7), session.begin():
            await _check_private_scope(session, grant, sender)
            if _eligible(session, actor_id, request.expectedConfigRevision) != approval:
                raise _unavailable()
            head = await read_snapshot_publication_head(
                expected_config_revision=profile.config_revision, sender_port=sender.port, sender_database=sender.database,
                sender_username=sender.username, sender_marker=grant.sourceMarker,
            )
            if _eligible(session, actor_id, request.expectedConfigRevision) != approval:
                raise _unavailable()
        return SnapshotIntentWebHead(generation=head.generation)
    except Exception:
        raise _unavailable() from None


async def snapshot_intent_web_prepare(
    session: AsyncSession, actor_id: UUID, request: SnapshotIntentWebPrepareRequest,
) -> SnapshotIntentWebPrepared:
    try:
        approval = _eligible(session, actor_id, request.expectedConfigRevision)

        def revalidate_web_scope() -> None:
            if _eligible(session, actor_id, request.expectedConfigRevision) != approval:
                raise _unavailable()

        stored = await freeze_snapshot_publication_intent(
            session, actor_id=actor_id, request_id=request.requestId,
            expected_config_revision=approval[1].config_revision, expected_previous_generation=request.expectedPreviousGeneration,
            web_approval=revalidate_web_scope,
        )
        revalidate_web_scope()
        return SnapshotIntentWebPrepared(id=stored.id, expectedPreviousGeneration=stored.expected_previous_generation,
                                         rowCount=stored.row_count, createdAt=stored.created_at)
    except SnapshotPublicationIntentPredecessorChanged:
        raise
    except Exception:
        raise _unavailable() from None
