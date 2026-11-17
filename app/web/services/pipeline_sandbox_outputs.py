"""Commit only exact reviewed synthetic projections to dedicated immutable output storage."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.cdc_sandbox import CdcSandboxBindingRow
from app.web.api.models.cdc_sandbox_versions import CdcSandboxVersionRow
from app.web.api.models.pipeline_sandbox import PipelineSandboxRevisionRow
from app.web.api.models.pipeline_sandbox_outputs import (
    PipelineSandboxCommit, PipelineSandboxOutput, PipelineSandboxOutputDetail, PipelineSandboxOutputRecordRow,
    PipelineSandboxOutputRow, PipelineSandboxOutputsPage,
)
from app.web.core.config import settings
from app.web.services.cdc_sandbox_versions import _read_records, _version
from app.web.services.pipeline_sandbox import (
    _builder_scope, _raw_revisions, _revision, lock_builder_writes, project_pipeline_records,
)
from app.web.services.synthetic_governance import (
    BASE, allowed_level, builder_marking, marking_limit, markings, require_marking_access,
)

MAX_OUTPUT_ROWS = 100
MAX_OUTPUT_BYTES = 65536
_HISTORY_LIMIT = 25


async def _output_scope(session: AsyncSession, limit: int | None) -> tuple[bool, CdcSandboxBindingRow | None]:
    if not settings.ONTOLOGY_SANDBOX_PIPELINE_OUTPUT_ENABLED:
        return False, None
    return await _builder_scope(session, limit)


async def _require_output_scope(session: AsyncSession, limit: int | None) -> CdcSandboxBindingRow | None:
    require_marking_access(limit)
    available, binding = await _output_scope(session, limit)
    if not available:
        raise HTTPException(403, "Committed synthetic Builder outputs require explicit local output opt-in")
    return binding


def _raw_outputs(binding: CdcSandboxBindingRow):
    return select(PipelineSandboxOutputRow).where(
        PipelineSandboxOutputRow.stream_marker == binding.stream_marker,
        PipelineSandboxOutputRow.physical_digest == binding.physical_digest,
    )


def _outputs(binding: CdcSandboxBindingRow, limit: int | None):
    output, revision, version = PipelineSandboxOutputRow, PipelineSandboxRevisionRow, CdcSandboxVersionRow
    query = _raw_outputs(binding).where(allowed_level(output.marking_level, limit))
    if limit is None:
        return query
    return query.where(select(revision.generation).join(version, version.receipt_id == revision.input_version_id).where(
        revision.generation == output.reviewed_generation,
        revision.input_version_id == output.input_version_id,
        revision.config_hash == output.config_hash,
        revision.marking_level == output.marking_level,
        revision.stream_marker == output.stream_marker,
        revision.physical_digest == output.physical_digest,
        version.stream_marker == output.stream_marker,
        version.physical_digest == output.physical_digest,
        version.marking_level == BASE,
    ).exists())


def _receipt(row: PipelineSandboxOutputRow) -> PipelineSandboxOutput:
    return PipelineSandboxOutput(
        id=row.id, requestId=row.request_id, reviewedGeneration=row.reviewed_generation, configHash=row.config_hash,
        resultHash=row.result_hash, versionId=row.input_version_id, columns=row.columns,
        inputRows=row.input_rows, outputRows=row.output_rows, recordedAt=row.recorded_at, markings=markings(row.marking_level),
    )


def _seal(output: PipelineSandboxOutputRow, rows: list[list[str]]) -> str:
    data = {"columns": output.columns, "rows": rows}
    if output.marking_level is not None:
        data.update(
            markings=markings(output.marking_level), reviewedGeneration=output.reviewed_generation,
            configHash=output.config_hash, versionId=str(output.input_version_id),
            streamMarker=str(output.stream_marker), physicalDigest=output.physical_digest,
        )
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _byte_size(rows: list[list[str]]) -> int:
    return sum(len(value.encode("utf-8")) for row in rows for value in row)


async def _stored_rows(session: AsyncSession, output: PipelineSandboxOutputRow) -> list[list[str]]:
    """Read bounded sealed data from output storage and verify it against its receipt."""
    records = (await session.execute(text("""
        WITH bounded AS (
            SELECT position, row_values, pg_catalog.octet_length(pg_catalog.convert_to(row_values[1], 'UTF8'))
                + coalesce(pg_catalog.octet_length(pg_catalog.convert_to(row_values[2], 'UTF8')), 0) AS bytes
            FROM public.ontology_pipeline_sandbox_output_rows WHERE output_id = :identity
            ORDER BY position LIMIT :row_cap
        ), measured AS (
            SELECT *, sum(bytes) OVER () AS total_bytes FROM bounded
        )
        SELECT position, CASE WHEN total_bytes <= :byte_cap THEN row_values END AS row_values, total_bytes
        FROM measured ORDER BY position
    """), {"identity": output.id, "row_cap": MAX_OUTPUT_ROWS + 1, "byte_cap": MAX_OUTPUT_BYTES})).all()
    if (len(records) != output.output_rows or (records[0].total_bytes if records else 0) != output.output_bytes
        or any(record.position != position or record.row_values is None
               or len(record.row_values) != len(output.columns) for position, record in enumerate(records, 1))):
        raise HTTPException(409, "The stored Builder output does not match its sealed row bounds")
    rows = [record.row_values for record in records]
    if _seal(output, rows) != output.result_hash:
        raise HTTPException(409, "The stored Builder output does not match its result seal")
    return rows


async def list_pipeline_outputs(session: AsyncSession, actor_id: UUID) -> PipelineSandboxOutputsPage:
    """List recent committed output receipts, not saved configuration revisions."""
    limit = marking_limit(actor_id)
    available, binding = await _output_scope(session, limit)
    if binding is None:
        return PipelineSandboxOutputsPage(available=available, outputs=[])
    rows = (await session.scalars(_outputs(binding, limit).order_by(
        PipelineSandboxOutputRow.recorded_at.desc(), PipelineSandboxOutputRow.id.desc(),
    ).limit(_HISTORY_LIMIT))).all()
    return PipelineSandboxOutputsPage(available=available, outputs=[_receipt(row) for row in rows])


async def read_pipeline_output(session: AsyncSession, identity: UUID, actor_id: UUID) -> PipelineSandboxOutputDetail:
    """Read one sealed immutable output by receipt, without reevaluating its input."""
    limit = marking_limit(actor_id)
    binding = await _require_output_scope(session, limit)
    row = (await session.scalar(_outputs(binding, limit).where(PipelineSandboxOutputRow.id == identity))) if binding else None
    if row is None:
        raise HTTPException(404, "No committed Builder output exists for this scope")
    return PipelineSandboxOutputDetail(**_receipt(row).model_dump(), rows=await _stored_rows(session, row))


async def commit_pipeline_output(
    session: AsyncSession, request: PipelineSandboxCommit, actor_id: UUID,
) -> PipelineSandboxOutput:
    """Commit a reviewed projection and one receipt atomically, or reconcile an exact prior request."""
    limit = marking_limit(actor_id)
    binding = await _require_output_scope(session, limit)
    if binding is None:
        raise HTTPException(409, "Review the synthetic input scope before committing Builder output")
    try:
        await lock_builder_writes(session)
        prior = await session.scalar(_outputs(binding, limit).where(PipelineSandboxOutputRow.request_id == request.requestId))
        if prior is not None:
            if (prior.reviewed_generation != request.expectedGeneration
                or prior.config_hash != request.expectedConfigHash):
                raise HTTPException(409, "This request ID is bound to a different reviewed Builder configuration")
            await _stored_rows(session, prior)
            return _receipt(prior)  # Reconcile from committed output storage before querying any config or input.
        if limit == BASE:
            raise HTTPException(403, "Both synthetic markings are required to manage the shared Builder head")
        if prior is None and await session.scalar(_raw_outputs(binding).where(
            PipelineSandboxOutputRow.request_id == request.requestId,
        ).with_only_columns(PipelineSandboxOutputRow.id)):
            raise HTTPException(404, "No authorized synthetic output exists for this request")
        revision = await session.scalar(_raw_revisions(binding).where(
            allowed_level(PipelineSandboxRevisionRow.marking_level, limit),
        ).order_by(
            PipelineSandboxRevisionRow.generation.desc(),
        ).limit(1))
        if (revision is None or revision.generation != request.expectedGeneration
            or revision.config_hash != request.expectedConfigHash):
            raise HTTPException(409, "The latest reviewed Builder generation or hash changed; review before committing")
        if limit is None and await session.scalar(_raw_revisions(binding).where(
            PipelineSandboxRevisionRow.generation > revision.generation,
        ).with_only_columns(PipelineSandboxRevisionRow.generation).limit(1)):
            raise HTTPException(403, "The protected Builder head requires local governance access")
        config = _revision(revision).config
        level = builder_marking(limit, config.additionalMarking)
        if level != revision.marking_level:
            raise HTTPException(404, "No authorized synthetic revision exists for this request")
        source = await _read_records(session, await _version(session, binding, config.versionId, limit))
        rows = project_pipeline_records(source, config)
        size = _byte_size(rows)
        if len(rows) > MAX_OUTPUT_ROWS or size > MAX_OUTPUT_BYTES:
            raise HTTPException(422, "The projected Builder output exceeds its row or UTF-8 byte budget; nothing was committed")
        output = PipelineSandboxOutputRow(
            request_id=request.requestId, reviewed_generation=revision.generation, config_hash=revision.config_hash,
            result_hash="", marking_level=level,
            input_version_id=config.versionId, stream_marker=binding.stream_marker,
            physical_digest=binding.physical_digest, columns=config.projection, input_rows=len(source), output_rows=len(rows),
            output_bytes=size, recorded_at=datetime.now(timezone.utc), committed_by=actor_id,
        )
        output.result_hash = _seal(output, rows)
        session.add(output)
        await session.flush()
        session.add_all([PipelineSandboxOutputRecordRow(output_id=output.id, position=position, row_values=row)
                         for position, row in enumerate(rows, 1)])
        receipt = _receipt(output)
        await session.commit()
        return receipt
    except HTTPException:
        await session.rollback()
        raise
    except Exception:
        await session.rollback()
        raise HTTPException(503, "Builder output commit was not confirmed; retry the same request ID") from None
