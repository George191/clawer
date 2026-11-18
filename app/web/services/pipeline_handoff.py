"""Metadata-only qualification: no transfer, output/input data read, or receiver connection."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.pipeline_handoff import (
    PipelineHandoffCapability, PipelineHandoffEligibility, PipelineHandoffRequest, PipelineHandoffSink,
)
from app.web.api.models.pipeline_sandbox_outputs import PipelineSandboxOutputRow
from app.web.core.config import settings
from app.web.services.pipeline_sandbox_outputs import _output_scope, _outputs, verify_output_ancestry
from app.web.services.synthetic_governance import BASE, BASE_TAG, RESTRICTED, marking_limit, markings


def _unavailable() -> HTTPException:
    return HTTPException(404, "No authorized synthetic handoff eligibility is available")


def _sink_grant(actor_id: UUID):
    if (not settings.ONTOLOGY_SANDBOX_HANDOFF_ENABLED or settings.ENVIRONMENT != "local"
        or "ENVIRONMENT" not in settings.model_fields_set):
        return None
    grant = settings.ONTOLOGY_SANDBOX_HANDOFF_SINK_GRANTS.get(actor_id)
    if (grant is None or grant.scope != "synthetic-review-sink"
        or grant.generation != settings.ONTOLOGY_SANDBOX_HANDOFF_SINK_GENERATION
        or grant.clearance != settings.ONTOLOGY_SANDBOX_HANDOFF_SINK_CLEARANCE):
        return None
    return grant


async def _visible_sink(session: AsyncSession, actor_id: UUID, expected_generation: UUID | None = None):
    limit = marking_limit(actor_id)
    grant = _sink_grant(actor_id)
    if (limit not in (BASE, RESTRICTED) or grant is None
        or (expected_generation is not None and grant.generation != expected_generation)):
        return None
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
    enabled, binding = await _output_scope(session, limit)
    if not enabled or binding is None or binding.stream_marker != grant.streamMarker:
        return None
    return grant, binding, limit


async def handoff_capability(session: AsyncSession, actor_id: UUID) -> PipelineHandoffCapability:
    scope = await _visible_sink(session, actor_id)
    if scope is None:
        return PipelineHandoffCapability(available=False)
    grant, _, _ = scope
    from app.web.services.pipeline_handoff_copy import copy_available

    available = False
    if settings.ONTOLOGY_SANDBOX_HANDOFF_COPY_ENABLED:
        try:
            async with session.begin_nested():
                available = await copy_available(session, actor_id, grant)
        except DBAPIError:
            pass
    sender_available = False
    if settings.ONTOLOGY_SANDBOX_HANDOFF_SENDER_PROOF_ENABLED:
        from app.web.services.pipeline_handoff_sender import sender_proof_available

        try:
            async with session.begin_nested():
                sender_available = await sender_proof_available(session, actor_id, grant.generation)
        except DBAPIError:
            pass
    return PipelineHandoffCapability(available=True, sink=PipelineHandoffSink(generation=grant.generation),
                                    copyAvailable=True if available else None,
                                    senderProofAvailable=True if sender_available else None)


async def handoff_eligibility(
    session: AsyncSession, actor_id: UUID, request: PipelineHandoffRequest,
) -> PipelineHandoffEligibility:
    scope = await _visible_sink(session, actor_id, request.expectedSinkGeneration)
    if scope is None:
        raise _unavailable()
    grant, binding, limit = scope
    output = await session.scalar(_outputs(binding, limit).where(PipelineSandboxOutputRow.id == request.outputId))
    if (output is None or output.marking_level not in (BASE, RESTRICTED)
        or not await verify_output_ancestry(session, binding, limit, [output])):
        raise _unavailable()
    # Compare durable sealed identity; this deliberately does not rehash stored
    # data or refresh the output/its source. There is no execution authority.
    if (output.config_hash, output.result_hash) != (request.expectedConfigHash, request.expectedResultHash):
        raise HTTPException(409, "The selected sealed output does not match its expected identity")
    accepted = BASE if grant.clearance == BASE_TAG else RESTRICTED
    eligible = output.marking_level <= accepted
    return PipelineHandoffEligibility(
        outputId=output.id, sink=PipelineHandoffSink(generation=grant.generation),
        eligible=eligible, reason="eligible_for_review" if eligible else "clearance_mismatch",
        markings=markings(output.marking_level),
    )
