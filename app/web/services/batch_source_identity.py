"""Application-side readiness for exact saved source revision and out-of-band marker."""

from datetime import datetime, timezone
from hashlib import sha256
from ipaddress import ip_address
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import false, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.batch_source_identity import BatchSourceIdentityRow, BatchSourceIdentityStatus
from app.web.api.models.batch_syncs import BatchSyncPlanRow
from app.web.core.config import settings
from app.web.services.data_connections import SavedSourcePaused, get_data_connections, paused_at_sql
from app.web.services.postgres_sync_source import SourceIdentityMismatch, review_batch_source


def _explicit_local_batch() -> bool:
    return (settings.BATCH_SYNC_SANDBOX_ENABLED and settings.ENVIRONMENT == "local"
            and "ENVIRONMENT" in settings.model_fields_set)


def identity_required() -> bool:
    return settings.BATCH_SYNC_SOURCE_IDENTITY_ENABLED and _explicit_local_batch()


def expected_fingerprint() -> str | None:
    marker = settings.BATCH_SYNC_SOURCE_IDENTITY_MARKER if identity_required() else None
    return sha256(b"ontology-batch-source/v1\x00" + marker.bytes).hexdigest() if marker else None


def _local_host(host: str | None) -> bool:
    try:
        return "%" not in host and ip_address(host).is_loopback
    except (TypeError, ValueError):
        return False


async def _saved_source(session: AsyncSession, identity: UUID, *, lock: bool = False, optional: bool = False):
    """Read only exact registry metadata; do not return credentials on identity routes."""
    if optional and not await session.scalar(text("SELECT pg_catalog.to_regclass('app.ontology_data_connections')")):
        return None
    row = (await session.execute(text(f"""
        SELECT engine, config->>'host' AS host, updated_at, {paused_at_sql('registry')} AS paused_at
        FROM app.ontology_data_connections registry WHERE id = :id
    """ + (" FOR UPDATE" if lock else "")), {"id": identity})).mappings().first()
    if row is None and not optional:
        raise HTTPException(404, "Saved PostgreSQL source does not exist")
    return row


async def read_source_identity(session: AsyncSession, identity: UUID) -> BatchSourceIdentityStatus:
    required = identity_required()
    row = await _saved_source(session, identity, optional=not required)
    if row is not None and row["paused_at"] is not None:
        return BatchSourceIdentityStatus(state="paused", sourceUpdatedAt=row["updated_at"])
    if not required:
        protected = _explicit_local_batch() and await session.get(BatchSourceIdentityRow, identity) is not None
        return BatchSourceIdentityStatus(state="blocked" if protected else "disabled")
    if row["engine"] != "postgresql" or not _local_host(row["host"]):
        return BatchSourceIdentityStatus(state="disabled")
    saved = row["updated_at"]
    proof = await session.get(BatchSourceIdentityRow, identity)
    expected = expected_fingerprint()
    if proof is None:
        return BatchSourceIdentityStatus(state="unverified", sourceUpdatedAt=saved)
    if not proof.is_current or not expected or proof.fingerprint != expected or proof.source_updated_at != saved:
        return BatchSourceIdentityStatus(state="stale", sourceUpdatedAt=saved)
    return BatchSourceIdentityStatus(state="verified", sourceUpdatedAt=saved, verifiedAt=proof.verified_at)


async def verify_source_identity(
    session: AsyncSession, identity: UUID, revision: datetime,
) -> BatchSourceIdentityStatus:
    marker, expected = settings.BATCH_SYNC_SOURCE_IDENTITY_MARKER, expected_fingerprint()
    if not marker or not expected:
        row = await _saved_source(session, identity, optional=True)
        if row is not None and row["paused_at"] is not None:
            raise SavedSourcePaused()
        raise HTTPException(403, "Reviewed source identity requires explicit local provisioning")
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
    await session.execute(text("SET LOCAL statement_timeout = '8s'"))
    # Serialize verify/failure for the exact saved registry row; concurrent config edit cannot pair with this proof.
    row = await _saved_source(session, identity, lock=True)
    if row["paused_at"] is not None:
        raise SavedSourcePaused()
    if row["engine"] != "postgresql" or not _local_host(row["host"]):
        raise HTTPException(422, "Use an eligible saved numeric-loopback PostgreSQL source")
    if row["updated_at"] != revision:
        raise HTTPException(409, "Saved source settings changed; reload before verifying")
    try:
        await review_batch_source(get_data_connections(), str(identity), revision, marker)
    except SourceIdentityMismatch:
        # Do not erase the ever-verified sentinel: flag-off must not reopen this connection.
        await session.execute(text("""
            UPDATE app.ontology_batch_source_identities SET is_current=false
            WHERE connection_id=:id AND source_updated_at=:revision AND fingerprint=:expected
        """), {"id": identity, "revision": revision, "expected": expected})
        await session.commit()
        raise
    now = datetime.now(timezone.utc)
    values = {"source_updated_at": revision, "fingerprint": expected, "verified_at": now, "is_current": True}
    await session.execute(insert(BatchSourceIdentityRow).values(
        connection_id=identity, **values,
    ).on_conflict_do_update(index_elements=["connection_id"], set_=values))
    await session.commit()
    return BatchSourceIdentityStatus(state="verified", sourceUpdatedAt=revision, verifiedAt=now)


async def require_source_proof(session: AsyncSession, identity: UUID, revision: datetime) -> str:
    expected = expected_fingerprint()
    if not expected:
        raise HTTPException(409, "The expected local PostgreSQL source identity is not provisioned")
    proof = await session.scalar(select(BatchSourceIdentityRow).where(
        BatchSourceIdentityRow.connection_id == identity,
        BatchSourceIdentityRow.source_updated_at == revision,
        BatchSourceIdentityRow.fingerprint == expected,
        BatchSourceIdentityRow.is_current.is_(True),
    ).with_for_update(read=True))
    if proof is None:
        raise HTTPException(409, "Verify this saved PostgreSQL source revision before continuing")
    return expected


async def require_legacy_source_scope(session: AsyncSession, identity: UUID) -> None:
    if await session.get(BatchSourceIdentityRow, identity) is not None:
        raise HTTPException(409, "Reenable the reviewed local PostgreSQL source identity policy")


async def require_plan_source_identity(session: AsyncSession, plan: BatchSyncPlanRow) -> UUID | None:
    """Return runtime marker only after exact saved proof and plan pin are both checked."""
    if not identity_required():
        if plan.source_identity_fingerprint:
            raise HTTPException(409, "Reenable the reviewed local PostgreSQL source identity policy")
        await require_legacy_source_scope(session, plan.connection_id)
        return None
    fingerprint = await require_source_proof(session, plan.connection_id, plan.source_updated_at)
    pinned = bool(plan.source_stream_digest) if plan.mode == "incremental" else bool(plan.source_oid and plan.source_file_node)
    if (plan.source_identity_fingerprint != fingerprint or not pinned
            or settings.BATCH_SYNC_SOURCE_IDENTITY_MARKER is None):
        raise HTTPException(409, "Review and save this batch plan against the verified PostgreSQL source")
    return settings.BATCH_SYNC_SOURCE_IDENTITY_MARKER


def plan_source_ready_clause():
    """Collector/readiness projection only; worker still rechecks proof and live marker."""
    plan, proof = BatchSyncPlanRow, BatchSourceIdentityRow
    expected = expected_fingerprint()
    if expected:
        return (plan.source_identity_fingerprint == expected) & select(proof.connection_id).where(
            proof.connection_id == plan.connection_id, proof.source_updated_at == plan.source_updated_at,
            proof.fingerprint == expected, proof.is_current.is_(True),
        ).correlate(plan).exists()
    if identity_required():
        return false()
    return plan.source_identity_fingerprint.is_(None) & ~select(proof.connection_id).where(
        proof.connection_id == plan.connection_id,
    ).correlate(plan).exists()
