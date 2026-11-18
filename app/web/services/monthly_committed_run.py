"""Inspect an exact Monthly receipt, never schedule work or read table rows."""

import re
from uuid import UUID

from anyio import fail_after
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.monthly_committed_run import MonthlyCommittedRun
from app.web.services.etl_layers import _LINEAGE_METADATA_JOIN, _LINEAGE_SCOPE, _LINEAGE_VISIBLE_SQL
from app.web.services.transfer_runs import SANDBOX, SCHEDULED_SYSTEM_ACTOR, clock_slot_job_id


class MonthlyCommittedRunUnavailable(Exception):
    """The exact recorded Monthly run could not be made visible."""


_PROOF = text("""
    SELECT j.id AS job_id,j.plan_id,j.generation,j.slot,j.schedule_day_of_month_utc AS day,
           r.id AS receipt_id,r.source_table,r.target_table,r.recorded_at,r.rows_affected,
           r.target_oid,r.target_filenode
    FROM ONLY public.ontology_transfer_schedule_jobs j
    JOIN ONLY public.ontology_transfer_runs r ON r.id=j.receipt_id
       AND r.request_id=j.id AND r.plan_id=j.plan_id AND r.plan_updated_at=j.plan_updated_at
    WHERE j.id=:job AND j.plan_id=:plan AND j.state='committed'
      AND j.generation>0 AND j.canceled_at IS NULL AND j.canceled_by IS NULL
      AND j.schedule_weekday_utc IS NULL AND j.schedule_day_of_month_utc BETWEEN 1 AND 28
      AND j.schedule_day_of_month_utc=EXTRACT(DAY FROM j.slot AT TIME ZONE 'UTC')
      AND j.slot=pg_catalog.date_trunc('minute',j.slot)
      AND pg_catalog.isfinite(j.slot) AND j.expires_at=j.slot+interval '5 minutes'
      AND pg_catalog.isfinite(j.plan_updated_at) AND j.plan_updated_at<=j.slot
      AND pg_catalog.isfinite(j.finished_at) AND j.finished_at>=j.slot
      AND j.finished_at<=pg_catalog.statement_timestamp()
      AND r.initiated_by=:system_actor AND r.source_schema='ts_transfer_sandbox' AND r.target_schema='ts_transfer_sandbox'
      AND r.source_table<>r.target_table AND r.write_mode='upsert' AND r.cursor_through IS NULL AND r.has_more IS NULL
      AND r.scheduled_source_oid IS NULL AND r.scheduled_source_filenode IS NULL
      AND r.rows_affected BETWEEN 0 AND 100 AND r.target_oid>0 AND r.target_filenode>0
      AND pg_catalog.isfinite(r.recorded_at) AND r.recorded_at>=j.slot AND r.recorded_at<=j.finished_at
      AND (j.dispatched_at IS NULL OR (j.dispatched_at>=j.slot AND j.dispatched_at<=r.recorded_at))
      AND NOT EXISTS (
          SELECT 1 FROM ONLY public.ontology_transfer_upstream_jobs u WHERE u.id=j.id AND u.plan_id=j.plan_id
      )
      AND (
          SELECT count(*)=1 AND bool_and(valid)
          FROM (
              SELECT a.schedule_job_id=j.id AND a.upstream_job_id IS NULL AND a.receipt_id=r.id
                AND a.plan_updated_at=r.plan_updated_at AND a.scope_trigger='month_utc'
                AND a.source_table=r.source_table AND a.target_table=r.target_table
                AND a.target_oid=r.target_oid AND a.target_filenode=r.target_filenode
                AND pg_catalog.isfinite(a.started_at) AND pg_catalog.isfinite(a.finished_at)
                AND a.started_at>=j.slot AND a.started_at<j.expires_at AND a.started_at<=r.recorded_at
                AND a.finished_at>=r.recorded_at AND a.finished_at<=j.finished_at AS valid
              FROM ONLY public.ontology_transfer_attempts a
              WHERE a.plan_id=j.plan_id AND a.request_id=j.id AND a.status='committed'
              LIMIT 2
          ) candidates
      )
""")

_ENDPOINTS = text(f"""
    SELECT c.relname::text AS name,c.oid::bigint AS oid,pg_catalog.pg_relation_filenode(c.oid)::bigint AS filenode
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_catalog.pg_am heap ON heap.oid=c.relam AND heap.amname='heap' AND heap.amtype='t'
    {_LINEAGE_METADATA_JOIN}
    WHERE n.nspname=:schema AND c.relname::text=ANY(CAST(:tables AS text[])) AND {_LINEAGE_VISIBLE_SQL}
      AND c.relkind='r' AND c.relpersistence='p' AND NOT (c.relrowsecurity OR c.relforcerowsecurity OR c.relhasrules)
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhparent=c.oid OR i.inhrelid=c.oid)
      AND pg_catalog.has_schema_privilege(n.oid,'USAGE') AND pg_catalog.has_table_privilege(c.oid,'SELECT')
""")
_HELD = text("""
    SELECT relation::bigint FROM pg_catalog.pg_locks
    WHERE pid=pg_catalog.pg_backend_pid() AND locktype='relation' AND granted AND mode='AccessShareLock'
      AND relation::bigint=ANY(CAST(:oids AS bigint[]))
""")


async def _read_parameters(session: AsyncSession):
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    await session.execute(text("SET LOCAL search_path=pg_catalog,pg_temp"))
    await session.execute(text("SET LOCAL statement_timeout='850ms'"))
    await session.execute(text("SET LOCAL lock_timeout='300ms'"))
    await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))


async def _proof(session: AsyncSession, plan: UUID, job: UUID):
    result = (await session.execute(_PROOF, {"plan": plan, "job": job, "system_actor": SCHEDULED_SYSTEM_ACTOR})).one_or_none()
    if (result is None or clock_slot_job_id(plan, result.generation, result.slot) != job
        or any(re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name, re.ASCII) is None
               for name in (result.source_table, result.target_table))):
        raise MonthlyCommittedRunUnavailable()
    return result


async def _endpoints(session: AsyncSession, witness):
    tables = [witness.source_table, witness.target_table]
    found = (await session.execute(_ENDPOINTS, {**_LINEAGE_SCOPE, "schema": SANDBOX, "tables": tables})).all()
    names = {row.name: (row.oid, row.filenode) for row in found}
    if (len(found) != 2 or set(names) != set(tables)
        or names[witness.target_table] != (witness.target_oid, witness.target_filenode)):
        raise MonthlyCommittedRunUnavailable()
    return names


async def read_monthly_committed_run(session: AsyncSession, plan: UUID, job: UUID) -> MonthlyCommittedRun:
    try:
        with fail_after(3), session.no_autoflush:
            # Resolve exact authorized names before using them in utility SQL.
            async with session.begin():
                await _read_parameters(session)
                first = await _proof(session, plan, job)
                await _endpoints(session, first)
            async with session.begin():
                await _read_parameters(session)
                # The fresh final snapshot starts *after* acquiring the locks,
                # so persisted RLS/ACL changes cannot be hidden by preflight.
                tables = sorted((first.source_table, first.target_table))
                references = ", ".join(f'ONLY "{SANDBOX}"."{table}"' for table in tables)
                await session.execute(text(f"LOCK TABLE {references} IN ACCESS SHARE MODE NOWAIT"))
                witness = await _proof(session, plan, job)
                if witness != first:
                    raise MonthlyCommittedRunUnavailable()
                pins = await _endpoints(session, witness)
                expected_oids = {item[0] for item in pins.values()}
                held = set((await session.scalars(_HELD, {"oids": list(expected_oids)})).all())
                if held != expected_oids:
                    raise MonthlyCommittedRunUnavailable()
                return MonthlyCommittedRun(planId=plan, jobId=job, receiptId=witness.receipt_id,
                                           sourceTable=witness.source_table, targetTable=witness.target_table,
                                           slot=witness.slot, scheduleDayOfMonthUtc=witness.day,
                                           recordedAt=witness.recorded_at, rowsAffected=witness.rows_affected)
    except (DBAPIError, TimeoutError):
        raise MonthlyCommittedRunUnavailable() from None
