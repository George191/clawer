"""Read one planner null-fraction estimate from an exact eligible warehouse column."""

from math import isfinite
import re

from anyio import fail_after
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.models.column_planner_statistic import ColumnPlannerStatistic
from app.web.services.etl_layers import _LINEAGE_METADATA_JOIN, _LINEAGE_SCOPE, _LINEAGE_VISIBLE_SQL


class ColumnPlannerStatisticUnavailable(Exception):
    """Unavailable table or column; do not reveal the reason."""


_ROOT = text(f"""
    SELECT c.oid::bigint AS oid, a.attnum, pg_catalog.pg_relation_filenode(c.oid)::bigint AS filenode,
           a.atttypid::bigint AS type_oid, a.atttypmod
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    JOIN pg_catalog.pg_am heap ON heap.oid=c.relam AND heap.amname='heap' AND heap.amtype='t'
    JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attname=:column
    {_LINEAGE_METADATA_JOIN}
    WHERE n.nspname=:schema AND c.relname=:table AND {_LINEAGE_VISIBLE_SQL}
      AND c.relkind='r' AND c.relpersistence='p'
      AND NOT (c.relrowsecurity OR c.relforcerowsecurity OR c.relhasrules)
      AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhparent=c.oid OR i.inhrelid=c.oid)
      AND a.attnum>0 AND NOT a.attisdropped
      AND pg_catalog.has_schema_privilege(n.oid,'USAGE')
      AND pg_catalog.has_table_privilege(c.oid,'SELECT')
      AND pg_catalog.has_column_privilege(c.oid,a.attnum,'SELECT')
""")
_HELD = text("""
    SELECT EXISTS (
        SELECT 1 FROM pg_catalog.pg_locks l
        WHERE l.pid=pg_catalog.pg_backend_pid() AND l.locktype='relation'
          AND l.mode='AccessShareLock' AND l.granted AND l.relation=CAST(:oid AS oid)
    )
""")
_SAMPLE = text("""
    SELECT null_frac, pg_catalog.statement_timestamp() AS observed_at
    FROM pg_catalog.pg_stats
    WHERE schemaname=:schema AND tablename=:table AND attname=:column AND inherited IS FALSE
    LIMIT 2
""")


async def read_column_planner_statistic(
    session: AsyncSession, schema: str, table: str, column: str,
) -> ColumnPlannerStatistic:
    # Both route and service validate canonical identifiers before SQL interpolation.
    if not _canonical(schema, table, column):
        raise ColumnPlannerStatisticUnavailable()
    params = {**_LINEAGE_SCOPE, "schema": schema, "table": table, "column": column}
    try:
        with fail_after(2.5):
            # Privacy preflight owns its transaction so it cannot set the
            # catalog snapshot used after obtaining the exact relation lock.
            async with session.begin():
                await _read_options(session)
                original = (await session.execute(_ROOT, params)).one_or_none()
                if original is None or original.filenode is None:
                    raise ColumnPlannerStatisticUnavailable()
            async with session.begin():
                await _read_options(session)
                # LOCK is the first snapshot-independent operation after SET.
                # A fresh RR snapshot now sees changes to RLS/ACL/visibility
                # committed after preflight, even when the OID did not change.
                await session.execute(text(f'LOCK TABLE ONLY "{schema}"."{table}" IN ACCESS SHARE MODE NOWAIT'))
                locked = (await session.execute(_ROOT, params)).one_or_none()
                if locked != original or await session.scalar(_HELD, {"oid": original.oid}) is not True:
                    raise ColumnPlannerStatisticUnavailable()
                sample = (await session.execute(_SAMPLE, params)).all()
    except (DBAPIError, TimeoutError):
        raise ColumnPlannerStatisticUnavailable() from None
    if len(sample) == 1:
        ratio, seen = sample[0]
        if ratio is not None and isfinite(ratio) and 0 <= ratio <= 1:
            return ColumnPlannerStatistic(status="observed", nullFraction=ratio, observedAt=seen)
    return ColumnPlannerStatistic(status="unavailable")


async def _read_options(session: AsyncSession) -> None:
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    await session.execute(text("SET LOCAL search_path=pg_catalog,pg_temp"))
    await session.execute(text("SET LOCAL statement_timeout='750ms'"))
    await session.execute(text("SET LOCAL lock_timeout='300ms'"))
    await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))


def _canonical(schema: str, table: str, column: str) -> bool:
    return (len(schema) <= 63 and re.fullmatch(r"ts_[a-z0-9_]+", schema, re.ASCII) is not None
            and all(re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name, re.ASCII) is not None for name in (table, column)))
