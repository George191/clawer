"""Independent recipient proof of a recorded receipt, not an integrity read of its replica."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.pipeline_handoff import (
    PipelineHandoffRecipientCapability, PipelineHandoffRecipientProof, PipelineHandoffRecipientRequest, PipelineHandoffSink,
)
from app.web.core.config import settings
from app.web.core.config.sandbox_handoff import HandoffCopyGrant
from app.web.services.pipeline_handoff_copy import copy_database_ready
from app.web.services.pipeline_handoff_scope import HANDOFF_PRIVATE_SCHEMA
from app.web.services.synthetic_governance import BASE, BASE_TAG, RESTRICTED, RESTRICTED_TAG, markings


_RECEIPT = text(f"""
    SELECT id, sink_generation, marking_level, rows_copied, recorded_at
    FROM ONLY {HANDOFF_PRIVATE_SCHEMA}.copies
    WHERE id=:identity AND sink_generation=:generation AND stream_marker=:stream
      AND marking_level IN (1,2) AND marking_level <= :clearance
      AND rows_copied BETWEEN 0 AND 100
      AND pg_catalog.isfinite(recorded_at) AND recorded_at <= pg_catalog.statement_timestamp()
""")


def recipient_proof_unavailable() -> HTTPException:
    return HTTPException(404, "No authorized recorded recipient proof is available",
                         headers={"Cache-Control": "private, no-store"})


def _grant(actor: UUID, expected: UUID | None = None) -> HandoffCopyGrant | None:
    s = settings
    if (not s.ONTOLOGY_SANDBOX_HANDOFF_PROOF_ENABLED or not s.ONTOLOGY_SANDBOX_HANDOFF_ENABLED
        or "ENVIRONMENT" not in s.model_fields_set or s.ENVIRONMENT != "local"):
        return None
    grant = s.ONTOLOGY_SANDBOX_HANDOFF_PROOF_GRANTS.get(actor)
    if (grant is None or grant.scope != "synthetic-review-sink"
        or not grant.generation.int or grant.generation != s.ONTOLOGY_SANDBOX_HANDOFF_SINK_GENERATION
        or (expected is not None and grant.generation != expected)
        or not grant.streamMarker.int or grant.streamMarker != s.ONTOLOGY_SANDBOX_HANDOFF_PROOF_STREAM_MARKER
        or not grant.databaseMarker.int or grant.databaseMarker != s.ONTOLOGY_SANDBOX_HANDOFF_COPY_DATABASE_MARKER
        or grant.physicalDigest != s.ONTOLOGY_SANDBOX_HANDOFF_COPY_PHYSICAL_DIGEST):
        return None
    clearance = s.ONTOLOGY_SANDBOX_HANDOFF_SINK_CLEARANCE
    if clearance not in (BASE_TAG, RESTRICTED_TAG) or (clearance == BASE_TAG and grant.clearance != BASE_TAG):
        return None
    return grant


async def _physical_ready(session: AsyncSession) -> bool:
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL search_path=pg_catalog,public,pg_temp"))
    await session.execute(text("SET LOCAL lock_timeout='100ms'"))
    await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
    return await copy_database_ready(session, write=False)


async def recipient_proof_capability(session: AsyncSession, actor: UUID) -> PipelineHandoffRecipientCapability:
    grant = _grant(actor)
    if grant is None or not await _physical_ready(session):
        return PipelineHandoffRecipientCapability(available=False)
    return PipelineHandoffRecipientCapability(available=True, sink=PipelineHandoffSink(generation=grant.generation))


async def read_recipient_proof(
    session: AsyncSession, actor: UUID, request: PipelineHandoffRecipientRequest,
) -> PipelineHandoffRecipientProof:
    grant = _grant(actor, request.expectedSinkGeneration)
    if grant is None or not await _physical_ready(session):
        raise recipient_proof_unavailable()
    row = (await session.execute(_RECEIPT, {
        "identity": request.receiptId, "generation": grant.generation, "stream": grant.streamMarker,
        "clearance": BASE if grant.clearance == BASE_TAG else RESTRICTED,
    })).one_or_none()
    if row is None:
        raise recipient_proof_unavailable()
    return PipelineHandoffRecipientProof(
        id=row.id, sink=PipelineHandoffSink(generation=row.sink_generation),
        markings=markings(row.marking_level), rowsRecorded=row.rows_copied, recordedAt=row.recorded_at,
    )
