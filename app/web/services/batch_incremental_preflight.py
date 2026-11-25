"""Verify only the next sealed incremental batch, without admitting or writing a run."""

from dataclasses import dataclass
from datetime import datetime, timezone
from ipaddress import ip_address
import re
from uuid import UUID

from anyio import fail_after
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import select

from app.web.api.models.batch_source_identity import BatchSourceIdentityRow
from app.web.api.models.batch_syncs import (
    INCREMENTAL_TARGET, TARGET_SCHEMA, BatchSyncIncrementalPreflight, BatchSyncIncrementalPreflightRequest,
    BatchSyncPlanRow, managed_batch_scope,
)
from app.web.core.config import settings
from app.web.services.batch_source_identity import expected_fingerprint
from app.web.services.batch_sync_incremental import (
    incremental_preflight_enabled, require_target_shape, same_binding, watermark,
)
from app.web.services.batch_syncs import _check_target_mapping, _plan_response, _target_metadata, _validate_source_revision
from app.web.services.data_connections import get_data_connections, paused_at_sql
from app.web.services.postgres_committed_batch_source import MAX_BATCH_ROWS, read_committed_batch


def _disabled() -> HTTPException:
    return HTTPException(403, "Incremental batch preflight requires explicit local opt-in")


def _unverified(message: str = "The next exact committed batch could not be verified") -> HTTPException:
    return HTTPException(409, {"code": "incremental_preflight_unverified", "message": message})


@dataclass(frozen=True)
class _ObservedReview:
    connection_id: UUID
    plan_updated_at: datetime
    source_updated_at: datetime
    marker: UUID
    stream_digest: str
    fingerprint: str
    source_proof_at: datetime
    source_config: str
    applied_through: int
    target_oid: int
    target_node: int


async def _proof_snapshot(
    session: AsyncSession, plan_id: UUID, request: BatchSyncIncrementalPreflightRequest,
) -> _ObservedReview:
    if not incremental_preflight_enabled():
        raise _disabled()
    engine = session.bind
    if not isinstance(engine, AsyncEngine) or engine.url.query or engine.url.drivername != "postgresql+asyncpg":
        raise _unverified()
    url = engine.url
    try:
        address = ip_address(url.host or "")
    except ValueError:
        raise _unverified() from None
    if not address.is_loopback or "%" in str(address) or not url.database or not url.username:
        raise _unverified()
    async with session.begin():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        await session.execute(text("SET LOCAL statement_timeout='900ms'"))
        await session.execute(text("SET LOCAL lock_timeout='300ms'"))
        await session.execute(text("SET LOCAL search_path=pg_catalog,app,pg_temp"))
        # Only the exact fixed target is pinned, and no write-style lock is
        # held across the independent source connection/read.
        await session.execute(text(
            f'LOCK TABLE ONLY "{TARGET_SCHEMA}"."{INCREMENTAL_TARGET}" IN ACCESS SHARE MODE NOWAIT'
        ))
        if await session.scalar(text("""
            SELECT pg_catalog.inet_server_addr()=CAST(:host AS inet)
              AND pg_catalog.inet_server_port()=:port AND pg_catalog.current_database()=:database
              AND current_user=:role AND session_user=:role
        """), {"host": str(address), "port": url.port or 5432, "database": url.database, "role": url.username}) is not True:
            raise _unverified()
        row = await session.get(BatchSyncPlanRow, plan_id, populate_existing=True)
        if row is None:
            raise HTTPException(404, "Batch sync plan does not exist")
        if row.updated_at != request.expectedUpdatedAt:
            raise HTTPException(409, {"code": "plan_changed", "message": "Batch sync plan changed; reload its review"})
        if row.mode != "incremental" or row.range_min is not None:
            raise _unverified("Use an exactly reviewed incremental batch plan")
        plan = _plan_response(row)
        if not managed_batch_scope(plan):
            raise _unverified("Use an exactly reviewed incremental batch plan")
        if row.source_updated_at != request.expectedSourceUpdatedAt:
            raise HTTPException(409, {"code": "source_revision_changed", "message": "Saved source revision changed"})
        registry = (await session.execute(text(f"""
            SELECT engine,config->>'host' AS host,config::text AS reviewed_config,updated_at,
                   {paused_at_sql('registry')} AS paused_at
            FROM ONLY app.ontology_data_connections registry WHERE id=:id
        """), {"id": row.connection_id})).mappings().first()
        _validate_source_revision(registry, request.expectedSourceUpdatedAt)
        fingerprint = expected_fingerprint()
        marker = settings.BATCH_SYNC_SOURCE_IDENTITY_MARKER
        if (marker is None or fingerprint is None or row.source_identity_fingerprint != fingerprint
            or not re.fullmatch(r"[0-9a-f]{64}", row.source_stream_digest or "")):
            raise _unverified("Verify the reviewed saved PostgreSQL source again")
        identity = await session.scalar(select(BatchSourceIdentityRow).where(
            BatchSourceIdentityRow.connection_id == row.connection_id,
            BatchSourceIdentityRow.source_updated_at == row.source_updated_at,
            BatchSourceIdentityRow.fingerprint == fingerprint, BatchSourceIdentityRow.is_current.is_(True),
        ).execution_options(populate_existing=True))
        if identity is None:
            raise _unverified("Verify the reviewed saved PostgreSQL source again")
        target, columns = await _target_metadata(session, INCREMENTAL_TARGET)
        require_target_shape(target, columns)
        _check_target_mapping(plan.mapping, plan.sourceKeyColumn, target, columns)
        if (target["table_oid"], target["table_node"]) != (row.target_oid, row.target_file_node):
            raise _unverified("The reviewed incremental target changed")
        if await session.scalar(text("""
            SELECT EXISTS (
              SELECT 1 FROM pg_catalog.pg_locks WHERE pid=pg_catalog.pg_backend_pid()
                AND locktype='relation' AND relation=CAST(:oid AS oid) AND mode='AccessShareLock' AND granted
            )
        """), {"oid": row.target_oid}) is not True:
            raise _unverified()
        binding = await watermark(session)
        if not same_binding(binding, row) or not 0 <= binding.applied_through < 2**63:
            raise _unverified("The incremental stream binding changed")
        return _ObservedReview(
            row.connection_id, row.updated_at, row.source_updated_at, marker, row.source_stream_digest, fingerprint,
            identity.verified_at, registry["reviewed_config"], binding.applied_through, row.target_oid, row.target_file_node,
        )


async def preflight_incremental_batch(
    session: AsyncSession, plan_id: UUID, request: BatchSyncIncrementalPreflightRequest,
) -> BatchSyncIncrementalPreflight:
    try:
        with fail_after(10):
            before = await _proof_snapshot(session, plan_id, request)
            if not incremental_preflight_enabled() or settings.BATCH_SYNC_SOURCE_IDENTITY_MARKER != before.marker:
                raise _disabled()
            try:
                batch = await read_committed_batch(
                    get_data_connections(), str(before.connection_id), expected_updated_at=before.source_updated_at,
                    identity_marker=before.marker, stream_digest=before.stream_digest, cursor=before.applied_through,
                )
            except HTTPException:
                raise _unverified() from None
            observed_at = datetime.now(timezone.utc)
            cursor, published, ordinal, count = before.applied_through, batch.published_through, batch.ordinal, len(batch.rows)
            if not cursor <= published < 2**63:
                raise _unverified()
            if ordinal is None:
                if published != cursor or count or batch.digest is not None:
                    raise _unverified()
            elif (ordinal != cursor + 1 or ordinal > published or not 1 <= count <= MAX_BATCH_ROWS
                  or not re.fullmatch(r"[0-9a-f]{64}", batch.digest or "")):
                raise _unverified()
            # This is not a reservation. Keep only safe observations and
            # require the entire local review/cursor to still match afterward.
            del batch
            after = await _proof_snapshot(session, plan_id, request)
            if before != after or not incremental_preflight_enabled() or settings.BATCH_SYNC_SOURCE_IDENTITY_MARKER != before.marker:
                raise _unverified("The incremental review or applied cursor changed")
            return BatchSyncIncrementalPreflight(
                status="matched" if ordinal is not None else "no_new_data",
                observedAppliedThrough=str(cursor), sourcePublishedThrough=str(published),
                nextOrdinal=str(ordinal) if ordinal is not None else None,
                nextRowCount=count if ordinal is not None else None, observedAt=observed_at,
            )
    except (DBAPIError, TimeoutError, ConnectionError, OSError):
        raise HTTPException(503, "Incremental batch preflight could not finish; no batch was run") from None
