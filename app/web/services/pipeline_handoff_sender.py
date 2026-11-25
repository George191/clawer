"""Sender-only proof of a recorded synthetic copy, not replica integrity."""

from ipaddress import ip_address
from uuid import UUID

from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.web.api.models.pipeline_handoff import PipelineHandoffCopyReceipt, PipelineHandoffCopyRequest
from app.web.api.models.pipeline_sandbox_outputs import PipelineSandboxOutputRow
from app.web.core.config import settings
from app.web.core.config.sandbox_handoff import HandoffCopyGrant
from app.web.services.cdc_sandbox_versions import _versions_marker
from app.web.services.pipeline_handoff import _sink_grant
from app.web.services.pipeline_handoff_copy import _NAMES, _SCHEMA, _copy_approval, _physical, _receipt
from app.web.services.pipeline_sandbox_outputs import MAX_OUTPUT_ROWS, _output_scope, _outputs, verify_output_ancestry
from app.web.services.synthetic_governance import BASE, BASE_TAG, RESTRICTED, marking_limit

_LOCK = text(f"LOCK TABLE {', '.join(f'ONLY {_SCHEMA}.{name}' for name in _NAMES)} IN ACCESS SHARE MODE NOWAIT")
_NATIVE = text(f"""
    SELECT pg_catalog.inet_server_addr() = CAST(:address AS inet)
       AND pg_catalog.inet_server_port() = :port
       AND pg_catalog.current_database() = :database
       AND session_user::text = :username AND current_user::text = :username
       AND pg_catalog.has_schema_privilege(pg_catalog.to_regnamespace('{_SCHEMA}'), 'USAGE')
""")
_HELD = text("""
    SELECT count(*) = 3
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='ontology_handoff_private' AND c.relname IN ('copies','copy_rows','sink_state')
      AND EXISTS (
          SELECT 1 FROM pg_catalog.pg_locks l
          WHERE l.pid=pg_catalog.pg_backend_pid() AND l.locktype='relation'
            AND l.relation=c.oid AND l.mode='AccessShareLock' AND l.granted
            AND l.database=(SELECT d.oid FROM pg_catalog.pg_database d
                            WHERE d.datname=pg_catalog.current_database())
      )
""")
_MARKER = text(f"SELECT marker FROM ONLY {_SCHEMA}.sink_state WHERE singleton IS TRUE LIMIT 2")
_RECEIPT = text(f"""
    SELECT id, request_id, output_id, marking_level, rows_copied, recorded_at
    FROM ONLY {_SCHEMA}.copies
    WHERE request_id=:request_id AND actor_id=:actor AND output_id=:output_id
      AND config_hash=:config_hash AND result_hash=:result_hash AND sink_generation=:generation
      AND stream_marker=:stream AND marking_level=:marking AND rows_copied=:rows
      AND pg_catalog.isfinite(recorded_at) AND recorded_at >= :output_recorded_at
      AND recorded_at <= pg_catalog.statement_timestamp()
    LIMIT 2
""")


class SenderProofUnavailable(Exception):
    """No authorized recorded receipt can be confirmed."""


def _approval(actor: UUID, expected: UUID | None = None) -> tuple[HandoffCopyGrant, int] | None:
    grant = _sink_grant(actor)
    level = marking_limit(actor)
    if (not settings.ONTOLOGY_SANDBOX_HANDOFF_SENDER_PROOF_ENABLED
            or not settings.ONTOLOGY_SANDBOX_PIPELINE_ENABLED or not settings.ONTOLOGY_SANDBOX_PIPELINE_OUTPUT_ENABLED
            or grant is None or level not in (BASE, RESTRICTED) or not grant.generation.int
            or not grant.streamMarker.int or grant.streamMarker != _versions_marker()
            or (expected is not None and grant.generation != expected)):
        return None
    approval = _copy_approval(actor, grant)
    return (approval, level) if approval is not None and approval.databaseMarker.int else None


async def _native(session: AsyncSession) -> bool:
    engine = session.bind
    if (not isinstance(engine, AsyncEngine) or engine.url.drivername != "postgresql+asyncpg"
            or engine.url.query or not engine.url.database or not engine.url.username):
        return False
    try:
        address = ip_address(engine.url.host or "")
    except ValueError:
        return False
    if not address.is_loopback or "%" in str(address):
        return False
    return await session.scalar(_NATIVE, {
        "address": str(address), "port": engine.url.port or 5432, "database": engine.url.database,
        "username": engine.url.username,
    }) is True


async def _limits(session: AsyncSession) -> None:
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    await session.execute(text("SET LOCAL search_path=pg_catalog,app,pg_temp"))
    await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
    await session.execute(text("SET LOCAL lock_timeout='100ms'"))


async def _physical_scope(
    session: AsyncSession, approval: HandoffCopyGrant, *, require_locks: bool = False,
) -> bool:
    if (not await _native(session) or await _physical(session) != approval.physicalDigest
            or (require_locks and not await session.scalar(_HELD))):
        return False
    marker = (await session.execute(_MARKER)).scalar_one_or_none()
    return marker == approval.databaseMarker


async def sender_proof_available(session: AsyncSession, actor: UUID, generation: UUID) -> bool:
    """Capability hint only; no receipt lookup or authorization for a new copy."""
    scope = _approval(actor, generation)
    if scope is None:
        return False
    await session.execute(text("SET LOCAL search_path=pg_catalog,app,pg_temp"))
    return await _physical_scope(session, scope[0])


async def read_sender_proof(
    session: AsyncSession, actor: UUID, request: PipelineHandoffCopyRequest,
) -> PipelineHandoffCopyReceipt:
    scope = _approval(actor, request.expectedSinkGeneration)
    if scope is None:
        raise SenderProofUnavailable
    approval, level = scope
    async with session.begin():
        await _limits(session)
        if not await _physical_scope(session, approval):
            raise SenderProofUnavailable
    # Acquire the fixed locks before taking the final catalog/metadata snapshot.
    async with session.begin():
        await _limits(session)
        await session.execute(_LOCK)
        if (_approval(actor, request.expectedSinkGeneration) != scope
                or not await _physical_scope(session, approval, require_locks=True)):
            raise SenderProofUnavailable
        enabled, binding = await _output_scope(session, level)
        if not enabled or binding is None or binding.stream_marker != approval.streamMarker:
            raise SenderProofUnavailable
        output = await session.scalar(_outputs(binding, level).where(
            PipelineSandboxOutputRow.id == request.outputId,
            func.pg_catalog.isfinite(PipelineSandboxOutputRow.recorded_at),
        ))
        clearance = BASE if approval.clearance == BASE_TAG else RESTRICTED
        if (output is None or output.marking_level not in (BASE, RESTRICTED) or output.marking_level > clearance
                or not 0 <= output.output_rows <= MAX_OUTPUT_ROWS
                or (output.config_hash, output.result_hash) != (request.expectedConfigHash, request.expectedResultHash)
                or not await verify_output_ancestry(session, binding, level, [output])):
            raise SenderProofUnavailable
        if _approval(actor, request.expectedSinkGeneration) != scope:
            raise SenderProofUnavailable
        rows = (await session.execute(_RECEIPT, {
            "request_id": request.requestId, "actor": actor, "output_id": output.id, "config_hash": output.config_hash,
            "result_hash": output.result_hash, "generation": approval.generation, "stream": binding.stream_marker,
            "marking": output.marking_level, "rows": output.output_rows, "output_recorded_at": output.recorded_at,
        })).all()
        if len(rows) != 1 or _approval(actor, request.expectedSinkGeneration) != scope:
            raise SenderProofUnavailable
        return _receipt(rows[0], approval.generation)
