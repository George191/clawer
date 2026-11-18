"""Freeze a complete sender-local synthetic publication intent; never publish."""

import asyncio
from datetime import datetime
from hashlib import sha256
from ipaddress import ip_address
import hmac
import json
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.core.config import settings
from app.web.services.snapshot_intent_scope import SNAPSHOT_INTENT_PRIVATE_SCHEMA
from app.web.services.table_export_snapshot import _sender_scope
from app.web.services.table_export_snapshot_publication import (
    SnapshotPublicationRow, _normalize, _seal, read_snapshot_publication_head,
)
from app.web.services.table_export_snapshot_publication_receiver import configured_snapshot_publication_profile
from app.web.services.table_export_source import _read_source, require_export_sender_marker

_PRIVATE = SNAPSHOT_INTENT_PRIVATE_SCHEMA
_SOURCE = "ts_table_export_sandbox"
_MAX_INTENTS = 16
_SOURCE_RELATIONS = ("identity", "records")
_PRIVATE_RELATIONS = ("identity", "intents", "records")
_DELIVERY_RELATIONS = ("deliveries",)


class SnapshotPublicationIntentUnavailable(Exception):
    """No trusted prepared-only intent could be confirmed."""


class SnapshotPublicationIntent(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    config_revision: UUID
    expected_previous_generation: int
    row_count: int
    created_at: datetime


def _unavailable():
    return SnapshotPublicationIntentUnavailable("Synthetic snapshot intent unconfirmed; use the exact original request")


async def _lock_scope(session: AsyncSession, schema: str, names: tuple[str, ...]) -> None:
    # These are two fixed callers with compile-time schema/relation sets; no request identifiers reach DDL.
    if (schema, names) not in {(_SOURCE, _SOURCE_RELATIONS), (_PRIVATE, _PRIVATE_RELATIONS), (_PRIVATE, _DELIVERY_RELATIONS)}:
        raise _unavailable()
    qualifier = ", ".join(f'ONLY "{schema}"."{table}"' for table in names)
    mode = "SHARE ROW EXCLUSIVE" if schema == _PRIVATE else "SHARE UPDATE EXCLUSIVE"
    await session.execute(text(f"LOCK TABLE {qualifier} IN {mode} MODE NOWAIT"))


async def _prove_locked_scope(
    session: AsyncSession, schema: str, names: tuple[str, ...], *, read_only: bool = False,
) -> str:
    rows = (await session.execute(text("""
        SELECT n.oid::bigint, c.relname::text, c.oid::bigint, pg_catalog.pg_relation_filenode(c.oid)::bigint,
               COALESCE((SELECT jsonb_agg(jsonb_build_array(
                         idx.oid::bigint, pg_catalog.pg_relation_filenode(idx.oid)::bigint, i.indisprimary,
                         i.indisunique, i.indisvalid, i.indislive, i.indisready,
                         pg_catalog.pg_get_indexdef(idx.oid)) ORDER BY idx.relname COLLATE "C")
                         FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class idx ON idx.oid=i.indexrelid
                         WHERE i.indrelid=c.oid), '[]'::jsonb),
               COALESCE((SELECT jsonb_agg(jsonb_build_array(
                            a.attnum,a.attname::text,a.atttypid::bigint,a.attnotnull,a.attgenerated::text,
                            a.attidentity::text,a.atttypmod,pg_catalog.pg_get_expr(d.adbin,d.adrelid))
                         ORDER BY a.attnum)
                         FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d
                           ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                         WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped), '[]'::jsonb),
               COALESCE((SELECT jsonb_agg(jsonb_build_array(
                             k.contype::text,k.convalidated,k.condeferrable,k.condeferred,
                             pg_catalog.pg_get_constraintdef(k.oid))
                          ORDER BY k.conname COLLATE "C")
                         FROM pg_catalog.pg_constraint k WHERE k.conrelid=c.oid), '[]'::jsonb)
        FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_class c ON c.relnamespace=n.oid
        JOIN pg_catalog.pg_am m ON m.oid=c.relam AND m.amname='heap'
        WHERE n.nspname=:schema AND c.relname::text=ANY(CAST(:names AS text[]))
          AND c.relkind='r' AND c.relpersistence='p' AND NOT c.relispartition
          AND NOT (c.relrowsecurity OR c.relforcerowsecurity OR c.relhasrules)
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE c.oid IN (i.inhparent,i.inhrelid))
          AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
          AND (:schema <> :private OR NOT EXISTS (
              SELECT 1 FROM pg_catalog.pg_constraint k
              WHERE k.conrelid=c.oid AND (
                  NOT k.convalidated OR (k.contype NOT IN ('c','n','p','u') AND NOT (
                      k.contype='f' AND c.relname='records'
                      AND k.confrelid=pg_catalog.to_regclass(:intents)
                      AND k.conkey=ARRAY[1]::smallint[] AND k.confkey=ARRAY[1]::smallint[]
                      AND k.confupdtype='a' AND k.confdeltype='a' AND k.confmatchtype='s'
                      AND NOT (k.condeferrable OR k.condeferred)
                  ))
              )
          ))
          AND EXISTS (SELECT 1 FROM pg_catalog.pg_locks l WHERE l.pid=pg_catalog.pg_backend_pid()
                       AND l.locktype='relation' AND l.granted AND l.relation=c.oid
                       AND l.mode=:mode)
        ORDER BY c.relname COLLATE "C"
    """), {"schema": schema, "names": list(names), "private": _PRIVATE, "intents": f"{_PRIVATE}.intents",
            "mode": "AccessShareLock" if read_only else "ShareRowExclusiveLock" if schema == _PRIVATE else "ShareUpdateExclusiveLock"})).all()
    if tuple(row[1] for row in rows) != names or any(row[3] is None for row in rows):
        raise _unavailable()
    canonical = json.dumps((schema, [tuple(row) for row in rows]), separators=(",", ":"), ensure_ascii=False)
    return sha256(canonical.encode("utf-8")).hexdigest()


async def compute_snapshot_intent_source_proof(session: AsyncSession) -> str:
    """Operator enrollment for the fixed fixture. Uses the same physical locks and catalog as preparation."""
    await _lock_scope(session, _SOURCE, _SOURCE_RELATIONS)
    return await _prove_locked_scope(session, _SOURCE, _SOURCE_RELATIONS)


async def compute_snapshot_intent_private_proof(session: AsyncSession) -> str:
    """Operator enrollment for the three fixed private relations."""
    await _lock_scope(session, _PRIVATE, _PRIVATE_RELATIONS)
    return await _prove_locked_scope(session, _PRIVATE, _PRIVATE_RELATIONS)


def _scope_digest(actor_id, grant, sender) -> str:
    binding = [
        "asiral:synthetic-snapshot-intent-v1", str(actor_id), str(grant.sourceMarker), grant.sourceProofSha256,
        str(grant.privateMarker), grant.privateProofSha256, str(grant.configRevision),
        sender.address, sender.port, sender.database, sender.username,
    ]
    return sha256(json.dumps(binding, separators=(",", ":")).encode()).hexdigest()


async def _check_sender(session: AsyncSession, sender) -> None:
    actual = await session.scalar(text("""
        SELECT pg_catalog.inet_server_addr()=CAST(:address AS inet)
            AND pg_catalog.inet_server_port()=:port AND pg_catalog.current_database()=:database
            AND current_user=:username AND session_user=:username
    """), vars(sender))
    if actual is not True:
        raise _unavailable()


async def _verify_frozen(session: AsyncSession, intent) -> list[tuple[int, str | None]]:
    count = intent["row_count"]
    records = (await session.execute(text(f"""
        WITH selected AS MATERIALIZED (
            SELECT ordinal, id, caption, COALESCE(pg_catalog.octet_length(caption),0) AS bytes
            FROM ONLY {_PRIVATE}.records WHERE intent_id=:intent ORDER BY ordinal LIMIT 101
        ), budget AS (
            SELECT pg_catalog.count(*)=:count AND pg_catalog.max(bytes)<=8192
               AND pg_catalog.sum(bytes+8)<=65536 AS valid FROM selected
        )
        SELECT ordinal,id,CASE WHEN budget.valid THEN caption END AS caption,budget.valid
        FROM selected CROSS JOIN budget ORDER BY ordinal
    """), {"intent": intent["id"], "count": count})).mappings().all()
    if (not 1 <= count <= 100 or len(records) != count
        or any(not record["valid"] or record["ordinal"] != n for n, record in enumerate(records, 1))):
        raise _unavailable()
    rows = [(r["id"], r["caption"]) for r in records]
    canonical = _normalize(SnapshotPublicationRow(id=str(key), caption=caption) for key, caption in rows)
    if canonical != rows or not hmac.compare_digest(_seal(rows), intent["payload_hash"]):
        raise _unavailable()
    return canonical


def _result(intent) -> SnapshotPublicationIntent:
    created = intent["created_at"]
    if created.tzinfo is None or created.year in (1, 9999):
        raise _unavailable()
    return SnapshotPublicationIntent(
        id=intent["id"], config_revision=intent["config_revision"], row_count=intent["row_count"],
        expected_previous_generation=intent["expected_previous_generation"], created_at=created,
    )


def _grant_for(actor_id, expected_config_revision):
    if (not isinstance(actor_id, UUID) or not isinstance(expected_config_revision, UUID)
        or settings.ENVIRONMENT != "local" or "ENVIRONMENT" not in settings.model_fields_set
        or not settings.TABLE_EXPORT_SNAPSHOT_INTENT_ENABLED or not settings.TABLE_EXPORT_SANDBOX_ENABLED):
        raise _unavailable()
    grant = settings.TABLE_EXPORT_SNAPSHOT_INTENT_GRANTS.get(actor_id)
    profile = configured_snapshot_publication_profile()
    if (grant is None or profile is None or grant.sourceMarker != settings.TABLE_EXPORT_SOURCE_MARKER
        or not grant.sourceMarker.int or not grant.privateMarker.int
        or len({grant.sourceMarker, grant.privateMarker, profile.marker}) != 3
        or not grant.configRevision == profile.config_revision == expected_config_revision):
        raise _unavailable()
    return grant, profile


async def freeze_snapshot_publication_intent(
    session: AsyncSession, *, actor_id: UUID, request_id: UUID, expected_previous_generation: int,
    expected_config_revision: UUID,
) -> SnapshotPublicationIntent:
    """Caller supplies authenticated actor identity and a fresh owned sender Session."""
    if (not isinstance(request_id, UUID) or request_id.version != 4
        or type(expected_previous_generation) is not int or not 0 <= expected_previous_generation < _MAX_INTENTS):
        raise _unavailable()
    grant, profile = _grant_for(actor_id, expected_config_revision)
    try:
        sender = _sender_scope(session)
        approved = make_url(str(settings.SQLALCHEMY_RUNNABLE_DATABASE_URI))
        if (approved.drivername != "postgresql+asyncpg" or approved.query
            or (str(ip_address(approved.host)), approved.port or 5432, approved.database, approved.username)
               != (sender.address, sender.port, sender.database, sender.username)):
            raise _unavailable()
        scope_digest = _scope_digest(actor_id, grant, sender)
        async with asyncio.timeout(8), session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            await session.execute(text("SET LOCAL search_path=pg_catalog,pg_temp"))
            await session.execute(text("SET LOCAL lock_timeout='400ms'"))
            await session.execute(text("SET LOCAL statement_timeout='1200ms'"))
            # Acquire the serial private lock before the first snapshot-bearing SELECT.
            await _lock_scope(session, _PRIVATE, _PRIVATE_RELATIONS)
            await _check_sender(session, sender)
            proof = await _prove_locked_scope(session, _PRIVATE, _PRIVATE_RELATIONS)
            if not hmac.compare_digest(proof, grant.privateProofSha256):
                raise _unavailable()
            markers = (await session.scalars(text(f"""
                SELECT marker FROM ONLY {_PRIVATE}.identity WHERE singleton IS TRUE LIMIT 2
            """))).all()
            if markers != [grant.privateMarker]:
                raise _unavailable()
            prior = (await session.execute(text(f"""
                SELECT id,actor_id,config_revision,expected_previous_generation,scope_digest,payload_hash,row_count,created_at
                FROM ONLY {_PRIVATE}.intents WHERE request_id=:request
            """), {"request": request_id})).mappings().one_or_none()
            if prior is not None:
                if (prior["actor_id"] != actor_id or prior["config_revision"] != expected_config_revision
                    or prior["expected_previous_generation"] != expected_previous_generation
                    or not hmac.compare_digest(prior["scope_digest"], scope_digest)):
                    raise _unavailable()
                await _verify_frozen(session, prior)
                if _grant_for(actor_id, expected_config_revision) != (grant, profile):
                    raise _unavailable()
                return _result(prior)

            intents = (await session.scalars(text(f"SELECT 1 FROM ONLY {_PRIVATE}.intents LIMIT 16"))).all()
            if len(intents) >= _MAX_INTENTS:
                raise _unavailable()
            if not hmac.compare_digest(await compute_snapshot_intent_source_proof(session), grant.sourceProofSha256):
                raise _unavailable()
            await require_export_sender_marker(session, grant.sourceMarker)
            head = await read_snapshot_publication_head(
                expected_config_revision=expected_config_revision, sender_port=sender.port,
                sender_database=sender.database, sender_username=sender.username, sender_marker=grant.sourceMarker,
            )
            if head.generation != expected_previous_generation or _grant_for(actor_id, expected_config_revision) != (grant, profile):
                raise _unavailable()
            rows = _normalize(SnapshotPublicationRow(id=str(key), caption=value) for key, value in
                              await _read_source(session, grant.sourceMarker, snapshot_preview=True))
            if _grant_for(actor_id, expected_config_revision) != (grant, profile):
                raise _unavailable()
            identity = uuid4()
            intent = (await session.execute(text(f"""
                INSERT INTO {_PRIVATE}.intents
                    (id,actor_id,request_id,config_revision,expected_previous_generation,scope_digest,payload_hash,row_count,created_at)
                VALUES (:id,:actor,:request,:revision,:previous,:scope,:digest,:count,pg_catalog.clock_timestamp())
                RETURNING id,config_revision,expected_previous_generation,row_count,created_at
            """), {"id": identity, "actor": actor_id, "request": request_id, "revision": expected_config_revision,
                   "previous": expected_previous_generation, "scope": scope_digest, "digest": _seal(rows),
                   "count": len(rows)})).mappings().one()
            await session.execute(text(f"""
                INSERT INTO {_PRIVATE}.records (intent_id,ordinal,id,caption)
                VALUES (:intent,:ordinal,:id,:caption)
            """), [{"intent": identity, "ordinal": ordinal, "id": key, "caption": value}
                    for ordinal, (key, value) in enumerate(rows, 1)])
            return _result(intent)
    except Exception:
        raise _unavailable() from None
