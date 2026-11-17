"""Local sender ledger for fixed, independently reviewed synthetic receivers."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from ipaddress import ip_address
import json
import logging
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlmodel import select

from app.web.api.models.table_exports import (
    ExportDestination, SYNTHETIC_EXPORT_MARKING, TableExportAttempt, TableExportAttemptRow, TableExportCapabilities,
    TableExportDestinationOption, TableExportGrant,
    TableExportGrantChange, TableExportGrantEvent, TableExportGrantRow, TableExportLineage,
    TableExportLineageDeclaration, TableExportLineageDestination, TableExportLineageEndpoint,
    TableExportLineageReceipt, TableExportMapping, TableExportPlan,
    TableExportPlanRow, TableExportReceipt, TableExportReceiptRow, TableExportRequestRow, TableExportReview,
    TableExportRunRequest,
)
from app.web.core.config import settings
from app.web.services.batch_syncs import _RunFailure, _rollback
from app.web.services.table_export_receiver import (
    ReceiverConfig, ReceiverFailure, ReceiverReceipt, apply_export_rows, export_payload_digest, find_export_receipt,
    preflight_export_receiver,
)
from app.web.services.table_export_source import (
    MAX_CAPTION_BYTES, MAX_EXPORT_ROWS, SOURCE_LABEL, read_table_export_source, require_export_sender_marker,
)

_TARGET_SCHEMA = "ts_table_export_receiver"
_TARGET_TABLE = "records"
_HISTORY_LIMIT = 25
_PROJECTION = (("id", "id", "bigint"), ("caption", "caption", "text"))
logger = logging.getLogger(__name__)


_DESTINATION_LABELS: dict[ExportDestination, str] = {
    "primary": "Approved local receiver (redacted)",
    "secondary": "Approved secondary local receiver (redacted)",
}


def _provisioned(destination: ExportDestination = "primary") -> tuple[UUID, ReceiverConfig, str] | None:
    source_marker = settings.TABLE_EXPORT_SOURCE_MARKER
    prefix = "TABLE_EXPORT_RECEIVER_"
    revision = None
    primary = None
    if destination == "secondary":
        if not settings.TABLE_EXPORT_SECONDARY_ENABLED or not settings.TABLE_EXPORT_SANDBOX_ENABLED:
            return None
        primary = _provisioned()
        revision = settings.TABLE_EXPORT_SECONDARY_CONFIG_REVISION
        if primary is None or revision is None:
            return None
        prefix = "TABLE_EXPORT_SECONDARY_RECEIVER_"
    password = getattr(settings, prefix + "PASSWORD")
    marker = getattr(settings, prefix + "MARKER")
    address = getattr(settings, prefix + "HOST")
    port = getattr(settings, prefix + "PORT")
    database = getattr(settings, prefix + "DATABASE")
    username = getattr(settings, prefix + "USERNAME")
    try:
        host = ip_address(address)
    except ValueError:
        return None
    if (not host.is_loopback or "%" in address or not 1 <= port <= 65535 or not source_marker or not marker
        or not database or not username or password is None or not password.get_secret_value()):
        return None
    if primary is not None and (port == primary[1].port or marker in (source_marker, primary[1].marker)):
        return None
    config = ReceiverConfig(host=str(host), port=port, database=database, username=username,
                            password=password.get_secret_value(), marker=marker, tls=getattr(settings, prefix + "TLS"),
                            exact_physical=destination == "secondary")
    binding_parts = [
        "table-export-v1", str(source_marker), SOURCE_LABEL, config.host, config.port,
        config.database, config.username, str(config.marker), config.tls, _TARGET_SCHEMA, _TARGET_TABLE,
    ]
    if revision is not None:
        binding_parts.extend(["secondary", str(revision)])
    binding = sha256(json.dumps(binding_parts, separators=(",", ":")).encode()).hexdigest()
    return source_marker, config, binding


def _local() -> bool:
    return "ENVIRONMENT" in settings.model_fields_set and settings.ENVIRONMENT == "local"


def _enabled() -> bool:
    return _local() and settings.TABLE_EXPORT_SANDBOX_ENABLED


def table_export_capabilities(destination: ExportDestination = "primary") -> TableExportCapabilities:
    provisioned = _local() and _provisioned(destination) is not None
    options = None
    if destination == "primary" and provisioned and _provisioned("secondary") is not None:
        options = [TableExportDestinationOption(id=key, label=label) for key, label in _DESTINATION_LABELS.items()]
    source_schema, source_table = SOURCE_LABEL.split(".")
    return TableExportCapabilities(
        enabled=_enabled() and provisioned, provisioned=provisioned,
        sourceSchema=source_schema, sourceTable=source_table, targetSchema=_TARGET_SCHEMA, targetTable=_TARGET_TABLE,
        maxRows=MAX_EXPORT_ROWS, maxCaptionBytes=MAX_CAPTION_BYTES,
        approvedDestinations=options, destinationLabel=_DESTINATION_LABELS[destination] if destination == "secondary" else None,
        mapping=[TableExportMapping(sourceColumn=source, targetColumn=target, type=kind)
                 for source, target, kind in _PROJECTION],
    )


def _plan(row: TableExportPlanRow) -> TableExportPlan:
    return TableExportPlan(id=row.id, name=row.name, updatedAt=row.updated_at)


def _receipt(row: TableExportReceiptRow, generation: int) -> TableExportReceipt:
    return TableExportReceipt(
        id=row.id, requestId=row.request_id, rowsAffected=row.rows_affected, recordedAt=row.recorded_at,
        grantGeneration=generation,
    )


async def _limits(session: AsyncSession) -> None:
    await session.execute(text("SET LOCAL search_path = pg_catalog, public, pg_temp"))
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    await session.execute(text("SET LOCAL idle_in_transaction_session_timeout = '25s'"))


async def _lock_plan(session: AsyncSession, destination: ExportDestination, *, read: bool = False) -> TableExportPlanRow | None:
    return (await session.scalars(select(TableExportPlanRow).where(TableExportPlanRow.destination == destination)
                                 .with_for_update(read=read).execution_options(populate_existing=True))).one_or_none()


async def _last_grant(session: AsyncSession, destination: ExportDestination) -> TableExportGrantRow | None:
    # Grant actions are append-only and serialized by the same plan lock as sender execution.
    return (await session.scalars(select(TableExportGrantRow).where(TableExportGrantRow.destination == destination)
                                 .order_by(TableExportGrantRow.generation.desc())
                                 .limit(1).execution_options(populate_existing=True))).one_or_none()


def _scope_digest(binding: str, receiver_proof: str | None = None) -> str:
    scope = ["export-grant-v1", SYNTHETIC_EXPORT_MARKING, binding, _PROJECTION, MAX_EXPORT_ROWS, MAX_CAPTION_BYTES]
    if receiver_proof is not None:
        scope.append(receiver_proof)
    return sha256(json.dumps(scope, separators=(",", ":")).encode()).hexdigest()


def _grant_view(
    plan: TableExportPlanRow | None, grant: TableExportGrantRow | None, live_binding: str | None,
) -> TableExportGrant:
    available = bool(plan and live_binding and plan.binding_digest == live_binding)
    effective = bool(available and grant and grant.action == "approved"
                     and grant.marking == SYNTHETIC_EXPORT_MARKING and grant.plan_updated_at == plan.updated_at
                     and grant.scope_digest == _scope_digest(live_binding, plan.receiver_proof))
    status = ("not_granted" if grant is None else "revoked" if grant.action == "revoked"
              else "active" if effective else "stale")
    return TableExportGrant(
        available=available, effective=effective, generation=grant.generation if grant else None, status=status,
        planUpdatedAt=grant.plan_updated_at if grant else None,
        approvedAt=grant.recorded_at if grant and grant.action == "approved" else None,
        revokedAt=grant.recorded_at if grant and grant.action == "revoked" else None,
    )


def _live_binding(destination: ExportDestination) -> str | None:
    provision = _provisioned(destination) if _local() else None
    return provision[2] if provision else None


async def _read_grant(session: AsyncSession, destination: ExportDestination) -> tuple[TableExportPlanRow | None, TableExportGrant]:
    await _limits(session)
    plan = await _lock_plan(session, destination, read=True)
    return plan, _grant_view(plan, await _last_grant(session, destination), _live_binding(destination))


async def get_table_export_grant(session: AsyncSession, destination: ExportDestination = "primary") -> TableExportGrant:
    """Return policy status without exposing scope fingerprints or approval reasons."""
    return (await _read_grant(session, destination))[1]


async def change_table_export_grant(
    session: AsyncSession, payload: TableExportGrantChange, actor_id: UUID, destination: ExportDestination = "primary",
) -> TableExportGrant:
    """Append an explicit administrator decision without contacting the source or target."""
    await _limits(session)
    try:
        plan = await _lock_plan(session, destination)
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) in {"55P03", "57014"}:
            raise _RunFailure(409, "target_busy", "A Table Export is settling; retry changing its grant") from None
        raise
    previous = await _last_grant(session, destination)
    live_binding = _live_binding(destination)
    current = _grant_view(plan, previous, live_binding)
    if plan is None or plan.updated_at != payload.expectedPlanUpdatedAt:
        raise _RunFailure(409, "plan_changed", "The Table Export review changed; reload it before changing the grant")
    if current.generation != payload.expectedGeneration:
        raise _RunFailure(409, "grant_changed", "The Table Export grant changed; reload it")
    if payload.approved and not current.available:
        raise _RunFailure(403, "grant_unavailable", "Review the current provisioned synthetic export before approval")
    if (payload.approved and current.effective) or (not payload.approved and (
        previous is None or previous.action != "approved"
    )):
        raise _RunFailure(409, "grant_unchanged", "The requested Table Export grant state is already recorded")
    if destination == "secondary":
        await _require_local_secondary_sender(session, settings.TABLE_EXPORT_SECONDARY_RECEIVER_PORT,
                                              settings.TABLE_EXPORT_SOURCE_MARKER)
    row = TableExportGrantRow(
        destination=destination, generation=(current.generation or 0) + 1, action="approved" if payload.approved else "revoked",
        marking=payload.marking, reason=payload.reason, actor_id=actor_id,
        scope_digest=_scope_digest(live_binding, plan.receiver_proof) if payload.approved else previous.scope_digest,
        plan_updated_at=plan.updated_at if payload.approved else previous.plan_updated_at,
        recorded_at=datetime.now(timezone.utc),
    )
    session.add(row)
    result = _grant_view(plan, row, live_binding)
    await session.commit()
    return result


async def list_table_export_grant_history(session: AsyncSession, destination: ExportDestination = "primary") -> list[TableExportGrantEvent]:
    """Read bounded local policy decisions, not payload, connection or receipt details."""
    rows = await session.scalars(select(TableExportGrantRow).where(TableExportGrantRow.destination == destination)
                                 .order_by(TableExportGrantRow.generation.desc())
                                .limit(_HISTORY_LIMIT))
    return [TableExportGrantEvent(
        id=row.id, action=row.action, generation=row.generation, marking=row.marking,
        recordedAt=row.recorded_at, planUpdatedAt=row.plan_updated_at, reason=row.reason,
    ) for row in rows]


def _require_grant(
    plan: TableExportPlanRow | None, grant_row: TableExportGrantRow | None, req: TableExportRunRequest, binding: str,
) -> int:
    grant = _grant_view(plan, grant_row, binding)
    if not grant.effective:
        raise _RunFailure(403, "grant_required", "An effective synthetic Table Export grant is required")
    if grant.generation != req.expectedGrantGeneration:
        raise _RunFailure(409, "grant_changed", "The Table Export grant changed; review a new request")
    if plan.updated_at != req.expectedUpdatedAt:
        raise _RunFailure(409, "plan_changed", "The Table Export review changed; review a new request")
    return req.expectedGrantGeneration


async def get_table_export(session: AsyncSession, destination: ExportDestination = "primary") -> TableExportPlan | None:
    row = await session.get(TableExportPlanRow, destination)
    return _plan(row) if row is not None else None


async def _require_local_secondary_sender(session: AsyncSession, port: int, source_marker: UUID | None) -> None:
    """Never use the selected connector from a remotely hosted sender or loop back into its DB port."""
    engine = session.bind
    try:
        host = ip_address(engine.url.host) if isinstance(engine, AsyncEngine) else None
    except ValueError:
        host = None
    if not _local() or host is None or not host.is_loopback or "%" in str(host) or engine.url.query:
        raise _RunFailure(403, "export_unprovisioned", "The secondary requires a distinct loopback sender database")
    local = await session.scalar(text("""
        SELECT (pg_catalog.inet_server_addr() << inet '127.0.0.0/8'
             OR pg_catalog.inet_server_addr() = inet '::1') AND pg_catalog.inet_server_port() <> :port
    """), {"port": port})
    if local is not True:
        raise _RunFailure(403, "export_unprovisioned", "The secondary requires a distinct loopback sender database")
    await require_export_sender_marker(session, source_marker)


async def review_table_export(
    session: AsyncSession, payload: TableExportReview, actor_id: UUID, destination: ExportDestination = "primary",
) -> TableExportPlan:
    provision = _provisioned(destination) if _local() else None
    if provision is None:
        raise _RunFailure(403, "export_unprovisioned", "Table Export review requires provisioned local sender and receiver settings")
    await _limits(session)
    row = await _lock_plan(session, destination)
    if payload.expectedUpdatedAt != (row.updated_at if row else None):
        raise _RunFailure(409, "plan_changed", "The Table Export review changed; reload it before saving")

    receiver_proof = None
    if destination == "secondary":
        await _require_local_secondary_sender(session, provision[1].port, provision[0])
        try:
            receiver_proof = await preflight_export_receiver(provision[1])
        except ReceiverFailure as error:
            raise _failure(error, receiver_may_have_committed=False) from None

    now = datetime.now(timezone.utc)
    if row is None:
        row = TableExportPlanRow(destination=destination, name=payload.name, reviewed_by=actor_id,
                                 binding_digest=provision[2], receiver_proof=receiver_proof, updated_at=now)
        created = (await session.execute(insert(TableExportPlanRow).values(row.model_dump()).on_conflict_do_nothing()
                                         .returning(TableExportPlanRow.destination))).scalar_one_or_none()
        if created is None:
            raise _RunFailure(409, "plan_changed", "The Table Export review changed; reload it before saving")
    else:
        row.name = payload.name
        row.reviewed_by = actor_id
        row.binding_digest = provision[2]
        row.receiver_proof = receiver_proof
        row.updated_at = max(now, row.updated_at + timedelta(microseconds=1))
    result = _plan(row)
    await session.commit()
    return result


async def list_table_export_receipts(session: AsyncSession, destination: ExportDestination = "primary") -> list[TableExportReceipt]:
    _, grant = await _read_grant(session, destination)
    if not grant.effective:
        return []
    rows = await session.scalars(select(TableExportReceiptRow)
                                .join(TableExportRequestRow, TableExportRequestRow.request_id == TableExportReceiptRow.request_id)
                                .where(TableExportRequestRow.destination == destination,
                                       TableExportRequestRow.grant_generation == grant.generation)
                                .order_by(TableExportReceiptRow.recorded_at.desc(), TableExportReceiptRow.id.desc())
                                .limit(_HISTORY_LIMIT))
    return [_receipt(row, grant.generation) for row in rows]


async def list_table_export_attempts(session: AsyncSession, destination: ExportDestination = "primary") -> list[TableExportAttempt]:
    _, grant = await _read_grant(session, destination)
    if not grant.effective:
        return []
    statement = (select(TableExportAttemptRow, TableExportRequestRow.plan_updated_at)
                 .join(TableExportRequestRow, TableExportRequestRow.request_id == TableExportAttemptRow.request_id)
                 .where(TableExportRequestRow.destination == destination,
                                       TableExportRequestRow.grant_generation == grant.generation)
                 .order_by(TableExportAttemptRow.started_at.desc(), TableExportAttemptRow.id.desc()).limit(_HISTORY_LIMIT))
    return [TableExportAttempt(id=row.id, requestId=row.request_id, planUpdatedAt=version, grantGeneration=grant.generation, status=row.status,
                               startedAt=row.started_at, finishedAt=row.finished_at, receiptId=row.receipt_id,
                               code=row.code, message=row.message)
            for row, version in (await session.execute(statement)).all()]


def _lineage_receipt(
    receipt: TableExportReceiptRow, reviewed_at: datetime, binding: str, plan: TableExportPlanRow | None,
) -> TableExportLineageReceipt:
    # Never look up the latest plan name for old evidence. The immutable
    # request's saved revision and binding are its only attribution.
    relation = "different_binding"
    if plan is not None and plan.binding_digest == binding:
        relation = "current_review" if plan.updated_at == reviewed_at else "earlier_review"
    return TableExportLineageReceipt(
        id=receipt.id, recordedAt=receipt.recorded_at, rowsAffected=receipt.rows_affected,
        reviewedAt=reviewed_at, reviewRelation=relation, observed=receipt.rows_affected > 0,
    )


async def get_table_export_lineage(session: AsyncSession, destination: ExportDestination = "primary") -> TableExportLineage:
    """Read verified sender mirrors only; do not probe source or receiver."""
    plan, grant = await _read_grant(session, destination)
    binding_matches = grant.available
    declaration = None if plan is None else TableExportLineageDeclaration(
        id=plan.id, name=plan.name, reviewedAt=plan.updated_at,
        state=("stale" if not binding_matches else "blocked" if not grant.effective
               else "enabled" if _enabled() else "inactive"),
    )
    statement = (
        select(TableExportReceiptRow, TableExportRequestRow.plan_updated_at, TableExportRequestRow.binding_digest)
        .join(TableExportRequestRow, TableExportRequestRow.request_id == TableExportReceiptRow.request_id)
        .where(TableExportRequestRow.destination == destination,
               TableExportRequestRow.payload_digest.op("~")("^[0-9a-f]{64}$"),
               TableExportRequestRow.grant_generation == grant.generation)
        .order_by(TableExportReceiptRow.recorded_at.desc(), TableExportReceiptRow.id.desc())
    )
    receipts = [_lineage_receipt(receipt, reviewed_at, binding, plan)
                for receipt, reviewed_at, binding in (await session.execute(statement.limit(_HISTORY_LIMIT))).all()
                ] if grant.effective else []
    observation = None
    if grant.effective:
        # The latest positive observation may be older than the recent window
        # when newer runs all committed zero rows. Do not lose that evidence.
        latest = (await session.execute(statement.where(
            TableExportRequestRow.plan_updated_at == plan.updated_at,
            TableExportRequestRow.binding_digest == plan.binding_digest,
            TableExportReceiptRow.rows_affected > 0,
        ).limit(1))).one_or_none()
        if latest is not None:
            observation = _lineage_receipt(*latest, plan)
    source_schema, source_table = SOURCE_LABEL.split(".")
    return TableExportLineage(
        source=TableExportLineageEndpoint(schema=source_schema, table=source_table),
        destination=TableExportLineageDestination(schema=_TARGET_SCHEMA, table=_TARGET_TABLE,
                                                  label=_DESTINATION_LABELS[destination]),
        declaration=declaration, receipts=receipts, observed=observation is not None, latestObservation=observation,
    )


async def _start(
    session: AsyncSession, req: TableExportRunRequest, actor_id: UUID, binding: str, destination: ExportDestination,
    config: ReceiverConfig, source_marker: UUID,
) -> UUID:
    await _limits(session)
    plan = await _lock_plan(session, destination)
    generation = _require_grant(plan, await _last_grant(session, destination), req, binding)
    if destination == "secondary":
        await _require_local_secondary_sender(session, config.port, source_marker)
    await session.execute(insert(TableExportRequestRow).values(
        destination=destination, request_id=req.requestId, plan_updated_at=req.expectedUpdatedAt, binding_digest=binding,
        grant_generation=generation,
    ).on_conflict_do_nothing())
    request = await session.get(TableExportRequestRow, req.requestId)
    if request is not None and request.destination != destination:
        raise _RunFailure(409, "request_destination_conflict", "This request ID belongs to a different approved receiver")
    if request is None or request.plan_updated_at != req.expectedUpdatedAt or request.binding_digest != binding:
        raise _RunFailure(409, "request_revision_conflict", "This request ID is bound to a different Table Export review")
    if request.grant_generation != generation:
        raise _RunFailure(409, "request_grant_changed", "The request belongs to a different grant and cannot be replayed")
    attempt = TableExportAttemptRow(request_id=req.requestId, initiated_by=actor_id, started_at=datetime.now(timezone.utc))
    attempt_id = attempt.id
    session.add(attempt)
    await session.commit()
    return attempt_id


async def _finish(session: AsyncSession, attempt_id: UUID, state: str, **outcome) -> None:
    result = await session.execute(update(TableExportAttemptRow).where(
        TableExportAttemptRow.id == attempt_id, TableExportAttemptRow.status == "started",
    ).values(status=state, finished_at=datetime.now(timezone.utc), **outcome))
    if result.rowcount != 1:
        raise _RunFailure(503, "execution_unavailable", "The sender attempt could not settle; use the same request ID", "retryable")


async def _pin(session: AsyncSession, request_id: UUID, digest: str) -> None:
    # Keep the singleton plan's lock in the main transaction while this
    # separate app transaction makes the digest durable before receiver DML.
    engine = session.bind
    if not isinstance(engine, AsyncEngine):
        raise _RunFailure(503, "execution_unavailable", "The sender could not pin the reviewed payload", "retryable")
    async with AsyncSession(engine) as ledger:
        await _limits(ledger)
        result = await ledger.execute(update(TableExportRequestRow).where(
            TableExportRequestRow.request_id == request_id, TableExportRequestRow.payload_digest.is_(None),
        ).values(payload_digest=digest))
        if result.rowcount != 1:
            raise _RunFailure(409, "payload_binding_conflict", "The request already has a different prepared payload")
        await ledger.commit()


async def _source_rows(session: AsyncSession, marker: UUID) -> list[dict]:
    engine = session.bind
    if not isinstance(engine, AsyncEngine):
        raise _RunFailure(503, "execution_unavailable", "The sender cannot open a read-only source snapshot", "retryable")
    async with AsyncSession(engine) as source:
        rows = await read_table_export_source(source, marker=marker)
        return [{"id": key, "caption": caption} for key, caption in rows]


def _verify_receipt(receipt: ReceiverReceipt, req: TableExportRunRequest, digest: str | None) -> None:
    if (digest is None or receipt.request_id != req.requestId or receipt.plan_updated_at != req.expectedUpdatedAt
        or receipt.payload_digest != digest or not 0 <= receipt.rows_affected <= MAX_EXPORT_ROWS):
        raise _RunFailure(409, "receipt_conflict", "The receiver receipt does not match this sender's prepared request")


async def _mirror(
    session: AsyncSession, attempt: UUID, proof: ReceiverReceipt, generation: int, *, replayed: bool,
) -> TableExportReceipt:
    row = TableExportReceiptRow(request_id=proof.request_id, rows_affected=proof.rows_affected, recorded_at=proof.committed_at)
    session.add(row)
    await session.flush()
    result = _receipt(row, generation)
    await _finish(session, attempt, "replayed" if replayed else "committed", receipt_id=result.id)
    await session.commit()
    return result


def _failure(error: Exception, *, receiver_may_have_committed: bool) -> _RunFailure:
    if isinstance(error, _RunFailure):
        return error
    if isinstance(error, ReceiverFailure):
        if error.outcome == "outcome_unknown":
            return _RunFailure(503, "outcome_unknown", "Receiver commit could not be confirmed; retry the same request ID", "outcome_unknown")
        if error.outcome == "retryable":
            return _RunFailure(error.status_code, "receiver_unavailable", "Receiver could not be verified; retry the same request ID", "retryable")
        return _RunFailure(error.status_code, "receiver_rejected", "Receiver rejected the export or its reviewed configuration")
    if receiver_may_have_committed:
        return _RunFailure(503, "outcome_unknown", "The sender could not confirm the receipt; retry the same request ID", "outcome_unknown")
    if isinstance(error, DBAPIError) and getattr(error.orig, "sqlstate", None) in {"55P03", "57014", "40P01"}:
        return _RunFailure(409, "target_busy", "Another Table Export is settling; retry the same request ID", "retryable")
    if isinstance(error, HTTPException) and error.status_code < 500:
        return _RunFailure(error.status_code, "source_rejected", "The local synthetic export source failed eligibility or batch limits")
    return _RunFailure(503, "execution_unavailable", "Export did not reach receiver writes; retry the same request ID", "retryable")


async def run_table_export(
    session: AsyncSession, req: TableExportRunRequest, actor_id: UUID, destination: ExportDestination = "primary",
) -> TableExportReceipt:
    provision = _provisioned(destination) if _local() else None
    if not _enabled() or provision is None:
        raise _RunFailure(403, "export_disabled", "Table Export execution is disabled")
    if req.requestId.version != 4:
        raise _RunFailure(422, "invalid_request_id", "Use a UUIDv4 request ID for a manual export")
    source_marker, config, binding = provision
    try:
        attempt = await _start(session, req, actor_id, binding, destination, config, source_marker)
    except _RunFailure:
        await _rollback(session)
        raise
    except HTTPException as error:
        await _rollback(session)
        raise _failure(error, receiver_may_have_committed=False) from None
    except Exception:
        await _rollback(session)
        raise _RunFailure(503, "execution_unavailable", "Sender could not start; retry the same request ID", "retryable") from None

    receiver_may_have_committed = False
    try:
        await _limits(session)
        plan = await _lock_plan(session, destination)
        generation = _require_grant(plan, await _last_grant(session, destination), req, binding)
        request = await session.get(TableExportRequestRow, req.requestId, populate_existing=True)
        if (request is None or request.destination != destination or request.grant_generation != generation
            or request.binding_digest != binding):
            raise _RunFailure(409, "request_grant_changed", "The request belongs to a different grant and cannot be replayed")
        if destination == "secondary":
            await _require_local_secondary_sender(session, config.port, source_marker)
            if not plan.receiver_proof:
                raise _RunFailure(403, "export_unprovisioned", "Review the selected receiver before running an export")
            config = replace(config, physical_proof=plan.receiver_proof)
        prior = (await session.scalars(select(TableExportReceiptRow).where(
            TableExportReceiptRow.request_id == req.requestId,
        ))).one_or_none()
        if prior is not None:
            result = _receipt(prior, generation)
            receiver_may_have_committed = True  # Sender already observed authoritative evidence.
            await _finish(session, attempt, "replayed", receipt_id=result.id)
            await session.commit()
            return result
        proof = await find_export_receipt(config, req.requestId)
        if proof is not None:
            _verify_receipt(proof, req, request.payload_digest)
            receiver_may_have_committed = True
            return await _mirror(session, attempt, proof, generation, replayed=True)
        rows = await _source_rows(session, source_marker)
        digest = export_payload_digest(rows)
        if request.payload_digest is None:
            await _pin(session, req.requestId, digest)
        elif request.payload_digest != digest:
            raise _RunFailure(409, "payload_changed_no_receipt", "The receiver had no receipt and the source payload changed; review a new request")
        receiver_may_have_committed = True
        proof = await apply_export_rows(config, req.requestId, req.expectedUpdatedAt, digest, rows)
        try:
            _verify_receipt(proof, req, digest)
            if proof.rows_affected != len(rows):
                raise ValueError("Unexpected receipt count")
        except (_RunFailure, ValueError):
            raise _RunFailure(503, "outcome_unknown", "The sender could not verify the receiver response; retry the same request ID",
                              "outcome_unknown") from None
        return await _mirror(session, attempt, proof, generation, replayed=False)
    except Exception as error:
        failure = _failure(error, receiver_may_have_committed=receiver_may_have_committed)
        if await _rollback(session):
            try:
                await _limits(session)
                await _finish(session, attempt, failure.outcome, code=failure.code, message=failure.message)
                await session.commit()
            except Exception:
                await _rollback(session)
                logger.warning("Could not settle table export attempt %s", attempt)
        raise failure from None
