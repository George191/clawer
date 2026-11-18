"""App-side bindings for the exact local committed batch stream and isolated sink."""

from hashlib import sha256
import json

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.batch_syncs import INCREMENTAL_TARGET, TARGET_SCHEMA, BatchSyncPlanRow, BatchSyncWatermarkRow
from app.web.core.config import settings
from app.web.services.batch_source_identity import expected_fingerprint, identity_required


def incremental_batch_enabled() -> bool:
    return settings.BATCH_SYNC_INCREMENTAL_ENABLED and identity_required() and expected_fingerprint() is not None


def incremental_preflight_enabled() -> bool:
    return bool(settings.BATCH_SYNC_INCREMENTAL_PREFLIGHT_ENABLED and incremental_batch_enabled())


def range_snapshot_enabled() -> bool:
    return settings.BATCH_SYNC_RANGE_SNAPSHOT_ENABLED and identity_required() and expected_fingerprint() is not None


def batch_lineage_enabled() -> bool:
    return (settings.BATCH_SYNC_INCREMENTAL_ENABLED and settings.ENVIRONMENT == "local"
            and "ENVIRONMENT" in settings.model_fields_set)


def require_target_shape(target: dict, columns: dict) -> None:
    if (target["key_columns"] != ["id"] or set(columns) != {"id", "value"}
        or any((columns[name]["type_oid"], columns[name]["typmod"], columns[name]["not_null"]) != shape
               for name, shape in (("id", (20, -1, True)), ("value", (25, -1, True))))):
        raise HTTPException(422, "Use the fixed synthetic bigint ID and non-null text target")


async def lock_incremental_target(session: AsyncSession) -> None:
    await session.execute(text(
        f'LOCK TABLE ONLY "{TARGET_SCHEMA}"."{INCREMENTAL_TARGET}" IN SHARE ROW EXCLUSIVE MODE NOWAIT',
    ))


async def watermark(session: AsyncSession, *, lock: bool = False) -> BatchSyncWatermarkRow | None:
    statement = select(BatchSyncWatermarkRow).where(BatchSyncWatermarkRow.singleton.is_(True))
    if lock:
        statement = statement.with_for_update()
    return await session.scalar(statement.execution_options(populate_existing=True))


def same_binding(row: BatchSyncWatermarkRow | None, plan: BatchSyncPlanRow) -> bool:
    return bool(row and (row.connection_id, row.source_identity_fingerprint, row.source_stream_digest,
                         row.target_oid, row.target_file_node)
                == (plan.connection_id, plan.source_identity_fingerprint, plan.source_stream_digest,
                    plan.target_oid, plan.target_file_node))


async def bind_incremental_target(session: AsyncSession, values: dict) -> BatchSyncWatermarkRow:
    """First review seals the binding. Caller holds the single physical target lock."""
    identity = {key: values[key] for key in (
        "connection_id", "source_identity_fingerprint", "source_stream_digest", "target_oid", "target_file_node",
    )}
    row = await watermark(session, lock=True)
    if row is None:
        if await session.scalar(text(
            f'SELECT 1 FROM ONLY "{TARGET_SCHEMA}"."{INCREMENTAL_TARGET}" LIMIT 1',
        )):
            raise HTTPException(409, "Provision an empty synthetic target before its first stream review")
        row = BatchSyncWatermarkRow(**identity)
        session.add(row)
    elif any(getattr(row, key) != value for key, value in identity.items()):
        raise HTTPException(409, "The synthetic target is already bound to another reviewed stream or physical relation")
    return row


def incremental_ready_clause():
    """Local admission only; actual worker rechecks physical target and source publisher."""
    plan, stream = BatchSyncPlanRow, BatchSyncWatermarkRow
    snapshot = plan.mode == "snapshot"
    if not range_snapshot_enabled():
        snapshot = snapshot & plan.range_min.is_(None)
    if not incremental_batch_enabled():
        return snapshot
    return snapshot | ((plan.mode == "incremental") & select(stream.singleton).where(
        stream.singleton.is_(True), stream.connection_id == plan.connection_id,
        stream.source_identity_fingerprint == plan.source_identity_fingerprint,
        stream.source_stream_digest == plan.source_stream_digest,
        stream.target_oid == plan.target_oid, stream.target_file_node == plan.target_file_node,
    ).correlate(plan).exists())


def watermark_scope(binding: BatchSyncWatermarkRow) -> str:
    """Internal audit identity; never base an asset attempt on mutable plan joins."""
    parts = [
        str(binding.connection_id), binding.source_identity_fingerprint, binding.source_stream_digest,
        binding.target_oid, binding.target_file_node,
    ]
    return sha256(json.dumps(parts, separators=(",", ":")).encode("ascii")).hexdigest()
