"""Fixed-scope local Push inbox; no downstream sink or acceptance implied by status."""

import base64
import binascii
from hmac import compare_digest
from ipaddress import ip_address
import re
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.push_inbox import (
    MAX_INTAKE_GENERATION, RECENT_LIMIT, TOTAL_LIMIT, WINDOW_LIMIT, WINDOW_SECONDS, PushInboxAcceptance,
    PushInboxIntakeChange, PushInboxReceiptRow,
    PushInboxStateRow, PushInboxStatus,
)
from app.web.core.config import settings

_EXPECTED_INET = text("""
    (pg_catalog.inet_server_addr() <<= '127.0.0.0/8'::inet OR pg_catalog.inet_server_addr() = '::1'::inet)
""")


def inbox_opted_in() -> bool:
    return (settings.PUSH_INBOX_SANDBOX_ENABLED and settings.ENVIRONMENT == "local"
            and "ENVIRONMENT" in settings.model_fields_set)


def strict_base64(value: bytes, size: int) -> bytes | None:
    if not isinstance(value, bytes) or len(value) != ((size + 2) // 3) * 4:
        return None
    try:
        decoded = base64.b64decode(value, validate=True)
        return decoded if len(decoded) == size and base64.b64encode(decoded) == value else None
    except (ValueError, binascii.Error):
        return None


def _public_key_bytes(configured) -> bytes | None:
    if configured is None:
        return None
    try:
        return strict_base64(configured.get_secret_value().encode("ascii"), 32)
    except (AttributeError, UnicodeError, ValueError):
        return None


def producer_key(*, secondary: bool = False) -> Ed25519PublicKey | None:
    if not inbox_opted_in():
        return None
    primary = _public_key_bytes(settings.PUSH_INBOX_SANDBOX_PUBLIC_KEY)
    if primary is None:
        return None
    if not secondary:
        return Ed25519PublicKey.from_public_bytes(primary)
    if not settings.PUSH_INBOX_SANDBOX_SECONDARY_ENABLED:
        return None
    alternate = _public_key_bytes(settings.PUSH_INBOX_SANDBOX_SECONDARY_PUBLIC_KEY)
    if alternate is None or alternate == primary:
        return None
    return Ed25519PublicKey.from_public_bytes(alternate)


def local_inbox_database() -> URL | None:
    """Validate the already-configured application DSN without network resolution."""
    try:
        url = make_url(settings.SQLALCHEMY_RUNNABLE_DATABASE_URI.unicode_string())
        if (url.drivername != "postgresql+asyncpg" or url.query or not url.host or "%" in url.host
            or not ip_address(url.host).is_loopback or not url.database
            or not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_-]{0,62}", url.database)):
            return None
        return url
    except Exception:
        # Configuration errors need not disclose connection credentials.
        return None


def event_path_opted_in() -> bool:
    return (settings.PUSH_INBOX_SANDBOX_EVENT_PATH_ENABLED and inbox_opted_in()
            and settings.PUSH_INBOX_SANDBOX_DATABASE_MARKER is not None and local_inbox_database() is not None)


def configured() -> bool:
    return (inbox_opted_in() and settings.PUSH_INBOX_SANDBOX_DATABASE_MARKER is not None
            and producer_key() is not None and local_inbox_database() is not None)


async def _state(session: AsyncSession, *, lock: bool) -> PushInboxStateRow | None:
    marker = settings.PUSH_INBOX_SANDBOX_DATABASE_MARKER
    if marker is None:
        return None
    statement = select(PushInboxStateRow).where(
        PushInboxStateRow.singleton.is_(True), _EXPECTED_INET,
    )
    if lock:
        statement = statement.with_for_update()
    state = await session.scalar(statement)
    return state if state is not None and state.database_marker == marker else None


def _receipt(row: PushInboxReceiptRow) -> PushInboxAcceptance:
    return PushInboxAcceptance(id=row.id, ordinal=str(row.ordinal), acceptedAt=row.accepted_at)


def intake_control_enabled() -> bool:
    return settings.PUSH_INBOX_SANDBOX_INTAKE_CONTROL_ENABLED and inbox_opted_in()


async def _intake_state(session: AsyncSession):
    # Preserve reference-only pre-migration deployments. Post-migration the values are
    # read from the singleton already locked by accept/change, or in the status snapshot.
    return (await session.execute(text("""
        SELECT (pg_catalog.to_jsonb(s)->>'intake_generation')::integer AS generation,
               (pg_catalog.to_jsonb(s)->>'intake_paused_at')::timestamptz AS paused_at
        FROM app.ontology_push_inbox_state s WHERE s.singleton IS TRUE
    """))).one()


async def _status(session: AsyncSession, state: PushInboxStateRow) -> PushInboxStatus:
    generation, paused_at = await _intake_state(session)
    changeable = intake_control_enabled() and generation is not None
    receipt = PushInboxReceiptRow
    recent = (await session.execute(select(receipt.id, receipt.ordinal, receipt.accepted_at)
                                   .order_by(receipt.ordinal.desc()).limit(RECENT_LIMIT))).all()
    return PushInboxStatus(
        available=True, state="paused" if paused_at is not None else "full" if state.accepted_count >= TOTAL_LIMIT else "ready",
        acceptedCount=state.accepted_count, intakeControlAvailable=True if changeable else None,
        intakeGeneration=generation if changeable or generation else None, pausedAt=paused_at,
        secondaryProducerAvailable=True if producer_key(secondary=True) is not None else None,
        eventPathAvailable=True if event_path_opted_in() else None,
        recent=[PushInboxAcceptance(id=identity, ordinal=str(ordinal), acceptedAt=at) for identity, ordinal, at in recent],
    )


async def read_push_status(session: AsyncSession) -> PushInboxStatus:
    """Return only accepted-receipt summaries. Never load digest, request IDs or payload."""
    if not inbox_opted_in():
        return PushInboxStatus(available=False, state="disabled")
    if not configured():
        return PushInboxStatus(available=False, state="unconfigured")
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL search_path = pg_catalog, app, pg_temp"))
    await session.execute(text("SET LOCAL statement_timeout = '1250ms'"))
    state = await _state(session, lock=False)
    if state is None:
        return PushInboxStatus(available=False, state="unconfigured")
    return await _status(session, state)


async def change_push_intake(session: AsyncSession, change: PushInboxIntakeChange) -> PushInboxStatus:
    """Confirm one current local intake state; this neither cancels receipts nor pauses apply."""
    if not intake_control_enabled():
        raise HTTPException(403, "Synthetic inbox intake control is disabled")
    if not configured():
        raise HTTPException(503, "Synthetic inbox unavailable")
    async with session.begin():
        await session.execute(text("SET LOCAL search_path = pg_catalog, app, pg_temp"))
        await session.execute(text("SET LOCAL lock_timeout = '500ms'"))
        await session.execute(text("SET LOCAL statement_timeout = '1250ms'"))
        state = await _state(session, lock=True)
        if state is None:
            raise HTTPException(503, "Synthetic inbox unavailable")
        generation, paused_at = await _intake_state(session)
        if generation is None:
            raise HTTPException(503, "Synthetic inbox intake control is unavailable")
        if generation != change.expectedGeneration:
            raise HTTPException(409, {"code": "intake_generation_changed", "message": "Reload the current inbox intake state"})
        if (paused_at is not None) != change.paused:
            if generation == MAX_INTAKE_GENERATION:
                raise HTTPException(409, "Synthetic inbox intake generation limit reached")
            await session.execute(text("""
                UPDATE app.ontology_push_inbox_state SET intake_generation=intake_generation+1,
                    intake_paused_at=CASE WHEN :paused THEN pg_catalog.clock_timestamp() ELSE NULL END
                WHERE singleton IS TRUE
            """), {"paused": change.paused})
        return await _status(session, state)


async def accept_push(
    session: AsyncSession, request_id: UUID, body_digest: str, payload: tuple[int, str] | None,
) -> PushInboxAcceptance:
    """Called only after current signature and timestamp authorization; atomically reserve and publish one acceptance."""
    async with session.begin():
        await session.execute(text("SET LOCAL search_path = pg_catalog, app, pg_temp"))
        await session.execute(text("SET LOCAL lock_timeout = '500ms'"))
        await session.execute(text("SET LOCAL statement_timeout = '1250ms'"))
        state = await _state(session, lock=True)
        if state is None:
            raise HTTPException(503, "Synthetic inbox unavailable")
        receipt = PushInboxReceiptRow
        previous = (await session.execute(select(receipt.id, receipt.ordinal, receipt.accepted_at, receipt.body_digest)
                                          .where(receipt.request_id == request_id))).one_or_none()
        if previous is not None:
            if not compare_digest(previous.body_digest, body_digest):
                raise HTTPException(409, "This request ID has a different accepted body")
            return PushInboxAcceptance(id=previous.id, ordinal=str(previous.ordinal), acceptedAt=previous.accepted_at)
        _, paused_at = await _intake_state(session)
        if paused_at is not None:
            raise HTTPException(503, "Synthetic inbox unavailable")
        if payload is None:
            raise HTTPException(422, "Invalid synthetic fixture record")
        now = await session.scalar(text("SELECT pg_catalog.clock_timestamp()"))
        interval = int(now.timestamp()) // WINDOW_SECONDS
        if state.window_epoch is not None and interval < state.window_epoch:
            raise HTTPException(503, "Synthetic inbox unavailable")
        used = state.window_accepted if state.window_epoch == interval else 0
        if state.accepted_count >= TOTAL_LIMIT or used >= WINDOW_LIMIT:
            raise HTTPException(429, "Synthetic inbox acceptance limit reached")
        saved = PushInboxReceiptRow(
            request_id=request_id, ordinal=state.accepted_count + 1, body_digest=body_digest,
            schema_version=1, entity_id=payload[0], fixture_value=payload[1], accepted_at=now,
        )
        session.add(saved)
        state.accepted_count = saved.ordinal
        state.window_epoch = interval
        state.window_accepted = used + 1
        await session.flush()
        return _receipt(saved)
