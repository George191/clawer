"""Transport for one provisioned PostgreSQL receiver in the local export pilot."""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from ipaddress import ip_address
import json
import re
import ssl
from typing import Literal
from uuid import UUID

import asyncpg
from anyio import fail_after
from fastapi import HTTPException

from app.web.core.config import settings

_SCHEMA = "ts_table_export_receiver"
_RECORDS = f"{_SCHEMA}.records"
_RECEIPTS = f"{_SCHEMA}.receipts"
_IDENTITY = f"{_SCHEMA}.export_receiver_identity"
# Single receiver schema per database: check and write use the same xact
# fence so an unsettled commit cannot be mistaken for an absent receipt.
_RECEIVER_FENCE = 6362555265112375041
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MAX_ROWS = 100
_MAX_CAPTION_BYTES = 8192


@dataclass(frozen=True)
class ReceiverConfig:
    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)
    marker: UUID
    tls: bool = False


@dataclass(frozen=True)
class ReceiverReceipt:
    request_id: UUID
    plan_updated_at: datetime
    payload_digest: str
    rows_affected: int
    committed_at: datetime


class ReceiverFailure(HTTPException):
    def __init__(
        self, status_code: int, code: str, detail: str,
        outcome: Literal["rejected", "retryable", "outcome_unknown"] = "rejected",
    ):
        super().__init__(status_code=status_code, detail=detail)
        self.code = code
        self.outcome = outcome


def _receiver_error(error: Exception, *, committing: bool) -> ReceiverFailure:
    code = getattr(error, "sqlstate", None)
    if committing:
        return ReceiverFailure(503, "receiver_commit_unknown",
                               "Receiver commit was not confirmed; reconcile the same request", "outcome_unknown")
    if code in {"23502", "23503", "23505", "23514", "22001", "42P01", "42703", "42501"}:
        return ReceiverFailure(422, "receiver_rejected", "Receiver rejected the export; nothing was committed")
    return ReceiverFailure(503, "receiver_unavailable", "The approved receiver could not complete the request", "retryable")


@asynccontextmanager
async def _connect(config: ReceiverConfig):
    try:
        address = ip_address(config.host)
    except (TypeError, ValueError):
        raise ReceiverFailure(422, "receiver_not_approved", "Receiver must use the approved numeric loopback endpoint") from None
    if (settings.ENVIRONMENT != "local" or not address.is_loopback or "%" in config.host
            or not isinstance(config.marker, UUID) or not 1 <= config.port <= 65535
            or not config.database or not config.username or not config.password):
        raise ReceiverFailure(422, "receiver_not_approved", "Receiver must use the approved numeric loopback endpoint")
    connection = None
    try:
        with fail_after(8):
            connection = await asyncpg.connect(
                host=str(address), port=config.port, database=config.database, user=config.username, password=config.password,
                ssl=ssl.create_default_context() if config.tls else False, timeout=3, command_timeout=3,
                server_settings={"search_path": "pg_catalog", "statement_timeout": "2000", "lock_timeout": "750",
                                 "idle_in_transaction_session_timeout": "5000", "application_name": "clawer_table_export"},
            )
            yield connection
    finally:
        if connection is not None:
            connection.terminate()


# Validate fixed relation names and shapes, and reject receiver-side features
# that could redirect the write, run code, or mutate rows outside this contract.
async def _verify_tables(connection: asyncpg.Connection, *, writing: bool) -> None:
    names = ["export_receiver_identity", "receipts", "records"] if writing else ["export_receiver_identity", "receipts"]
    actual = await connection.fetch("""
        SELECT c.relname::text AS name,
               ARRAY(SELECT a.attname::text || ':' || a.atttypid::text || ':' || a.attnotnull::text
                     FROM pg_catalog.pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                     ORDER BY a.attnum) AS columns,
               ARRAY(SELECT a.attname::text FROM pg_catalog.pg_attribute a
                     WHERE a.attrelid = c.oid AND a.attnum = i.indkey[0]) AS key_column
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_am h ON h.oid = c.relam AND h.amname = 'heap'
        JOIN pg_catalog.pg_index i ON i.indrelid = c.oid AND i.indisprimary AND i.indisvalid
          AND i.indisready AND i.indislive AND i.indnkeyatts = 1 AND i.indimmediate
        JOIN pg_catalog.pg_opclass opc ON opc.oid = i.indclass[0]
          AND opc.opcdefault AND opc.opcnamespace = 'pg_catalog'::regnamespace
        WHERE n.nspname::text = $1 AND c.relname::text = ANY($2::text[])
          AND c.relkind = 'r' AND NOT c.relispartition AND NOT c.relrowsecurity AND NOT c.relhasrules
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits p WHERE p.inhrelid = c.oid OR p.inhparent = c.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid = c.oid)
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid = c.oid
                          AND a.attnum > 0 AND NOT a.attisdropped
                          AND (a.attidentity <> '' OR a.attgenerated <> '' OR a.atttypmod <> -1
                               OR a.atthasdef OR (a.attcollation <> 0 AND a.attcollation <> 'pg_catalog."default"'::regcollation)))
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k WHERE k.conrelid = c.oid AND k.contype NOT IN ('p', 'n'))
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_index other
                          WHERE other.indrelid = c.oid AND other.indexrelid <> i.indexrelid)
    """, _SCHEMA, names)
    expected = {
        "export_receiver_identity": (["marker:2950:true"], ["marker"]),
        "receipts": (["request_id:2950:true", "plan_updated_at:1184:true", "payload_digest:25:true",
                      "rows_affected:23:true", "committed_at:1184:true"], ["request_id"]),
        "records": (["id:20:true", "caption:25:true"], ["id"]),
    }
    if {r["name"]: (r["columns"], r["key_column"]) for r in actual} != {name: expected[name] for name in names}:
        raise ReceiverFailure(422, "receiver_contract", "The approved receiver does not match the provisioned table contract")


async def _verify_receiver(connection: asyncpg.Connection, config: ReceiverConfig, *, writing: bool) -> None:
    # Hold relation locks across validation and use, preventing a DDL swap.
    # The receiver service role must be read-only on the marker table.
    await connection.execute(f"LOCK TABLE ONLY {_IDENTITY} IN ACCESS SHARE MODE")
    if writing:
        await connection.execute(f"LOCK TABLE ONLY {_RECEIPTS}, ONLY {_RECORDS} IN ROW EXCLUSIVE MODE")
    else:
        await connection.execute(f"LOCK TABLE ONLY {_RECEIPTS} IN ACCESS SHARE MODE")
    await _verify_tables(connection, writing=writing)
    markers = await connection.fetch(f"SELECT marker FROM ONLY {_IDENTITY} LIMIT 2")
    if len(markers) != 1 or markers[0]["marker"] != config.marker:
        raise ReceiverFailure(422, "receiver_identity_mismatch", "The approved receiver identity did not match")


async def _fence_receiver(connection: asyncpg.Connection) -> None:
    await connection.execute("SELECT pg_catalog.pg_advisory_xact_lock($1::bigint)", _RECEIVER_FENCE)


async def _read_receipt(connection: asyncpg.Connection, request_id: UUID) -> ReceiverReceipt | None:
    row = await connection.fetchrow(f"""
        SELECT request_id, plan_updated_at, payload_digest, rows_affected, committed_at
        FROM ONLY {_RECEIPTS} WHERE request_id = $1
    """, request_id)
    if row is None:
        return None
    receipt = ReceiverReceipt(**dict(row))
    if not _DIGEST.fullmatch(receipt.payload_digest) or not 0 <= receipt.rows_affected <= _MAX_ROWS:
        raise ReceiverFailure(503, "receiver_receipt_invalid", "Receiver receipt could not be verified", "outcome_unknown")
    return receipt


async def find_export_receipt(config: ReceiverConfig, request_id: UUID) -> ReceiverReceipt | None:
    """Read authoritative receiver evidence; never return success from transport status."""
    try:
        async with _connect(config) as connection:
            # READ COMMITTED takes a new snapshot for the receipt SELECT after
            # the fence is acquired, not a stale snapshot from while it waited.
            async with connection.transaction(isolation="read_committed", readonly=True):
                await _fence_receiver(connection)
                await _verify_receiver(connection, config, writing=False)
                return await _read_receipt(connection, request_id)
    except ReceiverFailure:
        raise
    except Exception as error:
        raise _receiver_error(error, committing=False) from None


def export_payload_digest(rows: list[dict]) -> str:
    """Validate the exact wire projection and pin a stable, key-ordered digest."""
    if type(rows) is not list or len(rows) > _MAX_ROWS:
        raise ReceiverFailure(422, "receiver_limit", "The export exceeds the receiver row limit")
    payload = []
    keys = set()
    for row in rows:
        if type(row) is not dict or set(row) != {"id", "caption"}:
            raise ReceiverFailure(422, "receiver_projection", "The export does not match the approved projection")
        key, caption = row["id"], row["caption"]
        if type(key) is not int or not -(2**63) <= key < 2**63 or key in keys or type(caption) is not str or "\x00" in caption:
            raise ReceiverFailure(422, "receiver_projection", "The export does not match the approved projection")
        try:
            if len(caption.encode("utf-8")) > _MAX_CAPTION_BYTES:
                raise ReceiverFailure(422, "receiver_limit", "The export exceeds the receiver row byte limit")
        except UnicodeError:
            raise ReceiverFailure(422, "receiver_projection", "The export does not match the approved projection") from None
        keys.add(key)
        payload.append((key, caption))
    return sha256(json.dumps([(str(key), caption) for key, caption in sorted(payload)], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


async def apply_export_rows(
    config: ReceiverConfig, request_id: UUID, plan_updated_at: datetime, sent_digest: str, rows: list[dict],
) -> ReceiverReceipt:
    """Upsert typed rows and write their receipt in the same receiver transaction."""
    if export_payload_digest(rows) != sent_digest:
        raise ReceiverFailure(409, "receiver_digest_mismatch", "The export does not match the pinned payload digest")
    committing = False
    try:
        async with _connect(config) as connection:
            transaction = connection.transaction(isolation="read_committed")
            await transaction.start()
            await _fence_receiver(connection)
            await _verify_receiver(connection, config, writing=True)
            prior = await _read_receipt(connection, request_id)
            if prior is not None:
                if prior.plan_updated_at != plan_updated_at or prior.payload_digest != sent_digest or prior.rows_affected != len(rows):
                    raise ReceiverFailure(409, "receiver_request_conflict", "This request already has a different receiver receipt")
                return prior
            if rows:
                affected = await connection.fetchval(f"""
                    WITH merged AS (
                        INSERT INTO {_RECORDS} (id, caption)
                        SELECT id, caption FROM unnest($1::bigint[], $2::text[]) AS payload(id, caption) ORDER BY id
                        ON CONFLICT (id) DO UPDATE SET caption = EXCLUDED.caption RETURNING 1
                    )
                    SELECT count(*) FROM merged
                """, [row["id"] for row in rows], [row["caption"] for row in rows])
                if affected != len(rows):
                    raise ReceiverFailure(422, "receiver_row_mismatch", "Receiver did not acknowledge the complete batch")
            row = await connection.fetchrow(f"""
                INSERT INTO {_RECEIPTS} (request_id, plan_updated_at, payload_digest, rows_affected, committed_at)
                VALUES ($1, $2, $3, $4, pg_catalog.clock_timestamp())
                RETURNING request_id, plan_updated_at, payload_digest, rows_affected, committed_at
            """, request_id, plan_updated_at, sent_digest, len(rows))
            receipt = ReceiverReceipt(**dict(row))
            committing = True
            await transaction.commit()
            return receipt
    except ReceiverFailure:
        raise
    except Exception as error:
        raise _receiver_error(error, committing=committing) from None
