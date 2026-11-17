"""Local sender ledger for one deployment-provisioned PostgreSQL Table Export."""

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
    TableExportAttempt, TableExportAttemptRow, TableExportCapabilities, TableExportLineage,
    TableExportLineageDeclaration, TableExportLineageDestination, TableExportLineageEndpoint,
    TableExportLineageReceipt, TableExportMapping, TableExportPlan,
    TableExportPlanRow, TableExportReceipt, TableExportReceiptRow, TableExportRequestRow, TableExportReview,
    TableExportRunRequest,
)
from app.web.core.config import settings
from app.web.services.batch_syncs import _RunFailure, _rollback
from app.web.services.table_export_receiver import (
    ReceiverConfig, ReceiverFailure, ReceiverReceipt, apply_export_rows, export_payload_digest, find_export_receipt,
)
from app.web.services.table_export_source import MAX_CAPTION_BYTES, MAX_EXPORT_ROWS, SOURCE_LABEL, read_table_export_source

_TARGET_SCHEMA = "ts_table_export_receiver"
_TARGET_TABLE = "records"
_HISTORY_LIMIT = 25
logger = logging.getLogger(__name__)


def _provisioned() -> tuple[UUID, ReceiverConfig, str] | None:
    password = settings.TABLE_EXPORT_RECEIVER_PASSWORD
    source_marker = settings.TABLE_EXPORT_SOURCE_MARKER
    marker = settings.TABLE_EXPORT_RECEIVER_MARKER
    address = settings.TABLE_EXPORT_RECEIVER_HOST
    port = settings.TABLE_EXPORT_RECEIVER_PORT
    database = settings.TABLE_EXPORT_RECEIVER_DATABASE
    username = settings.TABLE_EXPORT_RECEIVER_USERNAME
    try:
        host = ip_address(address)
    except ValueError:
        return None
    if (not host.is_loopback or "%" in address or not 1 <= port <= 65535 or not source_marker or not marker
        or not database or not username or password is None or not password.get_secret_value()):
        return None
    config = ReceiverConfig(host=str(host), port=port, database=database, username=username,
                            password=password.get_secret_value(), marker=marker, tls=settings.TABLE_EXPORT_RECEIVER_TLS)
    binding = sha256(json.dumps([
        "table-export-v1", str(source_marker), SOURCE_LABEL, config.host, config.port,
        config.database, config.username, str(config.marker), config.tls, _TARGET_SCHEMA, _TARGET_TABLE,
    ], separators=(",", ":")).encode()).hexdigest()
    return source_marker, config, binding


def _local() -> bool:
    return "ENVIRONMENT" in settings.model_fields_set and settings.ENVIRONMENT == "local"


def _enabled() -> bool:
    return _local() and settings.TABLE_EXPORT_SANDBOX_ENABLED


def table_export_capabilities() -> TableExportCapabilities:
    provisioned = _local() and _provisioned() is not None
    source_schema, source_table = SOURCE_LABEL.split(".")
    return TableExportCapabilities(
        enabled=_enabled() and provisioned, provisioned=provisioned,
        sourceSchema=source_schema, sourceTable=source_table, targetSchema=_TARGET_SCHEMA, targetTable=_TARGET_TABLE,
        maxRows=MAX_EXPORT_ROWS, maxCaptionBytes=MAX_CAPTION_BYTES,
        mapping=[TableExportMapping(sourceColumn=column, targetColumn=column, type=kind)
                 for column, kind in (("id", "bigint"), ("caption", "text"))],
    )


def _plan(row: TableExportPlanRow) -> TableExportPlan:
    return TableExportPlan(id=row.id, name=row.name, updatedAt=row.updated_at)


def _receipt(row: TableExportReceiptRow) -> TableExportReceipt:
    return TableExportReceipt(id=row.id, requestId=row.request_id, rowsAffected=row.rows_affected, recordedAt=row.recorded_at)


async def _limits(session: AsyncSession) -> None:
    await session.execute(text("SET LOCAL search_path = pg_catalog, public, pg_temp"))
    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
    await session.execute(text("SET LOCAL statement_timeout = '5s'"))
    await session.execute(text("SET LOCAL idle_in_transaction_session_timeout = '25s'"))


async def get_table_export(session: AsyncSession) -> TableExportPlan | None:
    row = await session.get(TableExportPlanRow, True)
    return _plan(row) if row is not None else None


async def review_table_export(session: AsyncSession, payload: TableExportReview, actor_id: UUID) -> TableExportPlan:
    provision = _provisioned()
    if provision is None or not _local():
        raise _RunFailure(403, "export_unprovisioned", "Table Export review requires provisioned local sender and receiver settings")
    await _limits(session)
    row = (await session.scalars(select(TableExportPlanRow).where(TableExportPlanRow.singleton)
                                 .with_for_update().execution_options(populate_existing=True))).one_or_none()
    if payload.expectedUpdatedAt != (row.updated_at if row else None):
        raise _RunFailure(409, "plan_changed", "The Table Export review changed; reload it before saving")
    now = datetime.now(timezone.utc)
    if row is None:
        row = TableExportPlanRow(name=payload.name, reviewed_by=actor_id, binding_digest=provision[2], updated_at=now)
        created = (await session.execute(insert(TableExportPlanRow).values(row.model_dump()).on_conflict_do_nothing()
                                         .returning(TableExportPlanRow.singleton))).scalar_one_or_none()
        if created is None:
            raise _RunFailure(409, "plan_changed", "The Table Export review changed; reload it before saving")
    else:
        row.name = payload.name
        row.reviewed_by = actor_id
        row.binding_digest = provision[2]
        row.updated_at = max(now, row.updated_at + timedelta(microseconds=1))
    result = _plan(row)
    await session.commit()
    return result


async def list_table_export_receipts(session: AsyncSession) -> list[TableExportReceipt]:
    rows = await session.scalars(select(TableExportReceiptRow).order_by(TableExportReceiptRow.recorded_at.desc(),
                                TableExportReceiptRow.id.desc()).limit(_HISTORY_LIMIT))
    return [_receipt(row) for row in rows]


async def list_table_export_attempts(session: AsyncSession) -> list[TableExportAttempt]:
    statement = (select(TableExportAttemptRow, TableExportRequestRow.plan_updated_at)
                 .join(TableExportRequestRow, TableExportRequestRow.request_id == TableExportAttemptRow.request_id)
                 .order_by(TableExportAttemptRow.started_at.desc(), TableExportAttemptRow.id.desc()).limit(_HISTORY_LIMIT))
    return [TableExportAttempt(id=row.id, requestId=row.request_id, planUpdatedAt=version, status=row.status,
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


async def get_table_export_lineage(session: AsyncSession) -> TableExportLineage:
    """Read verified sender mirrors only; do not probe source or receiver."""
    plan = await session.get(TableExportPlanRow, True)
    provision = _provisioned() if _local() else None
    binding_matches = bool(plan is not None and provision is not None and provision[2] == plan.binding_digest)
    declaration = None if plan is None else TableExportLineageDeclaration(
        id=plan.id, name=plan.name, reviewedAt=plan.updated_at,
        state=("enabled" if _enabled() else "inactive") if binding_matches else "stale",
    )
    statement = (
        select(TableExportReceiptRow, TableExportRequestRow.plan_updated_at, TableExportRequestRow.binding_digest)
        .join(TableExportRequestRow, TableExportRequestRow.request_id == TableExportReceiptRow.request_id)
        .where(TableExportRequestRow.payload_digest.op("~")("^[0-9a-f]{64}$"))
        .order_by(TableExportReceiptRow.recorded_at.desc(), TableExportReceiptRow.id.desc())
    )
    receipts = [_lineage_receipt(receipt, reviewed_at, binding, plan)
                for receipt, reviewed_at, binding in (await session.execute(statement.limit(_HISTORY_LIMIT))).all()]
    observation = None
    if binding_matches:
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
                                                  label="Approved local receiver (redacted)"),
        declaration=declaration, receipts=receipts, observed=observation is not None, latestObservation=observation,
    )


async def _start(session: AsyncSession, req: TableExportRunRequest, actor_id: UUID) -> UUID:
    await _limits(session)
    plan = await session.get(TableExportPlanRow, True)
    if plan is None:
        raise _RunFailure(409, "review_required", "Save the local Table Export review before running")
    await session.execute(insert(TableExportRequestRow).values(
        request_id=req.requestId, plan_updated_at=req.expectedUpdatedAt, binding_digest=plan.binding_digest,
    ).on_conflict_do_nothing())
    request = await session.get(TableExportRequestRow, req.requestId)
    if request is None or request.plan_updated_at != req.expectedUpdatedAt:
        raise _RunFailure(409, "request_revision_conflict", "This request ID is bound to a different Table Export review")
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
    session: AsyncSession, attempt: UUID, proof: ReceiverReceipt, *, replayed: bool,
) -> TableExportReceipt:
    row = TableExportReceiptRow(request_id=proof.request_id, rows_affected=proof.rows_affected, recorded_at=proof.committed_at)
    session.add(row)
    await session.flush()
    result = _receipt(row)
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


async def run_table_export(session: AsyncSession, req: TableExportRunRequest, actor_id: UUID) -> TableExportReceipt:
    provision = _provisioned()
    if not _enabled() or provision is None:
        raise _RunFailure(403, "export_disabled", "Table Export execution is disabled")
    if req.requestId.version != 4:
        raise _RunFailure(422, "invalid_request_id", "Use a UUIDv4 request ID for a manual export")
    source_marker, config, binding = provision
    try:
        attempt = await _start(session, req, actor_id)
    except _RunFailure:
        await _rollback(session)
        raise
    except Exception:
        await _rollback(session)
        raise _RunFailure(503, "execution_unavailable", "Sender could not start; retry the same request ID", "retryable") from None

    receiver_may_have_committed = False
    try:
        await _limits(session)
        plan = (await session.scalars(select(TableExportPlanRow).where(TableExportPlanRow.singleton).with_for_update()
                                      .execution_options(populate_existing=True))).one()
        request = await session.get(TableExportRequestRow, req.requestId, populate_existing=True)
        prior = (await session.scalars(select(TableExportReceiptRow).where(
            TableExportReceiptRow.request_id == req.requestId,
        ))).one_or_none()
        if prior is not None:
            result = _receipt(prior)
            receiver_may_have_committed = True  # Sender already observed authoritative evidence.
            await _finish(session, attempt, "replayed", receipt_id=result.id)
            await session.commit()
            return result
        if request.binding_digest != binding or (plan.updated_at == req.expectedUpdatedAt and plan.binding_digest != binding):
            raise _RunFailure(409, "destination_changed", "Export provisioning changed; review a new request before sending")
        if request.payload_digest is None and plan.updated_at != req.expectedUpdatedAt:
            raise _RunFailure(409, "plan_changed", "The Table Export review changed before this request was prepared")
        proof = await find_export_receipt(config, req.requestId)
        if proof is not None:
            _verify_receipt(proof, req, request.payload_digest)
            receiver_may_have_committed = True
            return await _mirror(session, attempt, proof, replayed=True)
        if plan.updated_at != req.expectedUpdatedAt or plan.binding_digest != binding:
            raise _RunFailure(409, "plan_changed", "The Table Export review changed; no new payload was sent for this request")
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
        return await _mirror(session, attempt, proof, replayed=False)
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
