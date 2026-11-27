"""Exact, read-only evidence for one known synthetic acceptance identifier."""

from datetime import datetime, timezone
from ipaddress import ip_address
import json
from uuid import UUID

from anyio import fail_after
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.push_inbox import TOTAL_LIMIT, PushInboxEventPath
from app.web.api.models.push_inbox_apply import MAX_PUSH_APPLY_EVENTS
from app.web.core.config import settings
from app.web.services import push_inbox
from app.web.services.push_inbox_apply import _SOURCE, _TARGET, _binding, _limits, _pin, _same_binding

_STATE = '"public"."helio_push_inbox_state"'
_BINDING = '"public"."helio_push_inbox_apply_binding"'
_RESULTS = '"public"."helio_push_inbox_apply_receipts"'
_DATABASE = text("""
    SELECT pg_catalog.host(pg_catalog.inet_server_addr()) = :host
       AND pg_catalog.inet_server_port() = :port
       AND pg_catalog.current_database() = :database
       AND session_user::text = :username AND current_user::text = :username
""")
_PRIVATE_PINS = text("""
    SELECT c.relname::text, c.oid::bigint, pg_catalog.pg_relation_filenode(c.oid)::bigint
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
    JOIN pg_catalog.pg_am am ON am.oid = c.relam AND am.amname = 'heap'
    WHERE c.relname IN ('helio_push_inbox_state', 'helio_push_inbox_apply_binding',
                       'helio_push_inbox_apply_receipts')
      AND c.relkind = 'r' AND c.relpersistence = 'p' AND NOT c.relispartition
      AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity AND NOT c.relhasrules
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE c.oid IN (i.inhrelid,i.inhparent))
    ORDER BY c.relname
""")
_STATE_HEAD = text(f"SELECT database_marker, accepted_count FROM ONLY {_STATE} WHERE singleton IS TRUE LIMIT 2")
_ACCEPTANCE_INDEX = text("""
    SELECT ic.relname::text
    FROM pg_catalog.pg_index i
    JOIN pg_catalog.pg_class ic ON ic.oid=i.indexrelid
    JOIN pg_catalog.pg_am am ON am.oid=ic.relam AND am.amname='btree'
    JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attname='id'
    JOIN pg_catalog.pg_opclass op ON op.oid=i.indclass[0]
    WHERE i.indrelid=CAST(:oid AS oid) AND i.indisprimary AND i.indisvalid AND i.indisready AND i.indislive
      AND i.indisunique AND i.indimmediate AND i.indnatts=1 AND i.indkey[0]=a.attnum
      AND i.indexprs IS NULL AND i.indpred IS NULL
      AND op.opcname='uuid_ops' AND op.opcnamespace='pg_catalog'::regnamespace
""")
_APPLY_INDEX = text("""
    SELECT EXISTS (
        SELECT 1 FROM pg_catalog.pg_index i
        JOIN pg_catalog.pg_class ic ON ic.oid=i.indexrelid AND ic.relname='uq_push_apply_ordinal'
        JOIN pg_catalog.pg_am am ON am.oid=ic.relam AND am.amname='btree'
        JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attname='through_inclusive'
        JOIN pg_catalog.pg_opclass op ON op.oid=i.indclass[0]
        WHERE i.indrelid=CAST(:oid AS oid) AND i.indisvalid AND i.indisready AND i.indislive
          AND i.indisunique AND i.indimmediate AND i.indnatts=1 AND i.indkey[0]=a.attnum
          AND i.indexprs IS NULL AND i.indoption[0] & 1 = 0
          AND pg_catalog.pg_get_expr(i.indpred,i.indrelid) = '((outcome)::text = ''applied''::text)'
          AND op.opcname='int8_ops' AND op.opcnamespace='pg_catalog'::regnamespace
          AND EXISTS (SELECT 1 FROM pg_catalog.pg_constraint k
                      WHERE k.conrelid=i.indrelid AND k.conname='ck_push_apply_result'
                        AND k.contype='c' AND k.convalidated AND NOT k.connoinherit)
    )
""")
_ACCEPTANCE = f"""
    SELECT id, ordinal, accepted_at, pg_catalog.isfinite(accepted_at) AS finite FROM ONLY {_SOURCE} WHERE id = :id LIMIT 2
"""
_TARGET_MATCH = f"""
    SELECT acceptance_id FROM ONLY {_TARGET} WHERE ordinal = :ordinal LIMIT 2
"""
_INTERVALS = f"""
    SELECT id, database_marker, generation, outcome, from_exclusive, through_inclusive,
           reviewed_accepted_through, events_applied, recorded_at, pg_catalog.isfinite(recorded_at) AS finite
    FROM ONLY {_RESULTS}
    WHERE outcome = 'applied' AND through_inclusive >= :ordinal AND from_exclusive < :ordinal
    ORDER BY through_inclusive LIMIT 2
"""
_SOURCE_CERT = f"""
    SELECT count(*) AS count, min(ordinal) AS first, max(ordinal) AS last
    FROM (SELECT ordinal FROM ONLY {_SOURCE} ORDER BY ordinal LIMIT {TOTAL_LIMIT + 1}) AS observed
"""
_TARGET_CERT = f"""
    SELECT count(*) AS count, min(ordinal) AS first, max(ordinal) AS last,
           COALESCE(bool_or(acceptance_id = :id AND ordinal <> :ordinal), false) AS misplaced
    FROM (SELECT ordinal, acceptance_id FROM ONLY {_TARGET} ORDER BY ordinal LIMIT {TOTAL_LIMIT + 1}) AS observed
"""


class PushInboxEventPathUnavailable(Exception):
    """No safe evidence can be emitted for this request."""


async def _index_read(
    session: AsyncSession, sql: str, params: dict, *, relation: str, index: str,
    certificate: bool = False, ranged: bool = False, interval: bool = False,
):
    raw = await session.scalar(text("EXPLAIN (ANALYZE FALSE, FORMAT JSON, VERBOSE TRUE) " + sql), params)
    root = (json.loads(raw) if isinstance(raw, str) else raw)[0]["Plan"]
    if certificate:
        if root.get("Node Type") != "Aggregate" or len(root.get("Plans", [])) != 1:
            raise PushInboxEventPathUnavailable
        root = root["Plans"][0]
    if root.get("Node Type") != "Limit" or len(root.get("Plans", [])) != 1:
        raise PushInboxEventPathUnavailable
    leaf = root["Plans"][0]
    schema, name = relation.replace('"', "").split(".")
    if (leaf.get("Node Type") not in ("Index Scan", "Index Only Scan")
            or leaf.get("Schema") != schema or leaf.get("Relation Name") != name
            or leaf.get("Index Name") != index or leaf.get("Scan Direction") != "Forward"
            or leaf.get("Parallel Aware") or leaf.get("Plans") or leaf.get("Recheck Cond")
            or bool(leaf.get("Index Cond")) != ranged or bool(leaf.get("Filter")) != interval):
        raise PushInboxEventPathUnavailable
    return await session.execute(text(sql), params)


def _certified(row, head: int) -> bool:
    return row[:3] == (head, 1 if head else None, head if head else None)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


async def _resolve(session: AsyncSession, receipt: UUID) -> PushInboxEventPath:
    url = push_inbox.local_inbox_database()
    marker = settings.PUSH_INBOX_SANDBOX_DATABASE_MARKER
    if not push_inbox.event_path_opted_in() or url is None or marker is None or not url.username:
        raise PushInboxEventPathUnavailable
    database = {"host": str(ip_address(url.host)), "port": url.port if url.port is not None else 5432,
                "database": url.database, "username": url.username}
    async with session.begin():
        await _limits(session, read_only=True)
        if not await session.scalar(_DATABASE, database):
            raise PushInboxEventPathUnavailable
        source, target = await _pin(session, sink=False), await _pin(session, sink=True)
        private = (await session.execute(_PRIVATE_PINS)).all()
        if source is None or target is None or len(private) != 3 or any(pin[2] is None for pin in private):
            raise PushInboxEventPathUnavailable
    # New snapshot after the scoped locks: catalog DDL committed between preflight
    # and acquisition cannot pass on the older transaction's metadata.
    async with session.begin():
        await _limits(session, read_only=True)
        for relation in (_STATE, _BINDING, _TARGET, _SOURCE, _RESULTS):
            await session.execute(text(f"LOCK TABLE ONLY {relation} IN ACCESS SHARE MODE NOWAIT"))
        if not await session.scalar(_DATABASE, database) or (await session.execute(_PRIVATE_PINS)).all() != private:
            raise PushInboxEventPathUnavailable
        state = (await session.execute(_STATE_HEAD)).one_or_none()
        binding = await _binding(session, lock=False)
        if (state is None or binding is None or state.database_marker != marker or binding.database_marker != marker
                or binding.generation != 1 or not 0 <= binding.applied_through <= state.accepted_count <= TOTAL_LIMIT
                or await _pin(session, sink=False) != source or await _pin(session, sink=True) != target
                or not _same_binding(binding, source, target)):
            raise PushInboxEventPathUnavailable
        await session.execute(text("SET LOCAL enable_seqscan=off"))
        await session.execute(text("SET LOCAL enable_bitmapscan=off"))
        acceptance_index = await session.scalar(_ACCEPTANCE_INDEX, {"oid": source.oid})
        result_oid = next(pin[1] for pin in private if pin[0] == "helio_push_inbox_apply_receipts")
        if not acceptance_index or not await session.scalar(_APPLY_INDEX, {"oid": result_oid}):
            raise PushInboxEventPathUnavailable
        now = await session.scalar(text("SELECT pg_catalog.statement_timestamp()"))
        acceptance = (await _index_read(session, _ACCEPTANCE, {"id": receipt}, relation=_SOURCE,
                                        index=acceptance_index, ranged=True)).one_or_none()
        if (acceptance is None or not acceptance.finite or not 1 <= acceptance.ordinal <= state.accepted_count
                or not acceptance.accepted_at <= now or not binding.reviewed_at <= now):
            raise PushInboxEventPathUnavailable
        ordinal = acceptance.ordinal
        accepted = (await _index_read(session, _SOURCE_CERT, {}, relation=_SOURCE,
                                      index=source.ordinal_index, certificate=True)).one()
        if not _certified(accepted, state.accepted_count):
            raise PushInboxEventPathUnavailable
        sink = (await _index_read(session, _TARGET_CERT, {"id": receipt, "ordinal": ordinal},
                                  relation=_TARGET, index=target.ordinal_index, certificate=True)).one()
        if not _certified(sink, binding.applied_through) or sink.misplaced:
            raise PushInboxEventPathUnavailable
        sink_match = (await _index_read(session, _TARGET_MATCH, {"ordinal": ordinal}, relation=_TARGET,
                                        index=target.ordinal_index, ranged=True)).scalar_one_or_none()
        intervals = (await _index_read(session, _INTERVALS, {"ordinal": ordinal}, relation=_RESULTS,
                                       index="uq_push_apply_ordinal", ranged=True, interval=True)).all()
        if not push_inbox.event_path_opted_in() or settings.PUSH_INBOX_SANDBOX_DATABASE_MARKER != marker or push_inbox.local_inbox_database() != url:
            raise PushInboxEventPathUnavailable
        metadata = dict(receiptId=receipt, ordinal=str(ordinal), acceptedAt=_utc(acceptance.accepted_at), observedAt=_utc(now))
        if ordinal > binding.applied_through:
            if sink_match is not None or intervals:
                raise PushInboxEventPathUnavailable
            return PushInboxEventPath(status="accepted_not_applied_at_observation", **metadata)
        if len(intervals) != 1 or sink_match != receipt:
            raise PushInboxEventPathUnavailable
        interval = intervals[0]
        if (not interval.finite or interval.database_marker != marker or interval.generation != binding.generation
                or interval.outcome != "applied"
                or not 0 <= interval.from_exclusive < ordinal <= interval.through_inclusive <= binding.applied_through
                or not interval.through_inclusive <= interval.reviewed_accepted_through <= state.accepted_count
                or not 1 <= interval.events_applied == interval.through_inclusive - interval.from_exclusive <= MAX_PUSH_APPLY_EVENTS
                or interval.through_inclusive != min(
                    interval.from_exclusive + MAX_PUSH_APPLY_EVENTS, interval.reviewed_accepted_through,
                )
                or not max(binding.reviewed_at, acceptance.accepted_at) <= interval.recorded_at <= now):
            raise PushInboxEventPathUnavailable
        return PushInboxEventPath(status="applied_recorded", applyReceiptId=interval.id,
                                 appliedAt=_utc(interval.recorded_at), **metadata)


async def read_push_event_path(session: AsyncSession, receipt: UUID) -> PushInboxEventPath:
    """Prove a bounded observation without reading payload/producer credentials."""
    try:
        with fail_after(4):
            return await _resolve(session, receipt)
    except (DBAPIError, MultipleResultsFound, OSError, TimeoutError, ValueError) as exc:
        raise PushInboxEventPathUnavailable from exc
