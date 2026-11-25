"""Capture and compare only stored, bounded synthetic target snapshots."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.cdc_sandbox import CdcSandboxBindingRow, CdcSandboxReceiptRow
from app.web.api.models.cdc_sandbox_versions import (
    CdcSandboxComparison, CdcSandboxVersion, CdcSandboxVersionChange, CdcSandboxVersionDetail,
    CdcSandboxVersionRecord, CdcSandboxVersionRecordRow, CdcSandboxVersionRow, CdcSandboxVersionsPage,
)
from app.web.core.config import settings
from app.web.services.cdc_sandbox import TARGET, _configured_marker, _has_protected_checkpoint
from app.web.services.synthetic_governance import allowed_level, markings, require_marking_access
from app.web.services.transfer_runs import _require_index_range_plan

MAX_VERSION_ROWS = 100
MAX_VERSION_BYTES = 65536
_HISTORY_LIMIT = 25


def _versions_marker() -> UUID | None:
    return _configured_marker() if settings.CDC_SANDBOX_VERSIONS_ENABLED else None


def _view(row: CdcSandboxVersionRow) -> CdcSandboxVersion:
    return CdcSandboxVersion(
        id=row.receipt_id, receiptId=row.receipt_id, generation=row.generation, appliedThrough=str(row.applied_through),
        rowsCaptured=row.rows_captured, bytesCaptured=row.bytes_captured, recordedAt=row.recorded_at,
        markings=markings(row.marking_level),
    )


async def capture_cdc_version(
    session: AsyncSession, binding: CdcSandboxBindingRow, receipt: CdcSandboxReceiptRow, level: int | None,
) -> None:
    """Store a final target snapshot under the apply transaction's already-held target write lock."""
    index = await session.scalar(text("""
        SELECT i.relname FROM pg_catalog.pg_constraint p JOIN pg_catalog.pg_class i ON p.conindid = i.oid
        WHERE p.conrelid = pg_catalog.to_regclass(:target) AND p.contype = 'p' AND NOT p.condeferrable
    """), {"target": TARGET})
    if index is None:
        raise HTTPException(409, "The reviewed target no longer has its bounded primary-key scan")
    bound = {"cap": MAX_VERSION_ROWS + 1}
    metadata_sql = f"""
        SELECT id, pg_catalog.octet_length(value)::bigint + 8 AS bytes
        FROM ONLY {TARGET} ORDER BY id LIMIT :cap
    """
    # First measure a bounded index-ordered prefix without materializing any
    # original values. Do not fetch values at all for over-budget targets.
    await _require_index_range_plan(session, metadata_sql, bound, index, ranged=False)
    metadata = (await session.execute(text(metadata_sql), bound)).all()
    size = sum(row.bytes for row in metadata)
    if not metadata or len(metadata) > MAX_VERSION_ROWS or size > MAX_VERSION_BYTES:
        raise HTTPException(422, "The final target exceeds the version snapshot budget; the synthetic batch rolled back")

    snapshot_sql = f"SELECT id, value FROM ONLY {TARGET} ORDER BY id LIMIT :cap"
    await _require_index_range_plan(session, snapshot_sql, bound, index, ranged=False)
    rows = (await session.execute(text(snapshot_sql), bound)).all()
    if [row.id for row in rows] != [row.id for row in metadata]:
        raise HTTPException(409, "Target rows changed inside the locked snapshot")
    await session.flush()  # The version is owned by this actual applied receipt, not a standalone write.
    session.add(CdcSandboxVersionRow(
        receipt_id=receipt.id, stream_marker=binding.stream_marker, physical_digest=binding.physical_digest,
        generation=binding.generation, applied_through=receipt.applied_through, rows_captured=len(rows),
        bytes_captured=size, recorded_at=receipt.recorded_at, marking_level=level,
    ))
    await session.flush()
    session.add_all([CdcSandboxVersionRecordRow(version_id=receipt.id, entity_id=row.id, value=row.value) for row in rows])


async def _scope(session: AsyncSession, limit: int | None) -> tuple[bool, CdcSandboxBindingRow | None]:
    marker = _versions_marker()
    if marker is None or limit == 0:
        return False, None
    binding = await session.get(CdcSandboxBindingRow, True)
    if binding is not None and (binding.stream_marker != marker or
                                (limit is None and await _has_protected_checkpoint(session, binding))):
        return False, None
    # An old version is historical evidence for the exact reviewed scope.
    # Neither its rows nor its existence come from the current target catalog.
    return True, binding


def _scoped_query(binding: CdcSandboxBindingRow, limit: int | None):
    return select(CdcSandboxVersionRow).where(
        CdcSandboxVersionRow.stream_marker == binding.stream_marker,
        CdcSandboxVersionRow.physical_digest == binding.physical_digest,
        allowed_level(CdcSandboxVersionRow.marking_level, limit),
    )


async def list_cdc_versions(session: AsyncSession, limit: int | None) -> CdcSandboxVersionsPage:
    """List recent stored versions independently of the current target's contents."""
    available, binding = await _scope(session, limit)
    if binding is None:
        return CdcSandboxVersionsPage(available=available, versions=[])
    rows = (await session.scalars(_scoped_query(binding, limit).order_by(
        CdcSandboxVersionRow.recorded_at.desc(), CdcSandboxVersionRow.receipt_id.desc(),
    ).limit(_HISTORY_LIMIT))).all()
    return CdcSandboxVersionsPage(available=available, versions=[_view(row) for row in rows])


async def _version(
    session: AsyncSession, binding: CdcSandboxBindingRow | None, identity: UUID, limit: int | None,
) -> CdcSandboxVersionRow:
    row = (await session.scalar(_scoped_query(binding, limit).where(
        CdcSandboxVersionRow.receipt_id == identity,
    ))) if binding else None
    if row is None:
        raise HTTPException(404, "No stored synthetic version exists for this receipt and reviewed scope")
    return row


async def _history_binding(session: AsyncSession, limit: int | None) -> CdcSandboxBindingRow | None:
    require_marking_access(limit)
    available, binding = await _scope(session, limit)
    if not available:
        raise HTTPException(403, "Synthetic version history is not available for this local scope")
    return binding


async def _read_records(session: AsyncSession, version: CdcSandboxVersionRow) -> dict[int, str]:
    rows = (await session.execute(text("""
        WITH bounded AS (
            SELECT entity_id, value, pg_catalog.octet_length(value)::bigint + 8 AS bytes
            FROM app.ontology_cdc_sandbox_version_rows WHERE version_id = :version
            ORDER BY entity_id LIMIT :cap
        ), measured AS (
            SELECT *, sum(bytes) OVER () AS snapshot_bytes FROM bounded
        )
        SELECT entity_id, CASE WHEN snapshot_bytes <= :byte_cap THEN value END AS value, snapshot_bytes
        FROM measured ORDER BY entity_id
    """), {"version": version.receipt_id, "cap": MAX_VERSION_ROWS + 1, "byte_cap": MAX_VERSION_BYTES})).all()
    if (len(rows) != version.rows_captured or not rows or rows[0].snapshot_bytes != version.bytes_captured
        or any(row.value is None for row in rows)):
        raise HTTPException(409, "Stored synthetic snapshot does not match its captured bounds")
    return {row.entity_id: row.value for row in rows}


async def read_cdc_version(session: AsyncSession, identity: UUID, limit: int | None) -> CdcSandboxVersionDetail:
    """Read an exact stored snapshot; never substitute live rows for a missing version."""
    row = await _version(session, await _history_binding(session, limit), identity, limit)
    values = await _read_records(session, row)
    return CdcSandboxVersionDetail(
        **_view(row).model_dump(),
        rows=[CdcSandboxVersionRecord(id=str(key), value=value) for key, value in values.items()],
    )


async def compare_cdc_versions(
    session: AsyncSession, before_id: UUID, after_id: UUID, limit: int | None,
) -> CdcSandboxComparison:
    """Compute exact, bounded insert/update/delete evidence from two captured versions."""
    binding = await _history_binding(session, limit)
    if before_id == after_id:
        raise HTTPException(422, "Choose two distinct stored versions to compare")
    before_version = await _version(session, binding, before_id, limit)
    after_version = await _version(session, binding, after_id, limit)
    before = await _read_records(session, before_version)
    after = await _read_records(session, after_version)
    changes = []
    for key in sorted(before.keys() | after.keys()):
        previous, current = before.get(key), after.get(key)
        if previous == current:
            continue
        kind = "inserted" if previous is None else "deleted" if current is None else "updated"
        changes.append(CdcSandboxVersionChange(id=str(key), kind=kind, before=previous, after=current))
    return CdcSandboxComparison(fromVersionId=before_id, toVersionId=after_id, changes=changes,
                                markings=markings(before_version.marking_level))
