"""Atomic copy of an exact sealed output into preprovisioned private local PostgreSQL."""

from hashlib import sha256
from ipaddress import ip_address
import json
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.web.api.models.pipeline_handoff import PipelineHandoffCopyReceipt, PipelineHandoffCopyRequest, PipelineHandoffSink
from app.web.api.models.pipeline_sandbox_outputs import PipelineSandboxOutputRow
from app.web.core.config import settings
from app.web.services.pipeline_handoff import _sink_grant, _unavailable
from app.web.services.pipeline_handoff_scope import HANDOFF_PRIVATE_SCHEMA
from app.web.services.pipeline_sandbox_outputs import (
    MAX_OUTPUT_BYTES, MAX_OUTPUT_ROWS, _output_scope, _outputs, _seal, _stored_rows, verify_output_ancestry,
)
from app.web.services.synthetic_governance import BASE, BASE_TAG, RESTRICTED, marking_limit, markings

_SCHEMA = HANDOFF_PRIVATE_SCHEMA
_NAMES = ("copies", "copy_rows", "sink_state")
_CATALOG = text("""
    SELECT c.relname::text, c.oid::bigint, pg_catalog.pg_relation_filenode(c.oid)::bigint,
        ARRAY(SELECT pg_catalog.concat_ws(':', f.attname, f.atttypid, f.atttypmod, f.attcollation, f.attnotnull)
              FROM pg_catalog.pg_attribute f WHERE f.attrelid=c.oid AND f.attnum>0 AND NOT f.attisdropped
              ORDER BY f.attnum),
        ARRAY(SELECT pg_catalog.concat_ws(':', p.oid, p.convalidated, pg_catalog.pg_get_constraintdef(p.oid))
              FROM pg_catalog.pg_constraint p WHERE p.conrelid=c.oid ORDER BY p.oid),
        ARRAY(SELECT pg_catalog.concat_ws(':', i.indexrelid, pg_catalog.pg_relation_filenode(i.indexrelid),
                                         i.indisvalid, i.indisready, pg_catalog.pg_get_indexdef(i.indexrelid))
              FROM pg_catalog.pg_index i WHERE i.indrelid=c.oid ORDER BY i.indexrelid)
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_catalog.pg_am a ON a.oid=c.relam AND a.amname='heap'
    WHERE n.nspname='ontology_handoff_private' AND c.relname IN ('copies','copy_rows','sink_state')
      AND c.relkind='r' AND c.relpersistence='p' AND NOT c.relispartition AND NOT c.relrowsecurity
      AND NOT c.relforcerowsecurity AND NOT c.relhasrules
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE c.oid IN (i.inhrelid,i.inhparent))
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
      AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.pg_attribute f WHERE f.attrelid=c.oid AND f.attnum>0
          AND NOT f.attisdropped AND (f.attgenerated<>'' OR f.attidentity<>'' OR f.atthasdef)
      )
      AND NOT EXISTS (
          SELECT 1 FROM pg_catalog.pg_constraint p WHERE p.conrelid=c.oid AND p.contype='f'
          AND NOT (c.relname='copy_rows' AND p.confrelid=pg_catalog.to_regclass('ontology_handoff_private.copies'))
      )
    ORDER BY c.relname::text COLLATE "C"
""")
_COLUMNS = """id, request_id, output_id, sink_generation, actor_id, config_hash, result_hash,
              stream_marker, marking_level, rows_copied, recorded_at"""


def _granted(actor_id: UUID, grant) -> bool:
    approval = settings.ONTOLOGY_SANDBOX_HANDOFF_COPY_GRANTS.get(actor_id)
    return bool(settings.ONTOLOGY_SANDBOX_HANDOFF_COPY_ENABLED and approval is not None
                and (approval.scope, approval.generation, approval.streamMarker, approval.clearance) ==
                    (grant.scope, grant.generation, grant.streamMarker, grant.clearance)
                and approval.databaseMarker == settings.ONTOLOGY_SANDBOX_HANDOFF_COPY_DATABASE_MARKER
                and approval.physicalDigest == settings.ONTOLOGY_SANDBOX_HANDOFF_COPY_PHYSICAL_DIGEST)


async def _physical(session: AsyncSession):
    rows = (await session.execute(_CATALOG)).all()
    if tuple(row[0] for row in rows) != _NAMES or any(not row[2] for row in rows):
        return None
    return sha256(json.dumps([tuple(row) for row in rows], separators=(",", ":")).encode()).hexdigest()


async def copy_database_ready(session: AsyncSession, *, write: bool) -> bool:
    engine = session.bind
    if not isinstance(engine, AsyncEngine) or engine.url.query:
        return False
    try:
        configured = ip_address(engine.url.host or "")
    except ValueError:
        return False
    if not configured.is_loopback or "%" in str(configured):
        return False
    local = await session.scalar(text("""
        SELECT pg_catalog.inet_server_addr() = CAST(:address AS inet)
            AND pg_catalog.inet_server_port() = :port
    """), {"address": str(configured), "port": engine.url.port or 5432})
    if local is not True:
        return False
    expected = settings.ONTOLOGY_SANDBOX_HANDOFF_COPY_PHYSICAL_DIGEST
    if expected is None or await _physical(session) != expected:
        return False
    mode = "SHARE ROW EXCLUSIVE" if write else "ACCESS SHARE"
    relations = ", ".join(f"ONLY {_SCHEMA}.{name}" for name in _NAMES)
    await session.execute(text(f"LOCK TABLE {relations} IN {mode} MODE NOWAIT"))
    if await _physical(session) != expected:
        return False
    marker = await session.scalar(text(f"SELECT marker FROM ONLY {_SCHEMA}.sink_state WHERE singleton"))
    return marker is not None and marker == settings.ONTOLOGY_SANDBOX_HANDOFF_COPY_DATABASE_MARKER


async def copy_available(session: AsyncSession, actor_id: UUID, grant) -> bool:
    if not _granted(actor_id, grant):
        return False
    await session.execute(text("SET LOCAL search_path=pg_catalog,public,pg_temp"))
    return await copy_database_ready(session, write=False)


def _receipt(row, generation: UUID) -> PipelineHandoffCopyReceipt:
    return PipelineHandoffCopyReceipt(
        id=row.id, requestId=row.request_id, outputId=row.output_id,
        sink=PipelineHandoffSink(generation=generation), markings=markings(row.marking_level),
        rowsCopied=row.rows_copied, recordedAt=row.recorded_at,
    )


async def _sink_seal_matches(session: AsyncSession, receipt_id: UUID, output: PipelineSandboxOutputRow) -> bool:
    """Verify the exact authorized replica without consulting source payload storage."""
    records = (await session.execute(text(f"""
        WITH bounded AS (
            SELECT position, row_values,
                   pg_catalog.octet_length(pg_catalog.convert_to(row_values[1], 'UTF8'))
                   + coalesce(pg_catalog.octet_length(pg_catalog.convert_to(row_values[2], 'UTF8')),0) AS bytes
            FROM ONLY {_SCHEMA}.copy_rows WHERE copy_id=:id ORDER BY position LIMIT :row_cap
        ), measured AS (
            SELECT *, sum(bytes) OVER () AS total_bytes FROM bounded
        )
        SELECT position, CASE WHEN total_bytes <= :byte_cap THEN row_values END AS row_values, total_bytes
        FROM measured ORDER BY position
    """), {"id": receipt_id, "row_cap": MAX_OUTPUT_ROWS + 1, "byte_cap": MAX_OUTPUT_BYTES})).all()
    if (len(records) != output.output_rows or (records[0].total_bytes if records else 0) != output.output_bytes
        or any(record.position != i or record.row_values is None or len(record.row_values) != len(output.columns)
               for i, record in enumerate(records, 1))):
        return False
    return _seal(output, [record.row_values for record in records]) == output.result_hash


async def copy_handoff(
    session: AsyncSession, actor_id: UUID, request: PipelineHandoffCopyRequest,
) -> PipelineHandoffCopyReceipt:
    grant = _sink_grant(actor_id)
    limit = marking_limit(actor_id)
    if (grant is None or limit not in (BASE, RESTRICTED) or not _granted(actor_id, grant)
        or request.expectedSinkGeneration != grant.generation):
        raise _unavailable()
    await session.execute(text("SET LOCAL statement_timeout='1000ms'"))
    await session.execute(text("SET LOCAL lock_timeout='100ms'"))
    await session.execute(text("SET LOCAL search_path=pg_catalog,public,pg_temp"))
    if not await copy_database_ready(session, write=True):
        raise _unavailable()
    # This scope cannot change the archived Builder review while a copy settles.
    if not await session.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock_shared(1649157671,275192983)")):
        raise HTTPException(503, "Synthetic handoff could not settle; retry the same request ID")
    enabled, binding = await _output_scope(session, limit)
    if not enabled or binding is None or binding.stream_marker != grant.streamMarker:
        raise _unavailable()
    output = await session.scalar(_outputs(binding, limit).where(PipelineSandboxOutputRow.id == request.outputId))
    accepted = BASE if grant.clearance == BASE_TAG else RESTRICTED
    if (output is None or output.marking_level not in (BASE, RESTRICTED) or output.marking_level > accepted
        or not await verify_output_ancestry(session, binding, limit, [output])):
        raise _unavailable()
    if (output.config_hash, output.result_hash) != (request.expectedConfigHash, request.expectedResultHash):
        raise HTTPException(409, "The selected sealed output does not match its expected identity")
    prior = (await session.execute(text(f"""
        SELECT {_COLUMNS} FROM ONLY {_SCHEMA}.copies WHERE request_id=:request_id
    """), {"request_id": request.requestId})).one_or_none()
    if prior is not None:
        if (prior.output_id, prior.actor_id, prior.sink_generation, prior.stream_marker, prior.marking_level,
            prior.config_hash, prior.result_hash, prior.rows_copied) != (
            output.id, actor_id, grant.generation, binding.stream_marker, output.marking_level,
            output.config_hash, output.result_hash, output.output_rows,
        ):
            raise HTTPException(409, "This request ID belongs to a different authorized handoff")
        if not await _sink_seal_matches(session, prior.id, output):
            raise HTTPException(409, "The private handoff copy no longer matches its sealed receipt")
        return _receipt(prior, grant.generation)
    if await session.scalar(text(f"""
        SELECT id FROM ONLY {_SCHEMA}.copies WHERE output_id=:id AND sink_generation=:generation
    """), {"id": output.id, "generation": grant.generation}):
        raise HTTPException(409, "This sealed output already has a handoff receipt for the selected sink generation")

    rows = await _stored_rows(session, output)
    params = dict(id=uuid4(), request_id=request.requestId, output_id=output.id, sink_generation=grant.generation,
                  actor_id=actor_id, config_hash=output.config_hash, result_hash=output.result_hash,
                  stream_marker=binding.stream_marker, marking_level=output.marking_level, rows_copied=len(rows))
    receipt = (await session.execute(text(f"""
        INSERT INTO {_SCHEMA}.copies ({_COLUMNS})
        VALUES (:id,:request_id,:output_id,:sink_generation,:actor_id,:config_hash,:result_hash,
                :stream_marker,:marking_level,:rows_copied,pg_catalog.clock_timestamp())
        RETURNING {_COLUMNS}
    """), params)).one()
    if rows:
        await session.execute(text(f"""
            INSERT INTO {_SCHEMA}.copy_rows (copy_id,position,row_values)
            VALUES (:copy_id,:position,CAST(:row_values AS text[]))
        """), [dict(copy_id=receipt.id, position=i, row_values=row) for i, row in enumerate(rows, 1)])
    result = _receipt(receipt, grant.generation)
    await session.commit()
    return result
