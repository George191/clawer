from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.etl.normalizers.base import safe_datetime
from app.storage.etl_metadata_store import ensure_ddlregistry_table, save_registered_ddl
from app.storage.postgres_client import get_pg_client

_ODS_TABLES = ("news", "patent", "navwarn", "intelligence")
_TABLE_ROLES = ("current", "history")
_EXTRA_DATA_COLUMN_RE = re.compile(
    r"^\s*extra_data\s+JSONB,\s*\r?\n",
    re.IGNORECASE | re.MULTILINE,
)


def _strip_extra_data_column(ddl_sql: str) -> str:
    return _EXTRA_DATA_COLUMN_RE.sub("", ddl_sql)


async def _update_registry() -> int:
    pg = get_pg_client()
    rows = await pg.fetch_all(
        """
        SELECT
            layer,
            table_role,
            table_name,
            ddl_sql,
            partition_type,
            partition_column,
            partition_granularity,
            partition_count,
            description
        FROM ts_meta.ddlregistry
        WHERE is_active = TRUE
          AND layer = 'ods'
          AND table_role IN ('current', 'history')
          AND table_name IN ('ods_news', 'ods_patent', 'ods_navwarn', 'ods_intelligence')
        ORDER BY table_role, table_name
        """
    )

    changed = 0
    for row in rows:
        ddl_sql = str(row["ddl_sql"])
        rewritten = _strip_extra_data_column(ddl_sql)
        if rewritten == ddl_sql:
            continue
        logical_table = str(row["table_name"]).removeprefix("ods_")
        await save_registered_ddl(
            pg,
            layer="ods",
            table=logical_table,
            ddl_sql=rewritten,
            table_role=str(row["table_role"]),
            partition_type=str(row["partition_type"]),
            partition_column=row.get("partition_column"),
            partition_granularity=row.get("partition_granularity"),
            partition_count=row.get("partition_count"),
            description=row.get("description"),
            updated_by="system:migrate-ods-drop-extra-data",
        )
        changed += 1
    return changed


async def _drop_extra_data_columns() -> int:
    pg = get_pg_client()
    changed = 0
    for schema_name in ("ts_ods", "ts_ods_hist"):
        for logical_table in _ODS_TABLES:
            table_name = f"{schema_name}.ods_{logical_table}"
            await pg.execute(
                f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS extra_data"
            )
            changed += 1
    return changed


async def _backfill_planet_source_updated_at() -> int:
    pg = get_pg_client()
    rows = await pg.fetch_all(
        """
        SELECT
            o.record_id,
            r.raw_data ->> 'modified' AS modified
        FROM ts_ods.ods_intelligence AS o
        JOIN ts_rds.rds_intelligence AS r
          ON r.record_id = o.record_id
        WHERE o.data_source = 'planet'
          AND o.source_updated_at IS NULL
          AND COALESCE(r.raw_data ->> 'modified', '') <> ''
        """
    )

    updates: list[dict[str, object]] = []
    for row in rows:
        source_updated_at = safe_datetime(row.get("modified"))
        if source_updated_at is None:
            continue
        updates.append(
            {
                "record_id": row["record_id"],
                "source_updated_at": source_updated_at,
            }
        )

    if updates:
        await pg.execute_many(
            """
            UPDATE ts_ods.ods_intelligence
            SET source_updated_at = CAST(:source_updated_at AS timestamptz),
                updated_at = NOW()
            WHERE record_id = :record_id
            """,
            updates,
        )

    return len(updates)


async def main() -> None:
    pg = get_pg_client()
    await pg.connect()
    try:
        await ensure_ddlregistry_table(pg)
        registry_changed = await _update_registry()
        dropped = await _drop_extra_data_columns()
        backfilled = await _backfill_planet_source_updated_at()
        print(
            f"[done] registry_changed={registry_changed} "
            f"dropped_tables={dropped} planet_source_updated_at_backfilled={backfilled}"
        )
    finally:
        await pg.close()


if __name__ == "__main__":
    asyncio.run(main())
