"""Read-only comparison for the fixed source and one independent preview profile."""

from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import ip_address
import json
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.web.api.models.table_exports import (
    TableExportSnapshotPreview, TableExportSnapshotPreviewCapability, TableExportSnapshotPreviewRequest,
)
from app.web.core.config import settings
from app.web.services.table_export_snapshot_receiver import (
    SnapshotReceiverProfile, SnapshotReceiverUnavailable, assert_snapshot_sender_distinct,
    configured_snapshot_profile, read_snapshot_preview,
)
from app.web.services.table_export_source import MAX_EXPORT_ROWS, _read_source, require_export_sender_marker


_MAX_SNAPSHOT_BYTES = 64 * 1024


def _unavailable() -> HTTPException:
    return HTTPException(403, "Synthetic snapshot comparison is not provisioned for this local scope")


def _profile() -> SnapshotReceiverProfile | None:
    if ("ENVIRONMENT" not in settings.model_fields_set or settings.ENVIRONMENT != "local"
        or not settings.TABLE_EXPORT_SANDBOX_ENABLED or settings.TABLE_EXPORT_SOURCE_MARKER is None):
        return None
    return configured_snapshot_profile()


@dataclass(frozen=True)
class _SenderScope:
    address: str
    port: int
    database: str
    username: str


def _sender_scope(session: AsyncSession) -> _SenderScope:
    engine = session.bind
    if not isinstance(engine, AsyncEngine) or engine.url.drivername != "postgresql+asyncpg" or engine.url.query:
        raise _unavailable()
    url = engine.url
    try:
        address = ip_address(url.host)
    except (TypeError, ValueError):
        raise _unavailable() from None
    port = url.port or 5432
    if not address.is_loopback or "%" in str(address) or not 1 <= port <= 65535 or not url.database or not url.username:
        raise _unavailable()
    return _SenderScope(str(address), port, url.database, url.username)


def _snapshot_revision(profile: SnapshotReceiverProfile, sender: _SenderScope, sender_marker: UUID) -> UUID:
    # The receiver derives its own nonsecret revision from all enrolled
    # transport/pin fields. Bind that plus this exact sender; no password hash.
    scope = [
        "asiral:table-export-snapshot-preview:v1", str(profile.config_revision), str(sender_marker),
        sender.address, sender.port, sender.database, sender.username,
    ]
    return uuid5(NAMESPACE_URL, json.dumps(scope, separators=(",", ":")))


async def _verify_sender(
    session: AsyncSession, profile: SnapshotReceiverProfile, marker: UUID, sender: _SenderScope,
) -> None:
    try:
        assert_snapshot_sender_distinct(
            profile, sender_port=sender.port, sender_marker=marker,
            sender_database=sender.database, sender_username=sender.username,
        )
    except SnapshotReceiverUnavailable:
        raise _unavailable() from None

    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL search_path = pg_catalog, pg_temp"))
    await session.execute(text("SET LOCAL statement_timeout='1750ms'"))
    await session.execute(text("SET LOCAL lock_timeout='500ms'"))
    if await session.scalar(text("""
        SELECT pg_catalog.inet_server_addr() = CAST(:address AS inet)
           AND pg_catalog.inet_server_port() = :port
           AND pg_catalog.current_database() = :database
           AND current_user = :username AND session_user = :username
    """), {"address": sender.address, "port": sender.port, "database": sender.database, "username": sender.username}) is not True:
        raise _unavailable()
    try:
        await require_export_sender_marker(session, marker)
    except HTTPException as exc:
        if exc.status_code == 503:
            raise HTTPException(503, "Synthetic snapshot comparison is temporarily unavailable") from None
        raise _unavailable() from None


async def read_snapshot_preview_capability(session: AsyncSession) -> TableExportSnapshotPreviewCapability:
    """Review only the local configuration and sender marker; never dial the preview receiver."""
    profile, marker = _profile(), settings.TABLE_EXPORT_SOURCE_MARKER
    if profile is None or marker is None:
        return TableExportSnapshotPreviewCapability(available=False)
    try:
        sender = _sender_scope(session)
        async with session.begin():
            await _verify_sender(session, profile, marker, sender)
    except HTTPException:
        return TableExportSnapshotPreviewCapability(available=False)
    return TableExportSnapshotPreviewCapability(available=True, configRevision=_snapshot_revision(profile, sender, marker))


def _index_rows(rows: Sequence[tuple[int, str | None]], *, source: bool) -> dict[int, str | None]:
    if (source and not rows) or len(rows) > MAX_EXPORT_ROWS:
        raise HTTPException(422 if source else 503, "The synthetic snapshot is outside the approved comparison bounds")
    result = {}
    total_bytes = 0
    for key, caption in rows:
        if type(key) is not int or not -(2**63) <= key < 2**63 or key in result:
            raise HTTPException(503, "The exact synthetic snapshot could not be verified")
        if caption is None:
            if source:
                raise HTTPException(422, "The synthetic source is outside the approved comparison bounds")
        elif not isinstance(caption, str):
            raise HTTPException(503, "The exact synthetic snapshot could not be verified")
        total_bytes += 8 + (len(caption.encode("utf-8")) if caption is not None else 0)
        if total_bytes > _MAX_SNAPSHOT_BYTES:
            raise HTTPException(422 if source else 503, "The synthetic snapshot is outside the approved comparison bounds")
        result[key] = caption
    return result


async def compare_table_export_snapshots(
    session: AsyncSession, request: TableExportSnapshotPreviewRequest,
) -> TableExportSnapshotPreview:
    profile, marker = _profile(), settings.TABLE_EXPORT_SOURCE_MARKER
    if profile is None or marker is None:
        raise _unavailable()
    sender = _sender_scope(session)
    revision = _snapshot_revision(profile, sender, marker)
    if revision != request.expectedConfigRevision:
        raise HTTPException(409, "The snapshot comparison profile changed; reload before previewing")
    async with session.begin():
        await _verify_sender(session, profile, marker, sender)
        try:
            # Reuse the exact fixed source query, shape checks, catalog locks and index
            # check already used by Table Export, within this verified sender snapshot.
            rows = await _read_source(session, marker, snapshot_preview=True)
        except HTTPException as exc:
            if exc.status_code == 422:
                raise HTTPException(422, "The synthetic source is outside the approved comparison bounds") from None
            raise _unavailable() from None
        source = _index_rows(rows, source=True)

    # Deliberately pass only deployment-owned connection scope: no sender keys,
    # values or digests are needed or sent to take an independent target snapshot.
    try:
        targets = await read_snapshot_preview(
            profile, sender_port=sender.port, sender_marker=marker,
            sender_database=sender.database, sender_username=sender.username,
        )
    except SnapshotReceiverUnavailable:
        raise HTTPException(503, "The approved snapshot comparison receiver is unavailable") from None
    target = _index_rows(tuple((row.business_key, row.caption) for row in targets), source=False)
    shared = source.keys() & target.keys()
    same = sum(source[key] == target[key] for key in shared)
    return TableExportSnapshotPreview(
        configRevision=revision,
        sourceRows=len(source), targetRows=len(target),
        added=len(source.keys() - target.keys()), changed=len(shared) - same,
        unchanged=same, wouldRemove=len(target.keys() - source.keys()),
    )
