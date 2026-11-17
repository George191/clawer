"""Current physical evidence of completed manual sandbox transfers, not declared edges."""

from uuid import UUID

from anyio import fail_after
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.lineage_neighbors import LineageDirection, ObservedLineage, ObservedLineageNeighbor
from app.web.services.etl_layers import (
    _LINEAGE_METADATA_JOIN, _LINEAGE_SCOPE, _LINEAGE_VISIBLE_SQL, quote_identifier,
)
from app.web.services.transfer_runs import (
    SANDBOX, SCHEDULED_SYSTEM_ACTOR, UPSTREAM_SYSTEM_ACTOR, observed_lineage_enabled,
)

_LIMIT = 12
_PHYSICAL = f"""
    SELECT c.relname::text AS name, c.oid::bigint AS oid, pg_catalog.pg_relation_filenode(c.oid)::bigint AS node
    FROM pg_catalog.pg_namespace n
    JOIN pg_catalog.pg_class c ON c.relnamespace = n.oid
    JOIN pg_catalog.pg_am am ON am.oid = c.relam
    {_LINEAGE_METADATA_JOIN}
    WHERE n.nspname = 'ts_transfer_sandbox' AND {_LINEAGE_VISIBLE_SQL}
      AND c.relkind = 'r' AND c.relpersistence = 'p' AND am.amname = 'heap'
      AND NOT c.relrowsecurity AND NOT c.relhasrules
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits h WHERE c.oid IN (h.inhrelid, h.inhparent))
"""
_ROOT = text(_PHYSICAL + " AND c.relname = :table")
_PIN = text(_PHYSICAL + """
    AND c.relname::text = ANY(CAST(:tables AS text[]))
    AND EXISTS (
        SELECT 1 FROM pg_catalog.pg_locks l
        WHERE l.pid=pg_catalog.pg_backend_pid() AND l.locktype='relation' AND l.relation=c.oid
          AND l.mode='AccessShareLock' AND l.granted
    )
""")


async def visible_observed_table_identity(session: AsyncSession, schema: str, table: str) -> tuple[int, int] | None:
    """Gate a protected receipt link with the same exact current catalog policy."""
    if schema != SANDBOX:
        return None
    root = (await session.execute(_ROOT, {**_LINEAGE_SCOPE, "table": table})).one_or_none()
    if root is None or root.node is None:
        return None
    await session.execute(text(f'LOCK TABLE ONLY ts_transfer_sandbox.{quote_identifier(table)} IN ACCESS SHARE MODE NOWAIT'))
    pinned = (await session.execute(_PIN, {**_LINEAGE_SCOPE, "tables": [table]})).one_or_none()
    if pinned is None or (pinned.oid, pinned.node) != (root.oid, root.node):
        return None
    return pinned.oid, pinned.node


_POSITIVE_MANUAL_PROOF = """
    r.source_schema = 'ts_transfer_sandbox' AND r.target_schema = 'ts_transfer_sandbox'
    AND r.source_table <> r.target_table
    AND r.rows_affected BETWEEN 1 AND 100 AND r.write_mode = 'upsert'
    AND r.cursor_through IS NULL AND r.has_more IS NULL
    AND r.initiated_by NOT IN (:daily_actor, :upstream_actor)
    AND r.recorded_at <= pg_catalog.statement_timestamp() AND r.plan_updated_at <= r.recorded_at
    AND r.target_oid = e.target_oid AND r.target_filenode = e.target_node
    AND e.parent_plan_id = r.plan_id AND e.parent_updated_at = r.plan_updated_at AND e.recorded_at = r.recorded_at
    AND e.source_oid > 0 AND e.source_node > 0 AND e.target_oid > 0 AND e.target_node > 0
    AND EXISTS (
        SELECT 1 FROM public.ontology_transfer_attempts a
        WHERE a.receipt_id = r.id AND a.receipt_id IS NOT NULL
          AND a.scope_trigger = 'manual' AND a.status = 'committed'
          AND a.schedule_job_id IS NULL AND a.upstream_job_id IS NULL
          AND a.plan_id = r.plan_id AND a.request_id = r.request_id AND a.plan_updated_at = r.plan_updated_at
          AND a.source_table = r.source_table AND a.target_table = r.target_table
          AND a.target_oid = r.target_oid AND a.target_filenode = r.target_filenode
          AND a.started_at <= r.recorded_at AND a.finished_at >= r.recorded_at
    )
"""
_PROOF_ACTORS = {"daily_actor": SCHEDULED_SYSTEM_ACTOR, "upstream_actor": UPSTREAM_SYSTEM_ACTOR}
_LINK_PROOF = text(f"""
    SELECT e.source_oid, e.source_node, e.target_oid, e.target_node
    FROM public.ontology_transfer_runs r
    JOIN public.ontology_transfer_commit_events e ON e.parent_receipt_id=r.id
    WHERE r.id=:receipt_id AND r.target_table=:table AND {_POSITIVE_MANUAL_PROOF}
""")


async def observed_receipt_physical_proof(session: AsyncSession, table: str, receipt_id: UUID):
    """An existing capture remains historical proof independent of current plan/date window."""
    return (await session.execute(_LINK_PROOF, {**_PROOF_ACTORS, "receipt_id": receipt_id, "table": table})).one_or_none()


def _receipt_query(direction: LineageDirection):
    current, remote = ("source", "target") if direction == "outgoing" else ("target", "source")
    return text(f"""
        WITH physical AS MATERIALIZED ({_PHYSICAL}), by_peer AS (
            SELECT DISTINCT ON (peer.name COLLATE "C")
                peer.name, peer.oid, peer.node, r.id, r.recorded_at, r.rows_affected
            FROM public.ontology_transfer_runs r
            JOIN public.ontology_transfer_commit_events e ON e.parent_receipt_id = r.id
            JOIN public.ontology_transfer_plans p ON p.id = r.plan_id
            JOIN physical src ON src.name = r.source_table AND src.oid = e.source_oid AND src.node = e.source_node
            JOIN physical dst ON dst.name = r.target_table AND dst.oid = e.target_oid AND dst.node = e.target_node
            JOIN physical peer ON peer.name = r.{remote}_table
            WHERE r.{current}_schema = 'ts_transfer_sandbox' AND r.{current}_table = :table
              AND r.recorded_at > pg_catalog.statement_timestamp() - interval '7 days'
              AND {_POSITIVE_MANUAL_PROOF}
              AND p.updated_at = r.plan_updated_at
              AND p.source_schema = r.source_schema AND p.source_table = r.source_table
              AND p.target_schema = r.target_schema AND p.target_table = r.target_table
              AND p.trigger = 'manual' AND p.write_mode = 'upsert' AND p.incremental_cursor IS NULL
            ORDER BY peer.name COLLATE "C", r.recorded_at DESC, r.id DESC
        )
        SELECT name, oid, node, id, recorded_at, rows_affected FROM by_peer
        ORDER BY recorded_at DESC, name COLLATE "C", id DESC
        LIMIT :limit
    """)


async def observed_transfer_lineage(
    session: AsyncSession, schema: str, table: str, direction: LineageDirection,
) -> ObservedLineage:
    if schema != SANDBOX:
        raise HTTPException(404, "Table does not exist in this schema")
    quote_identifier(table)
    if not observed_lineage_enabled():
        return ObservedLineage(available=False, direction=direction, neighbors=[], truncated=False)
    try:
        with fail_after(3):
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            await session.execute(text("SET LOCAL statement_timeout='750ms'"))
            root = (await session.execute(_ROOT, {**_LINEAGE_SCOPE, "table": table})).one_or_none()
            if root is None or root.node is None:
                raise HTTPException(404, "Table does not exist in this schema")
            rows = (await session.execute(_receipt_query(direction), {
                **_LINEAGE_SCOPE, **_PROOF_ACTORS, "table": table, "limit": _LIMIT + 1,
            })).all()
            # Resolve every returned identity under an exact no-wait read fence.
            # The sentinel affects truncation but is never returned as evidence.
            identities = {row.name: (row.oid, row.node) for row in rows[:_LIMIT]}
            identities[table] = root.oid, root.node
            qualified = ", ".join(f"ONLY ts_transfer_sandbox.{quote_identifier(name)}" for name in sorted(identities))
            await session.execute(text(f"LOCK TABLE {qualified} IN ACCESS SHARE MODE NOWAIT"))
            pinned = (await session.execute(_PIN, {**_LINEAGE_SCOPE, "tables": list(identities)})).all()
            if {row.name: (row.oid, row.node) for row in pinned} != identities:
                raise HTTPException(503, "Current observed transfer evidence is temporarily unavailable")
    except (TimeoutError, DBAPIError):
        raise HTTPException(503, "Current observed transfer evidence is temporarily unavailable") from None
    return ObservedLineage(
        available=True, direction=direction, truncated=len(rows) > _LIMIT,
        neighbors=[ObservedLineageNeighbor(schema=SANDBOX, table=row.name, receiptId=row.id,
                                           receiptRecordedAt=row.recorded_at, rowsAffected=row.rows_affected)
                   for row in rows[:_LIMIT]],
    )
