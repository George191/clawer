"""Internal writer for complete, immutable snapshots in the local publication fixture.

No application route invokes this service. The preview receiver is not a publication authority.
"""

import asyncio
from datetime import datetime
from hashlib import sha256
import hmac
import json
import re
from typing import Iterable
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.web.services.table_export_snapshot_publication_receiver import (
    SNAPSHOT_PUBLICATION_SCHEMA,
    configured_snapshot_publication_profile,
    lock_snapshot_publication_reconciliation,
    recheck_snapshot_publication_receiver,
    snapshot_publication_connection,
    verify_snapshot_publication_receiver,
)

_MAX_GENERATION = 16
_MAX_ROWS = 100
_MAX_BYTES = 64 * 1024
_MAX_CAPTION_BYTES = 8192
_KEY = re.compile(r"(?:0|-?[1-9][0-9]{0,18})", re.ASCII)
_SCOPE = SNAPSHOT_PUBLICATION_SCHEMA


class SnapshotPublicationConflict(Exception):
    """The exact request or reviewed predecessor conflicts; no publication by this call."""


class SnapshotPublicationUnavailable(Exception):
    """Publication unconfirmed; reconcile by retrying the identical request only."""


class SnapshotPublicationRow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str
    caption: str | None


class SnapshotPublicationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id: UUID
    generation: int
    previous_generation: int
    row_count: int
    recorded_at: datetime


class SnapshotPublicationHead(BaseModel):
    model_config = ConfigDict(frozen=True)
    generation: int
    row_count: int | None = None
    recorded_at: datetime | None = None


def _normalize(rows: Iterable[SnapshotPublicationRow]) -> list[tuple[int, str | None]]:
    canonical: dict[int, str | None] = {}
    size = 0
    for row in rows:
        if (len(canonical) == _MAX_ROWS or not isinstance(row, SnapshotPublicationRow)
            or not isinstance(row.id, str) or not _KEY.fullmatch(row.id)):
            raise ValueError("Invalid local snapshot manifest")
        key, caption = int(row.id), row.caption
        if key < -(2**63) or key >= 2**63 or key in canonical:
            raise ValueError("Invalid local snapshot key")
        if caption is not None and (
            not isinstance(caption, str) or len(caption) > _MAX_CAPTION_BYTES or "\0" in caption
        ):
            raise ValueError("Invalid local snapshot caption")
        try:
            caption_size = len(caption.encode("utf-8")) if caption is not None else 0
        except UnicodeError:
            raise ValueError("Invalid local snapshot caption") from None
        size += 8 + caption_size
        if caption_size > _MAX_CAPTION_BYTES or size > _MAX_BYTES:
            raise ValueError("Invalid local snapshot size")
        canonical[key] = caption
    if not canonical:
        raise ValueError("Empty local snapshot")
    return sorted(canonical.items())


def _seal(rows: list[tuple[int, str | None]]) -> str:
    # ID strings preserve the full signed width; caption null and empty are distinct.
    payload = json.dumps([(str(key), caption) for key, caption in rows],
                         ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return sha256(b"synthetic-table-export-publication-v1\0" + payload).hexdigest()


def _unavailable() -> SnapshotPublicationUnavailable:
    return SnapshotPublicationUnavailable("Snapshot publication unconfirmed; retry the same reviewed request")


async def _replay(connection, prior, *, request_id: UUID, previous: int, digest: str):
    generation, count, recorded = prior["generation"], prior["row_count"], prior["recorded_at"]
    if prior["previous_generation"] != previous or not hmac.compare_digest(prior["payload_hash"], digest):
        raise SnapshotPublicationConflict("Request does not match its recorded snapshot")
    if (not 1 <= generation <= _MAX_GENERATION or generation != previous + 1 or not 1 <= count <= _MAX_ROWS
        or recorded.tzinfo is None or recorded.year in (1, 9999)):
        raise _unavailable()
    await _verify_sealed_snapshot(connection, generation=generation, count=count, digest=digest)
    return SnapshotPublicationResult(request_id=request_id, generation=generation,
                                     previous_generation=previous, row_count=count, recorded_at=recorded)


async def _verify_sealed_snapshot(connection, *, generation: int, count: int, digest: str) -> None:
    # One MVCC statement bounds both bytes and row count before sending payload to the client.
    stored = await connection.fetch(f"""
        WITH snapshot AS MATERIALIZED (
            SELECT id, caption, COALESCE(pg_catalog.octet_length(caption),0) AS size
            FROM ONLY {_SCOPE}.records WHERE generation=$1 ORDER BY id LIMIT 101
        ), budget AS (
            SELECT pg_catalog.count(*) = $2::bigint
               AND pg_catalog.max(size) <= $3::int
               AND pg_catalog.sum(size + 8) <= $4::bigint AS valid
            FROM snapshot
        )
        SELECT id, CASE WHEN budget.valid THEN caption END AS caption, budget.valid
        FROM snapshot CROSS JOIN budget ORDER BY id
    """, generation, count, _MAX_CAPTION_BYTES, _MAX_BYTES)
    if len(stored) != count or not all(row["valid"] for row in stored) or not hmac.compare_digest(
        digest, _seal(_normalize(SnapshotPublicationRow(id=str(r["id"]), caption=r["caption"]) for r in stored))
    ):
        raise _unavailable()

async def publish_snapshot(
    request_id: UUID, expected_previous_generation: int, rows: Iterable[SnapshotPublicationRow], *,
    expected_config_revision: UUID, sender_port: int, sender_database: str, sender_username: str, sender_marker: UUID,
) -> SnapshotPublicationResult:
    """Advance only the dedicated fixture pointer; no sender reads or prior-generation writes."""
    if (not isinstance(request_id, UUID) or request_id.version != 4
        or type(expected_previous_generation) is not int or not 0 <= expected_previous_generation <= _MAX_GENERATION):
        raise ValueError("Invalid local snapshot review")
    canonical = _normalize(rows)
    digest = _seal(canonical)
    profile = configured_snapshot_publication_profile()
    if profile is None or not isinstance(expected_config_revision, UUID) or profile.config_revision != expected_config_revision:
        raise _unavailable()
    try:
        async with asyncio.timeout(5):
            async with snapshot_publication_connection(
                profile, sender_port=sender_port, sender_database=sender_database,
                sender_username=sender_username, sender_marker=sender_marker,
            ) as connection:
                async with connection.transaction():
                    await connection.execute("SET LOCAL lock_timeout = '400ms'")
                    await connection.execute("SET LOCAL statement_timeout = '750ms'")
                    await verify_snapshot_publication_receiver(connection, profile)
                    state = await connection.fetchrow(f"""
                        SELECT marker,current_generation FROM ONLY {_SCOPE}.state WHERE singleton IS TRUE FOR UPDATE
                    """)
                    if state is None or state["marker"] != profile.marker:
                        raise _unavailable()
                    head = state["current_generation"]
                    if configured_snapshot_publication_profile() != profile or not 0 <= head <= _MAX_GENERATION:
                        raise _unavailable()
                    prior = await connection.fetchrow(f"""
                        SELECT generation,previous_generation,payload_hash,row_count,recorded_at
                        FROM ONLY {_SCOPE}.generations WHERE request_id=$1
                    """, request_id)
                    if prior is not None:
                        if prior["generation"] > head:
                            raise _unavailable()
                        return await _replay(connection, prior, request_id=request_id,
                                             previous=expected_previous_generation, digest=digest)
                    if head != expected_previous_generation or head >= _MAX_GENERATION:
                        raise SnapshotPublicationConflict("Reviewed snapshot predecessor unavailable")
                    persisted_head = await connection.fetchval(f"""
                        SELECT generation FROM ONLY {_SCOPE}.generations ORDER BY generation DESC LIMIT 1
                    """)
                    if (persisted_head if persisted_head is not None else 0) != head:
                        raise _unavailable()
                    generation = head + 1
                    recorded = await connection.fetchval(f"""
                        INSERT INTO {_SCOPE}.generations
                            (generation,previous_generation,request_id,payload_hash,row_count,recorded_at)
                        VALUES ($1,$2,$3,$4,$5,pg_catalog.clock_timestamp()) RETURNING recorded_at
                    """, generation, head, request_id, digest, len(canonical))
                    await connection.executemany(f"""
                        INSERT INTO {_SCOPE}.records (generation,id,caption) VALUES ($1,$2,$3)
                    """, [(generation, key, caption) for key, caption in canonical])
                    if await connection.execute(f"""
                        UPDATE ONLY {_SCOPE}.state SET current_generation=$1
                        WHERE singleton IS TRUE AND current_generation=$2
                    """, generation, head) != "UPDATE 1":
                        raise _unavailable()
                    result = SnapshotPublicationResult(request_id=request_id, generation=generation,
                                                       previous_generation=head, row_count=len(canonical),
                                                       recorded_at=recorded)
                return result
    except (SnapshotPublicationConflict, SnapshotPublicationUnavailable):
        raise
    except Exception:
        # A timeout or transport failure across COMMIT may have left a durable result.
        raise _unavailable() from None



async def reconcile_snapshot(
    request_id: UUID, expected_previous_generation: int, expected_payload_digest: str, *,
    expected_config_revision: UUID, sender_port: int, sender_database: str, sender_username: str, sender_marker: UUID,
) -> SnapshotPublicationResult:
    """Confirm exact original durable publication without supplying values or granting resend.

    Unknown/absent and denied outcomes intentionally have the same meaning: unconfirmed.
    """
    if (not isinstance(request_id, UUID) or request_id.version != 4
        or type(expected_previous_generation) is not int or not 0 <= expected_previous_generation < _MAX_GENERATION
        or not isinstance(expected_payload_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_payload_digest, re.ASCII)):
        raise _unavailable()
    profile = configured_snapshot_publication_profile()
    if profile is None or not isinstance(expected_config_revision, UUID) or profile.config_revision != expected_config_revision:
        raise _unavailable()
    try:
        async with asyncio.timeout(5):
            async with snapshot_publication_connection(
                profile, sender_port=sender_port, sender_database=sender_database,
                sender_username=sender_username, sender_marker=sender_marker,
            ) as connection:
                async with connection.transaction(isolation="repeatable_read", readonly=True):
                    await connection.execute("SET LOCAL lock_timeout = '400ms'")
                    await connection.execute("SET LOCAL statement_timeout = '750ms'")
                    await lock_snapshot_publication_reconciliation(connection)
                    await recheck_snapshot_publication_receiver(connection, profile)
                    head = await connection.fetchval(f"""
                        SELECT current_generation FROM ONLY {_SCOPE}.state WHERE singleton IS TRUE
                    """)
                    prior = await connection.fetchrow(f"""
                        SELECT generation,previous_generation,payload_hash,row_count,recorded_at
                        FROM ONLY {_SCOPE}.generations WHERE request_id=$1
                    """, request_id)
                    if (head is None or not 1 <= head <= _MAX_GENERATION or prior is None
                        or not 1 <= prior["generation"] <= head):
                        raise _unavailable()
                    result = await _replay(connection, prior, request_id=request_id,
                                           previous=expected_previous_generation, digest=expected_payload_digest)
                    await recheck_snapshot_publication_receiver(connection, profile)
                    if configured_snapshot_publication_profile() != profile:
                        raise _unavailable()
                return result
    except Exception:
        # No pointer, row, source, or reservation write, including on miss or uncertainty.
        raise _unavailable() from None



async def _record_generation_edge(connection, *, descending: bool) -> int | None:
    order, direction = ("DESC", "Backward") if descending else ("ASC", "Forward")
    query = f"""SELECT r.generation FROM ONLY {_SCOPE}.records AS r
                ORDER BY r.generation {order}, r.id {order} LIMIT 1"""
    raw = await connection.fetchval(f"EXPLAIN (FORMAT JSON, VERBOSE TRUE, ANALYZE FALSE) {query}")
    root = json.loads(raw)[0]["Plan"]
    children = root.get("Plans", [])
    if root.get("Node Type") != "Limit" or len(children) != 1:
        raise _unavailable()
    child = children[0]
    if (child.get("Node Type") not in {"Index Scan", "Index Only Scan"}
        or child.get("Schema") != _SCOPE or child.get("Relation Name") != "records" or child.get("Alias") != "r"
        or child.get("Index Name") != "records_pkey" or child.get("Scan Direction") != direction
        or any(node.get("Parallel Aware") or any(key in node for key in (
            "Filter", "Recheck Cond", "Index Cond", "InitPlan", "Subplan Name",
        )) for node in (root, child)) or child.get("Plans")):
        raise _unavailable()
    return await connection.fetchval(query)


async def read_snapshot_publication_head(
    *, expected_config_revision: UUID, sender_port: int, sender_database: str, sender_username: str,
    sender_marker: UUID,
) -> SnapshotPublicationHead:
    """Return the receiver's verified head in one snapshot, not authority to publish."""
    profile = configured_snapshot_publication_profile()
    if profile is None or not isinstance(expected_config_revision, UUID) or profile.config_revision != expected_config_revision:
        raise _unavailable()
    try:
        async with asyncio.timeout(5):
            async with snapshot_publication_connection(
                profile, sender_port=sender_port, sender_database=sender_database,
                sender_username=sender_username, sender_marker=sender_marker,
            ) as connection:
                async with connection.transaction(isolation="repeatable_read", readonly=True):
                    await connection.execute("SET LOCAL lock_timeout = '400ms'")
                    await connection.execute("SET LOCAL statement_timeout = '750ms'")
                    await lock_snapshot_publication_reconciliation(connection)
                    await recheck_snapshot_publication_receiver(connection, profile)
                    head = await connection.fetchval(f"""
                        SELECT current_generation FROM ONLY {_SCOPE}.state WHERE singleton IS TRUE
                    """)
                    if head is None or not 0 <= head <= _MAX_GENERATION:
                        raise _unavailable()
                    headers = await connection.fetch(f"""
                        SELECT generation, previous_generation, row_count, recorded_at,
                               CASE WHEN pg_catalog.octet_length(payload_hash)=64 THEN payload_hash END AS payload_hash,
                               pg_catalog.isfinite(recorded_at) AS finite
                        FROM ONLY {_SCOPE}.generations ORDER BY generation LIMIT 17
                    """)
                    if len(headers) != head:
                        raise _unavailable()
                    for generation, row in enumerate(headers, start=1):
                        if (row["generation"] != generation or row["previous_generation"] != generation - 1
                            or not 1 <= row["row_count"] <= _MAX_ROWS or not row["finite"]
                            or row["recorded_at"].tzinfo is None or row["recorded_at"].year in (1, 9999)
                            or row["payload_hash"] is None
                            or not re.fullmatch(r"[0-9a-f]{64}", row["payload_hash"], re.ASCII)):
                            raise _unavailable()
                    first_generation = await _record_generation_edge(connection, descending=False)
                    last_generation = await _record_generation_edge(connection, descending=True)
                    if head == 0:
                        if first_generation is not None or last_generation is not None:
                            raise _unavailable()
                    elif (first_generation is None or last_generation is None
                          or not 1 <= first_generation <= last_generation <= head):
                        raise _unavailable()
                    if head == 0:
                        result = SnapshotPublicationHead(generation=0)
                    else:
                        header = headers[-1]
                        await _verify_sealed_snapshot(
                            connection, generation=head, count=header["row_count"], digest=header["payload_hash"],
                        )
                        result = SnapshotPublicationHead(
                            generation=head, row_count=header["row_count"], recorded_at=header["recorded_at"],
                        )
                    await recheck_snapshot_publication_receiver(connection, profile)
                    if configured_snapshot_publication_profile() != profile:
                        raise _unavailable()
                return result
    except Exception:
        raise _unavailable() from None
