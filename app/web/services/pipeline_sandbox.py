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
from app.web.api.models.cdc_sandbox_versions import CdcSandboxVersionRow
from app.web.api.models.pipeline_sandbox import (
    PipelineIdFilter, PipelineSandboxConfig, PipelineSandboxPreview, PipelineSandboxRevision,
    PipelineSandboxRevisionRow, PipelineSandboxSave, PipelineSandboxStatus,
)
from app.web.core.config import settings
from app.web.services.cdc_sandbox_versions import _read_records, _scope, _version
from app.web.services.synthetic_governance import (
    BASE, RESTRICTED, allowed_level, builder_marking, marking_limit, markings, require_marking_access,
)

_HISTORY_LIMIT = 25


async def _builder_scope(session: AsyncSession, limit: int | None) -> tuple[bool, CdcSandboxBindingRow | None]:
    if not settings.ONTOLOGY_SANDBOX_PIPELINE_ENABLED:
        return False, None
    return await _scope(session, limit)  # The source remains gated by explicit local CDC version authorization.


async def _require_builder(session: AsyncSession, limit: int | None) -> CdcSandboxBindingRow | None:
    require_marking_access(limit)
    available, binding = await _builder_scope(session, limit)
    if not available:
        raise HTTPException(403, "Synthetic Pipeline Builder requires explicit local version and Builder opt-in")
    return binding


def _raw_revisions(binding: CdcSandboxBindingRow):
    return select(PipelineSandboxRevisionRow).where(
        PipelineSandboxRevisionRow.stream_marker == binding.stream_marker,
        PipelineSandboxRevisionRow.physical_digest == binding.physical_digest,
    )


def _revisions(binding: CdcSandboxBindingRow, limit: int | None):
    revision, version = PipelineSandboxRevisionRow, CdcSandboxVersionRow
    query = _raw_revisions(binding).where(allowed_level(revision.marking_level, limit))
    if limit is None:
        return query
    return query.where(select(version.receipt_id).where(
        version.receipt_id == revision.input_version_id, version.stream_marker == revision.stream_marker,
        version.physical_digest == revision.physical_digest, version.marking_level == BASE,
    ).exists())


async def _head(session: AsyncSession, binding: CdcSandboxBindingRow | None, limit: int | None) -> int | None:
    if binding is None:
        return None
    row = await session.scalar(_revisions(binding, limit).order_by(PipelineSandboxRevisionRow.generation.desc()).limit(1))
    return row.generation if row else None


def _hash(config: PipelineSandboxConfig, level: int | None = None) -> str:
    data = config.model_dump(mode="json")
    if level is not None:
        data = {"config": data, "markings": markings(level)}
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _revision(row: PipelineSandboxRevisionRow) -> PipelineSandboxRevision:
    try:
        config = PipelineSandboxConfig.model_validate(row.config)
    except ValidationError:
        raise HTTPException(409, "The stored Builder revision is invalid") from None
    if (config.versionId != row.input_version_id or _hash(config, row.marking_level) != row.config_hash
        or (config.additionalMarking is not None) != (row.marking_level == RESTRICTED)):
        raise HTTPException(409, "The stored Builder revision did not match its exact input and hash")
    return PipelineSandboxRevision(generation=row.generation, configHash=row.config_hash, config=config,
                                   recordedAt=row.recorded_at, markings=markings(row.marking_level))


async def read_pipeline_sandbox(session: AsyncSession, actor_id: UUID) -> PipelineSandboxStatus:
    """Return capability and the exact saved head, not a pipeline run."""
    limit = marking_limit(actor_id)
    available, binding = await _builder_scope(session, limit)
    return PipelineSandboxStatus(
        available=available, latestGeneration=await _head(session, binding, limit),
        governanceEnabled=True if limit is not None else None,
        allowedMarkings=(markings(limit) or []) if limit is not None else None,
        canSave=(available and limit == RESTRICTED) if limit is not None else None,
    )


async def list_pipeline_revisions(session: AsyncSession, actor_id: UUID) -> list[PipelineSandboxRevision]:
    """Return a bounded history of immutable configuration revisions."""
    limit = marking_limit(actor_id)
    binding = await _require_builder(session, limit)
    if binding is None:
        return []
    rows = (await session.scalars(_revisions(binding, limit).order_by(
        PipelineSandboxRevisionRow.generation.desc(),
    ).limit(_HISTORY_LIMIT))).all()
    return [_revision(row) for row in rows]


async def read_pipeline_revision(session: AsyncSession, generation: int, actor_id: UUID) -> PipelineSandboxRevision:
    """Read a single saved revision in the current reviewed local scope."""
    limit = marking_limit(actor_id)
    binding = await _require_builder(session, limit)
    row = (await session.scalar(_revisions(binding, limit).where(
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


def project_pipeline_records(records: dict[int, str], config: PipelineSandboxConfig) -> list[list[str]]:
    """Project actual bounded snapshot rows in exact numeric ID order."""
    return [[str(key) if column == "id" else value for column in config.projection]
            for key, value in sorted(records.items()) if _matches(key, value, config)]


async def lock_builder_writes(session: AsyncSession) -> None:
    """Serialize saved review changes with outputs admitted against their exact head."""
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    await session.execute(text("SELECT pg_catalog.pg_advisory_xact_lock(1649157671, 275192983)"))


async def preview_pipeline_sandbox(
    session: AsyncSession, config: PipelineSandboxConfig, actor_id: UUID,
) -> PipelineSandboxPreview:
    """Filter and project the exact stored input; do not create a revision or output receipt."""
    limit = marking_limit(actor_id)
    binding = await _require_builder(session, limit)
    level = builder_marking(limit, config.additionalMarking)
    records = await _read_records(session, await _version(session, binding, config.versionId, limit))
    rows = project_pipeline_records(records, config)
    return PipelineSandboxPreview(versionId=config.versionId, configHash=_hash(config, level), inputRows=len(records),
                                  matchedRows=len(rows), columns=config.projection, rows=rows, markings=markings(level))


async def save_pipeline_revision(
    session: AsyncSession, payload: PipelineSandboxSave, actor_id: UUID,
) -> PipelineSandboxRevision:
    """Append a new config revision only against the caller's reviewed generation."""
    limit = marking_limit(actor_id)
    binding = await _require_builder(session, limit)
    if limit == BASE:
        raise HTTPException(403, "Both synthetic markings are required to manage the shared Builder head")
    level = builder_marking(limit, payload.config.additionalMarking)
    await _version(session, binding, payload.config.versionId, limit)
    try:
        await lock_builder_writes(session)
        latest = await session.scalar(_raw_revisions(binding).order_by(
            PipelineSandboxRevisionRow.generation.desc(),
        ).limit(1))
        if latest is not None and latest.marking_level is not None and limit is None:
            raise HTTPException(403, "The protected Builder head requires local governance access")
        current = await _head(session, binding, limit)
        if current != payload.expectedGeneration:
            raise HTTPException(409, "The saved Builder generation changed; reload before saving")
        actual_generation = latest.generation if latest else 0
        if actual_generation == 2147483647:
            raise HTTPException(409, "The Builder generation limit has been reached")
        row = PipelineSandboxRevisionRow(
            generation=actual_generation + 1, stream_marker=binding.stream_marker, physical_digest=binding.physical_digest,
            input_version_id=payload.config.versionId, config_hash=_hash(payload.config, level), marking_level=level,
            config=payload.config.model_dump(mode="json"), recorded_at=datetime.now(timezone.utc), saved_by=actor_id,
        )
        session.add(row)
        result = _revision(row)
        await session.commit()
        return result
    except HTTPException:
        await session.rollback()
        raise
    except IntegrityError as error:
        await session.rollback()
        if getattr(error.orig, "sqlstate", None) == "23505":
            raise HTTPException(409, "Another request saved this Builder generation; reload before saving") from None
        raise HTTPException(503, "The Builder revision could not be saved") from None
    except DBAPIError:
        await session.rollback()
        raise HTTPException(503, "The Builder revision save was not confirmed; reload the saved history") from None
