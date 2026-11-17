"""Preview typed transformations of immutable synthetic snapshots, without output writes."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.cdc_sandbox import CdcSandboxBindingRow
from app.web.api.models.pipeline_sandbox import (
    PipelineIdFilter, PipelineSandboxConfig, PipelineSandboxPreview, PipelineSandboxRevision,
    PipelineSandboxRevisionRow, PipelineSandboxSave, PipelineSandboxStatus,
)
from app.web.core.config import settings
from app.web.services.cdc_sandbox_versions import _read_records, _scope, _version

_HISTORY_LIMIT = 25


async def _builder_scope(session: AsyncSession) -> tuple[bool, CdcSandboxBindingRow | None]:
    if not settings.ONTOLOGY_SANDBOX_PIPELINE_ENABLED:
        return False, None
    return await _scope(session)  # The source remains gated by explicit local CDC version authorization.


async def _require_builder(session: AsyncSession) -> CdcSandboxBindingRow | None:
    available, binding = await _builder_scope(session)
    if not available:
        raise HTTPException(403, "Synthetic Pipeline Builder requires explicit local version and Builder opt-in")
    return binding


def _revisions(binding: CdcSandboxBindingRow):
    return select(PipelineSandboxRevisionRow).where(
        PipelineSandboxRevisionRow.stream_marker == binding.stream_marker,
        PipelineSandboxRevisionRow.physical_digest == binding.physical_digest,
    )


async def _head(session: AsyncSession, binding: CdcSandboxBindingRow | None) -> int | None:
    if binding is None:
        return None
    row = await session.scalar(_revisions(binding).order_by(PipelineSandboxRevisionRow.generation.desc()).limit(1))
    return row.generation if row else None


def _hash(config: PipelineSandboxConfig) -> str:
    canonical = json.dumps(config.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _revision(row: PipelineSandboxRevisionRow) -> PipelineSandboxRevision:
    try:
        config = PipelineSandboxConfig.model_validate(row.config)
    except ValidationError:
        raise HTTPException(409, "The stored Builder revision is invalid") from None
    if config.versionId != row.input_version_id or _hash(config) != row.config_hash:
        raise HTTPException(409, "The stored Builder revision did not match its exact input and hash")
    return PipelineSandboxRevision(generation=row.generation, configHash=row.config_hash, config=config,
                                   recordedAt=row.recorded_at)


async def read_pipeline_sandbox(session: AsyncSession) -> PipelineSandboxStatus:
    """Return capability and the exact saved head, not a pipeline run."""
    available, binding = await _builder_scope(session)
    return PipelineSandboxStatus(available=available, latestGeneration=await _head(session, binding))


async def list_pipeline_revisions(session: AsyncSession) -> list[PipelineSandboxRevision]:
    """Return a bounded history of immutable configuration revisions."""
    binding = await _require_builder(session)
    if binding is None:
        return []
    rows = (await session.scalars(_revisions(binding).order_by(
        PipelineSandboxRevisionRow.generation.desc(),
    ).limit(_HISTORY_LIMIT))).all()
    return [_revision(row) for row in rows]


async def read_pipeline_revision(session: AsyncSession, generation: int) -> PipelineSandboxRevision:
    """Read a single saved revision in the current reviewed local scope."""
    binding = await _require_builder(session)
    row = (await session.scalar(_revisions(binding).where(
        PipelineSandboxRevisionRow.generation == generation,
    ))) if binding else None
    if row is None:
        raise HTTPException(404, "No saved Builder revision exists for this reviewed scope")
    return _revision(row)


def _matches(key: int, value: str, config: PipelineSandboxConfig) -> bool:
    for clause in config.filters:
        if isinstance(clause, PipelineIdFilter):
            target = int(clause.value)
            if clause.operator == "eq" and key != target:
                return False
            if clause.operator == "gte" and key < target:
                return False
            if clause.operator == "lte" and key > target:
                return False
        elif clause.operator == "eq" and value != clause.value:
            return False
        elif clause.operator == "prefix" and not value.startswith(clause.value):
            return False
    return True


async def preview_pipeline_sandbox(
    session: AsyncSession, config: PipelineSandboxConfig,
) -> PipelineSandboxPreview:
    """Filter and project the exact stored input; do not create a revision or output receipt."""
    binding = await _require_builder(session)
    records = await _read_records(session, await _version(session, binding, config.versionId))
    rows = [[str(key) if column == "id" else value for column in config.projection]
            for key, value in records.items() if _matches(key, value, config)]
    return PipelineSandboxPreview(versionId=config.versionId, configHash=_hash(config), inputRows=len(records),
                                  matchedRows=len(rows), columns=config.projection, rows=rows)


async def save_pipeline_revision(
    session: AsyncSession, payload: PipelineSandboxSave, actor_id: UUID,
) -> PipelineSandboxRevision:
    """Append a new config revision only against the caller's reviewed generation."""
    binding = await _require_builder(session)
    await _version(session, binding, payload.config.versionId)
    current = await _head(session, binding)
    if current != payload.expectedGeneration:
        raise HTTPException(409, "The saved Builder generation changed; reload before saving")
    if current == 2147483647:
        raise HTTPException(409, "The Builder generation limit has been reached")
    row = PipelineSandboxRevisionRow(
        generation=(current or 0) + 1, stream_marker=binding.stream_marker, physical_digest=binding.physical_digest,
        input_version_id=payload.config.versionId, config_hash=_hash(payload.config),
        config=payload.config.model_dump(mode="json"), recorded_at=datetime.now(timezone.utc), saved_by=actor_id,
    )
    try:
        await session.execute(text("SET LOCAL lock_timeout = '2s'"))
        session.add(row)
        result = _revision(row)
        await session.commit()
        return result
    except IntegrityError as error:
        await session.rollback()
        if getattr(error.orig, "sqlstate", None) == "23505":
            raise HTTPException(409, "Another request saved this Builder generation; reload before saving") from None
        raise HTTPException(503, "The Builder revision could not be saved") from None
    except DBAPIError:
        await session.rollback()
        raise HTTPException(503, "The Builder revision save was not confirmed; reload the saved history") from None
