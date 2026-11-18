"""Commit only exact reviewed synthetic projections to dedicated immutable output storage."""

from collections.abc import Sequence
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models.cdc_sandbox import CdcSandboxBindingRow
from app.web.api.models.pipeline_sandbox import PipelineSandboxConfig, PipelineSandboxRevisionRow
from app.web.api.models.pipeline_sandbox_outputs import (
    PipelineSandboxCommit, PipelineSandboxOutput, PipelineSandboxOutputChange, PipelineSandboxOutputCompareRequest,
    PipelineSandboxOutputComparison, PipelineSandboxOutputDetail, PipelineSandboxOutputRecordRow,
    PipelineSandboxOutputRow, PipelineSandboxOutputsPage, PipelineSandboxRecordHistory,
    PipelineSandboxRecordHistoryRequest, PipelineSandboxRecordHistoryStep,
)
from app.web.core.config import settings
from app.web.services.cdc_sandbox_versions import _read_records
from app.web.services.pipeline_sandbox import (
    _builder_scope, _match_version_query, _raw_revisions, _revision, _revisions, lock_builder_writes, project_pipeline_records,
    read_match_ids, resolve_pipeline_inputs,
)
from app.web.services.synthetic_governance import (
    BASE, RESTRICTED, allowed_level, marking_limit, markings, require_marking_access,
)

MAX_OUTPUT_ROWS = 100
MAX_OUTPUT_BYTES = 65536
_HISTORY_LIMIT = 25
_RECORD_HISTORY_MAX_ROWS = 500
_RECORD_HISTORY_MAX_BYTES = 320 * 1024
_RECORD_HISTORY_RESPONSE_BYTES = 128 * 1024


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
    output, revision = PipelineSandboxOutputRow, PipelineSandboxRevisionRow
    query = _raw_outputs(binding).where(allowed_level(output.marking_level, limit))
    ancestry = _revisions(binding, limit).where(
        revision.generation == output.reviewed_generation,
        revision.input_version_id == output.input_version_id,
        revision.match_version_id.is_not_distinct_from(output.match_version_id),
        revision.config_hash == output.config_hash,
        revision.marking_level.is_not_distinct_from(output.marking_level),
    ).with_only_columns(revision.generation).correlate(output).exists()
    return query.where(ancestry)


def _receipt(row: PipelineSandboxOutputRow) -> PipelineSandboxOutput:
    return PipelineSandboxOutput(
        id=row.id, requestId=row.request_id, reviewedGeneration=row.reviewed_generation, configHash=row.config_hash,
        resultHash=row.result_hash, versionId=row.input_version_id, matchVersionId=row.match_version_id,
        columns=row.columns,
        inputRows=row.input_rows, outputRows=row.output_rows, recordedAt=row.recorded_at, markings=markings(row.marking_level),
    )


def _seal(output: PipelineSandboxOutputRow, rows: list[list[str]]) -> str:
    data = {"columns": output.columns, "rows": rows}
    if output.marking_level is not None or output.match_version_id is not None:
        data.update(
            markings=markings(output.marking_level), reviewedGeneration=output.reviewed_generation,
            configHash=output.config_hash, versionId=str(output.input_version_id),
            streamMarker=str(output.stream_marker), physicalDigest=output.physical_digest,
        )
    if output.match_version_id is not None:
        data.update(operator="match_by_id", matchVersionId=str(output.match_version_id))
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


def _compare_missing() -> HTTPException:
    return HTTPException(404, "Two authorized sealed Builder outputs are required for comparison")


async def _authorize_comparison_operands(
    session: AsyncSession, request: PipelineSandboxOutputCompareRequest, actor_id: UUID,
) -> tuple[PipelineSandboxOutputRow, PipelineSandboxOutputRow]:
    limit = marking_limit(actor_id)
    if limit == 0:
        raise _compare_missing()
    binding = await _require_output_scope(session, limit)
    if binding is None:
        raise _compare_missing()
    operands = (await session.scalars(_outputs(binding, limit).where(
        PipelineSandboxOutputRow.id.in_((request.fromOutputId, request.toOutputId)),
    ))).all()
    if len(operands) != 2:
        raise _compare_missing()
    if not await verify_output_ancestry(session, binding, limit, operands):
        raise _compare_missing()
    keyed = {item.id: item for item in operands}
    return keyed[request.fromOutputId], keyed[request.toOutputId]


async def _verified_output_configs(
    session: AsyncSession, binding: CdcSandboxBindingRow, limit: int | None,
    operands: Sequence[PipelineSandboxOutputRow],
) -> dict[UUID, PipelineSandboxConfig] | None:
    """Verify exact immutable review and approved input headers, without any row reads."""
    saved = {row.generation: row for row in (await session.scalars(_revisions(binding, limit).where(
        PipelineSandboxRevisionRow.generation.in_({row.reviewed_generation for row in operands}),
    ))).all()}
    accepted = {}
    for output in operands:
        revision = saved.get(output.reviewed_generation)
        if revision is None:
            return None
        try:
            config = _revision(revision).config
        except HTTPException:
            return None
        # The current-match flag controls new execution, not this historical read.
        levels = []
        for identity in (config.versionId, config.matchVersionId):
            if identity is None:
                continue
            header = await session.scalar(_match_version_query(binding, identity, limit))
            if header is None:
                return None
            levels.append(header.marking_level)
        if (config.projection != output.columns
            or (config.matchVersionId is not None and any(level not in (BASE, RESTRICTED) for level in levels))
            or (max([level or 0 for level in levels] + [RESTRICTED if config.additionalMarking else 0]) or None)
                != output.marking_level):
            return None
        accepted[output.id] = config
    return accepted


async def verify_output_ancestry(
    session: AsyncSession, binding: CdcSandboxBindingRow, limit: int | None,
    operands: Sequence[PipelineSandboxOutputRow],
) -> bool:
    return await _verified_output_configs(session, binding, limit, operands) is not None


def _keyed_output_rows(rows: list[list[str]], id_position: int) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for row in rows:
        value = row[id_position]
        if not isinstance(value, str) or not re.fullmatch(r"(?:0|-?[1-9][0-9]{0,18})", value):
            raise HTTPException(409, "Stored Builder output has an invalid canonical bigint ID")
        key = int(value)
        if not -(2**63) <= key < 2**63 or key in result:
            raise HTTPException(409, "Stored Builder output has an invalid or duplicate bigint ID")
        result[key] = row
    return result


async def compare_pipeline_outputs(
    session: AsyncSession, request: PipelineSandboxOutputCompareRequest, actor_id: UUID,
) -> PipelineSandboxOutputComparison:
    """Read two already sealed outputs under one snapshot; never run the Builder or load CDC rows."""
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
    before, after = await _authorize_comparison_operands(session, request, actor_id)
    if before.columns != after.columns or "id" not in before.columns:
        raise HTTPException(409, "Choose outputs with the same ordered projection including ID")
    before_rows = await _stored_rows(session, before)
    after_rows = await _stored_rows(session, after)
    if (len(before_rows) + len(after_rows) > 2 * MAX_OUTPUT_ROWS
        or _byte_size(before_rows) + _byte_size(after_rows) > 2 * MAX_OUTPUT_BYTES):
        raise HTTPException(409, "The sealed outputs exceed the comparison budget")
    position = before.columns.index("id")
    left, right = _keyed_output_rows(before_rows, position), _keyed_output_rows(after_rows, position)
    counts = dict(added=0, removed=0, updated=0, unchanged=0)
    details = []
    bytes_left = 2 * MAX_OUTPUT_BYTES
    truncated = False
    for key in sorted(left.keys() | right.keys()):
        a, b = left.get(key), right.get(key)
        kind = "added" if a is None else "removed" if b is None else "updated" if a != b else "unchanged"
        counts[kind] += 1
        if kind == "unchanged":
            continue
        if not truncated and len(details) < 25:
            change = PipelineSandboxOutputChange(id=str(key), kind=kind, before=a, after=b)
            bytes_left -= len(change.model_dump_json().encode("utf-8")) + 1
            if bytes_left >= 0:
                details.append(change)
                continue
        truncated = True
    return PipelineSandboxOutputComparison(
        fromOutputId=before.id, toOutputId=after.id, projection=before.columns,
        markings=markings(max(before.marking_level or 0, after.marking_level or 0) or None),
        changes=details, truncated=truncated, **counts,
    )


def _history_missing() -> HTTPException:
    return HTTPException(404, "Authorized sealed Builder outputs are required for record history",
                         headers={"Cache-Control": "private, no-store"})


def _recipe(config: PipelineSandboxConfig) -> tuple:
    # Config has already been parsed, normalized and checked against its own
    # exact hash and marking; only compatibility ignores version IDs and mark override.
    return (config.matchVersionId is not None, tuple(config.projection),
            tuple((item.field, item.operator, item.value) for item in config.filters))


async def trace_pipeline_output_record(
    session: AsyncSession, request: PipelineSandboxRecordHistoryRequest, actor_id: UUID,
) -> PipelineSandboxRecordHistory:
    """Read selected sealed outputs only, with no partial result or Builder evaluation."""
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
    await session.execute(text("SET LOCAL lock_timeout='100ms'"))
    limit = marking_limit(actor_id)
    if limit == 0:
        raise _history_missing()
    available, binding = await _output_scope(session, limit)
    if not available or binding is None:
        raise _history_missing()
    operands = (await session.scalars(_outputs(binding, limit).where(
        PipelineSandboxOutputRow.id.in_(request.outputIds),
    ))).all()
    if len(operands) != len(request.outputIds):
        raise _history_missing()
    configs = await _verified_output_configs(session, binding, limit, operands)
    if configs is None:
        raise _history_missing()

    outputs = sorted(operands, key=lambda output: output.recorded_at)
    if len({output.recorded_at for output in outputs}) != len(outputs):
        raise HTTPException(409, "The selected outputs share a recorded time; record order is ambiguous")
    projection = outputs[0].columns
    recipe = _recipe(configs[outputs[0].id])
    if "id" not in projection or any(_recipe(configs[output.id]) != recipe for output in outputs[1:]):
        raise HTTPException(409, "Choose outputs from the same Builder recipe and ordered projection including ID")
    if (sum(output.output_rows for output in outputs) > _RECORD_HISTORY_MAX_ROWS
        or sum(output.output_bytes for output in outputs) > _RECORD_HISTORY_MAX_BYTES):
        raise HTTPException(409, "The selected outputs exceed the record history budget")

    key = int(request.recordId)
    position = projection.index("id")
    steps = []
    previous = None
    for output in outputs:
        # Persisted position constraint and primary key bound each proved seal to
        # 100 records; oversized or missing archived rows cannot produce a step.
        rows = await _stored_rows(session, output)
        current = _keyed_output_rows(rows, position).get(key)
        if not steps:
            label = "absent" if current is None else "baseline"
        elif current is None:
            label = "absent" if previous is None else "removed"
        elif previous is None:
            label = "added"
        else:
            label = "unchanged" if current == previous else "changed"
        steps.append(PipelineSandboxRecordHistoryStep(
            outputId=output.id, recordedAt=output.recorded_at, kind=label, row=current,
        ))
        previous = current
    return PipelineSandboxRecordHistory(
        recordId=request.recordId, projection=projection,
        markings=markings(max(output.marking_level or 0 for output in outputs) or None), steps=steps,
    )


def encode_record_history(history: PipelineSandboxRecordHistory) -> bytes:
    payload = history.model_dump_json().encode("utf-8")
    if len(payload) > _RECORD_HISTORY_RESPONSE_BYTES:
        raise HTTPException(409, "The selected record history exceeds the response budget")
    return payload


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
            return _receipt(prior)  # Reconcile from the sealed result without reading either input snapshot.
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
        left, right, level = await resolve_pipeline_inputs(session, binding, config, limit)
        if level != revision.marking_level:
            raise HTTPException(404, "No authorized synthetic revision exists for this request")
        right_ids = await read_match_ids(session, right)
        source = await _read_records(session, left)
        rows = project_pipeline_records(source, config, right_ids)
        size = _byte_size(rows)
        if len(rows) > MAX_OUTPUT_ROWS or size > MAX_OUTPUT_BYTES:
            raise HTTPException(422, "The projected Builder output exceeds its row or UTF-8 byte budget; nothing was committed")
        output = PipelineSandboxOutputRow(
            request_id=request.requestId, reviewed_generation=revision.generation, config_hash=revision.config_hash,
            result_hash="", marking_level=level,
            input_version_id=config.versionId, match_version_id=config.matchVersionId,
            stream_marker=binding.stream_marker,
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
