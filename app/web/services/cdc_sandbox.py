"""Replay the fixed synthetic PostgreSQL after-image stream, never production CDC."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.cdc_sandbox import (
    CdcSandboxApply, CdcSandboxBindingRow, CdcSandboxReceipt, CdcSandboxReceiptRow, CdcSandboxStatus,
)
from app.web.core.config import settings
from app.web.services.transfer_runs import _require_index_range_plan, _sandbox_tables

SCHEMA = "ts_cdc_sandbox"
SOURCE = f"{SCHEMA}.event_log"
TARGET = f"{SCHEMA}.target_rows"
HEAD = f"{SCHEMA}.stream_head"
MAX_ROWS = 50
MAX_BYTES = 65536
_TYPES = {
    "stream_head": {"marker": "uuid", "ordinal": "int8"},
    "event_log": {"ordinal": "int8", "entity_id": "int8", "value": "text"},
    "target_rows": {"id": "int8", "value": "text"},
}
_KEYS = {"stream_head": ["marker"], "event_log": ["ordinal"], "target_rows": ["id"]}


def _configured_marker() -> UUID | None:
    if settings.ENVIRONMENT != "local" or "ENVIRONMENT" not in settings.model_fields_set or not settings.CDC_SANDBOX_ENABLED:
        return None
    return settings.CDC_SANDBOX_STREAM_MARKER


def _require_local() -> UUID:
    marker = _configured_marker()
    if marker is None:
        raise HTTPException(403, "Synthetic CDC requires explicit local provisioning and enablement")
    return marker


async def _catalog(session: AsyncSession) -> tuple[str, str] | None:
    shapes = {row["name"]: row for pair in (("stream_head", "event_log"), ("target_rows", "target_rows"))
              for row in await _sandbox_tables(session, *pair, schema=SCHEMA)}
    if shapes.keys() != _TYPES.keys() or any(
        list(row["key_columns"] or []) != _KEYS[name] or row["table_node"] in (None, 0)
        for name, row in shapes.items()
    ):
        return None
    attributes = (await session.execute(text("""
        SELECT c.relname, a.attname, typename.typname, a.attnotnull,
               a.attgenerated = '' AND a.attidentity = '', c.relpersistence = 'p'
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
        JOIN pg_catalog.pg_type typename ON typename.oid = a.atttypid
        WHERE n.nspname = :schema AND c.relname IN ('stream_head', 'event_log', 'target_rows')
          AND a.attnum > 0 AND NOT a.attisdropped
    """), {"schema": SCHEMA})).all()
    actual = {name: {} for name in _TYPES}
    for table, column, kind, required, ordinary, persistent in attributes:
        if not required or not ordinary or not persistent:
            return None
        actual[table][column] = kind
    if actual != _TYPES:
        return None
    index_name = await session.scalar(text("""
        SELECT i.relname FROM pg_catalog.pg_constraint p
        JOIN pg_catalog.pg_class i ON i.oid = p.conindid
        WHERE p.conrelid = :source_oid AND p.contype = 'p' AND NOT p.condeferrable
    """), {"source_oid": shapes["event_log"]["table_oid"]})
    if not index_name:
        return None
    descriptor = [(name, shapes[name]["table_oid"], shapes[name]["table_node"]) for name in sorted(shapes)]
    return sha256(json.dumps(descriptor, separators=(",", ":")).encode()).hexdigest(), index_name


async def _head(session: AsyncSession, marker: UUID) -> int | None:
    rows = (await session.execute(text(f"SELECT marker, ordinal FROM ONLY {HEAD} LIMIT 2"))).all()
    if len(rows) != 1 or rows[0][0] != marker or rows[0][1] < 0:
        return None
    return rows[0][1]


async def _lock_fixture(session: AsyncSession, *, applying: bool) -> tuple[str, str]:
    before = await _catalog(session)
    if before is None:
        raise HTTPException(409, "Provision the exact synthetic CDC fixture before continuing")
    # Fixed names only. SHARE excludes the synthetic producer while we apply
    # a prefix; a still-uncommitted producer cannot be read or block forever.
    source_lock = "SHARE" if applying else "ACCESS SHARE"
    target_lock = "SHARE ROW EXCLUSIVE" if applying else "ACCESS SHARE"
    await session.execute(text(f"LOCK TABLE ONLY {SOURCE}, ONLY {HEAD} IN {source_lock} MODE NOWAIT"))
    await session.execute(text(f"LOCK TABLE ONLY {TARGET} IN {target_lock} MODE NOWAIT"))
    after = await _catalog(session)
    if after is None or after != before:
        raise HTTPException(409, "Synthetic CDC relations changed during review")
    return after


def _status(
    row: CdcSandboxBindingRow | None, *, available: bool, blocked: bool, high_water: int | None,
) -> CdcSandboxStatus:
    state = "blocked" if blocked else "armed" if row else "unreviewed"
    return CdcSandboxStatus(
        available=available, state=state, generation=row.generation if row else None,
        appliedThrough=str(row.applied_through) if row else "0",
        sourceHighWater=str(high_water) if high_water is not None else None,
        source=SOURCE, target=TARGET, maxRows=MAX_ROWS, maxBytes=MAX_BYTES,
    )


def _receipt(row: CdcSandboxReceiptRow) -> CdcSandboxReceipt:
    return CdcSandboxReceipt(
        id=row.id, requestId=row.request_id, generation=row.generation,
        fromExclusive=str(row.from_exclusive), appliedThrough=str(row.applied_through),
        rowsAffected=row.rows_affected, recordedAt=row.recorded_at,
    )


async def _limits(session: AsyncSession) -> None:
    await session.execute(text("SET LOCAL search_path = pg_catalog, public, pg_temp"))
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))


def _db_failure(error: Exception) -> HTTPException:
    state = getattr(getattr(error, "orig", None), "sqlstate", None)
    if state in ("55P03", "57014", "40P01", "42P01", "40001"):
        return HTTPException(409, "The synthetic fixture is changing, busy, or not committed; retry the same request")
    return HTTPException(503, "Synthetic apply was not confirmed; retry the same request ID")


async def read_cdc_sandbox(session: AsyncSession) -> CdcSandboxStatus:
    """Show the reviewed checkpoint separately from the current source high-water."""
    marker = _configured_marker()
    if marker is None:
        return _status(None, available=False, blocked=True, high_water=None)
    await _limits(session)
    binding = await session.get(CdcSandboxBindingRow, True)
    physical = await _catalog(session)
    if physical is None:
        return _status(binding, available=True, blocked=True, high_water=None)
    if binding and (binding.stream_marker != marker or binding.physical_digest != physical[0]):
        return _status(binding, available=True, blocked=True, high_water=None)
    high_water = await _head(session, marker)
    return _status(binding, available=True, blocked=high_water is None or
                   bool(binding and high_water < binding.applied_through), high_water=high_water)


async def review_cdc_sandbox(session: AsyncSession, actor_id: UUID) -> CdcSandboxStatus:
    """Review the immutable local binding once, without creating fixture tables or resetting progress."""
    marker = _require_local()
    try:
        await _limits(session)
        if await session.get(CdcSandboxBindingRow, True) is not None:
            raise HTTPException(409, "This synthetic stream was already reviewed; its checkpoint cannot be reset or rebound")
        physical, _ = await _lock_fixture(session, applying=False)
        high_water = await _head(session, marker)
        if high_water is None:
            raise HTTPException(409, "The synthetic stream provisioning marker did not match")
        row = CdcSandboxBindingRow(stream_marker=marker, physical_digest=physical, reviewed_by=actor_id,
                                   reviewed_at=datetime.now(timezone.utc))
        session.add(row)
        result = _status(row, available=True, blocked=False, high_water=high_water)
        await session.commit()
        return result
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "This synthetic stream was already reviewed; it cannot be rebound") from None
    except DBAPIError as error:
        await session.rollback()
        raise _db_failure(error) from None


async def list_cdc_sandbox_receipts(session: AsyncSession) -> list[CdcSandboxReceipt]:
    """List recent immutable receipts without reading the source or modifying the checkpoint."""
    _require_local()
    rows = (await session.scalars(select(CdcSandboxReceiptRow).order_by(
        CdcSandboxReceiptRow.recorded_at.desc(), CdcSandboxReceiptRow.id.desc(),
    ).limit(25))).all()
    return [_receipt(row) for row in rows]


_RANGE = f"SELECT ordinal, entity_id, value FROM ONLY {SOURCE} " \
         "WHERE ordinal > CAST(:low AS bigint) AND ordinal <= CAST(:high AS bigint) ORDER BY ordinal LIMIT :limit"


async def _read_prefix(session: AsyncSession, low: int, high: int, index: str) -> list:
    should_read = min(MAX_ROWS, high - low)
    args = {"low": low, "high": high, "limit": should_read}
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    await session.execute(text("SET LOCAL enable_bitmapscan = off"))
    await _require_index_range_plan(session, _RANGE, args, index, ranged=True)
    # Project no more than the byte cap into Python. Metadata is still
    # returned for the whole bounded window so gaps cannot be overlooked.
    rows = (await session.execute(text(f"""
        WITH bounded AS MATERIALIZED ({_RANGE}),
        weighted AS (
            SELECT ordinal, entity_id, value, pg_catalog.octet_length(value)::bigint + 16 AS bytes FROM bounded
        ),
        prefix AS (
            SELECT *, sum(bytes) OVER (ORDER BY ordinal ROWS UNBOUNDED PRECEDING) AS prefix_bytes FROM weighted
        )
        SELECT ordinal, entity_id, CASE WHEN prefix_bytes <= :byte_cap THEN value END AS value, bytes
        FROM prefix ORDER BY ordinal
    """), {**args, "byte_cap": MAX_BYTES})).all()
    if len(rows) != should_read or any(row.ordinal != low + index + 1 for index, row in enumerate(rows)):
        raise HTTPException(409, "A committed ordinal is missing; the checkpoint did not advance")
    if any(row.bytes > MAX_BYTES for row in rows):
        raise HTTPException(422, "A synthetic event exceeds the after-image byte limit; the checkpoint did not advance")
    return [row for row in rows if row.value is not None]


async def _write_after_images(session: AsyncSession, events: list) -> int:
    # Multiple after-images for an entity within the same committed prefix
    # resolve to its greatest ordinal; never skip an ordinal in the checkpoint.
    return (await session.execute(text(f"""
        WITH latest AS (
            SELECT DISTINCT ON (id) id, value
            FROM ROWS FROM (pg_catalog.unnest(CAST(:ids AS bigint[])),
                            pg_catalog.unnest(CAST(:values AS text[]))) WITH ORDINALITY AS e(id, value, n)
            ORDER BY id, n DESC
        ), written AS (
            INSERT INTO {TARGET} (id, value)
            SELECT id, value FROM latest ORDER BY id
            ON CONFLICT (id) DO UPDATE SET value = EXCLUDED.value
            RETURNING 1
        )
        SELECT count(*) FROM written
    """), {"ids": [event.entity_id for event in events], "values": [event.value for event in events]})).scalar_one()


async def apply_cdc_sandbox(session: AsyncSession, request: CdcSandboxApply, actor_id: UUID) -> CdcSandboxReceipt:
    """Apply a bounded contiguous prefix, committing sink writes, checkpoint and one receipt atomically."""
    marker = _require_local()
    try:
        await _limits(session)
        binding = await session.scalar(select(CdcSandboxBindingRow).where(
            CdcSandboxBindingRow.singleton.is_(True),
        ).with_for_update().execution_options(populate_existing=True))
        if binding is None:
            raise HTTPException(409, "Review this synthetic stream before applying committed events")
        expected = int(request.expectedAppliedThrough)
        receipt = await session.scalar(select(CdcSandboxReceiptRow).where(
            CdcSandboxReceiptRow.request_id == request.requestId,
        ))
        if receipt is not None:
            if receipt.generation != request.expectedGeneration or receipt.from_exclusive != expected:
                raise HTTPException(409, "This request ID is bound to a different generation or starting cursor")
            return _receipt(receipt)  # Never consult the source for an already committed request.
        if binding.generation != request.expectedGeneration or binding.applied_through != expected:
            raise HTTPException(409, "Review the current applied cursor before applying another batch")
        if binding.stream_marker != marker:
            raise HTTPException(409, "The reviewed synthetic stream marker changed; no reset is allowed")
        physical, index = await _lock_fixture(session, applying=True)
        if binding.physical_digest != physical:
            raise HTTPException(409, "The reviewed synthetic relations were replaced or rewritten")
        high_water = await _head(session, marker)
        if high_water is None or high_water < expected:
            raise HTTPException(409, "The reviewed source marker or committed high-water changed")
        if high_water == expected:
            raise HTTPException(409, "No committed event is available after the applied cursor")
        events = await _read_prefix(session, expected, high_water, index)
        affected = await _write_after_images(session, events)
        binding.applied_through = events[-1].ordinal
        row = CdcSandboxReceiptRow(
            request_id=request.requestId, generation=binding.generation, from_exclusive=expected,
            applied_through=binding.applied_through, rows_affected=affected, applied_by=actor_id,
            recorded_at=datetime.now(timezone.utc),
        )
        session.add(row)
        if settings.CDC_SANDBOX_VERSIONS_ENABLED:
            from app.web.services.cdc_sandbox_versions import capture_cdc_version

            await capture_cdc_version(session, binding, row)
        result = _receipt(row)
        await session.commit()
        return result
    except HTTPException:
        await session.rollback()
        raise
    except Exception as error:
        await session.rollback()
        raise _db_failure(error) from None
