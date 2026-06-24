from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.storage.etl_metadata_store import ensure_ddlregistry_table, save_registered_ddl
from app.storage.postgres_client import get_pg_client

_TABLES = {
    ("ods", "news"): {
        "drop": ["primary_attachment"],
        "rename": {},
    },
    ("ods", "patent"): {
        "drop": [],
        "rename": {"original_file": "url"},
    },
    ("ods", "intelligence"): {
        "drop": ["original_file"],
        "rename": {},
    },
}


def _drop_column_line(ddl_sql: str, column_name: str) -> str:
    pattern = re.compile(
        rf"^\s*{re.escape(column_name)}\s+[A-Z ]+(?:\([^)]*\))?,\s*\r?\n",
        re.IGNORECASE | re.MULTILINE,
    )
    return pattern.sub("", ddl_sql)


def _rename_column_line(ddl_sql: str, old_name: str, new_name: str) -> str:
    pattern = re.compile(
        rf"(^\s*){re.escape(old_name)}(\s+[A-Z ]+(?:\([^)]*\))?,\s*$)",
        re.IGNORECASE | re.MULTILINE,
    )
    return pattern.sub(rf"\1{new_name}\2", ddl_sql)


def _rewrite_ddl(ddl_sql: str, *, drop: list[str], rename: dict[str, str]) -> str:
    rewritten = ddl_sql
    for old_name, new_name in rename.items():
        rewritten = _rename_column_line(rewritten, old_name, new_name)
        rewritten = rewritten.replace(f"({old_name})", f"({new_name})")
        rewritten = rewritten.replace(f"{old_name} DESC", f"{new_name} DESC")
        rewritten = rewritten.replace(f"_{old_name}", f"_{new_name}")
    for column_name in drop:
        rewritten = _drop_column_line(rewritten, column_name)
        rewritten = rewritten.replace(f"_{column_name}", "")
    return rewritten


async def _update_registry() -> int:
    pg = get_pg_client()
    rows = await pg.fetch_all(
        """
        SELECT layer, table_role, table_name, ddl_sql, partition_type, partition_column,
               partition_granularity, partition_count, description
        FROM ts_meta.ddlregistry
        WHERE is_active = TRUE
          AND layer = 'ods'
          AND table_name IN ('ods_news', 'ods_patent', 'ods_intelligence')
        ORDER BY table_role, table_name
        """
    )

    changed = 0
    for row in rows:
        logical_table = str(row["table_name"]).removeprefix("ods_")
        spec = _TABLES[("ods", logical_table)]
        rewritten = _rewrite_ddl(
            str(row["ddl_sql"]),
            drop=spec["drop"],
            rename=spec["rename"],
        )
        if rewritten == row["ddl_sql"]:
            continue
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
            updated_by="system:migrate-ods-field-contracts",
        )
        changed += 1
    return changed


async def _migrate_news() -> None:
    pg = get_pg_client()
    for schema_name in ("ts_ods", "ts_ods_hist"):
        await pg.execute(
            f"ALTER TABLE {schema_name}.ods_news DROP COLUMN IF EXISTS primary_attachment"
        )


async def _migrate_patent() -> None:
    pg = get_pg_client()
    for schema_name in ("ts_ods", "ts_ods_hist"):
        await pg.execute(
            f"ALTER TABLE {schema_name}.ods_patent RENAME COLUMN original_file TO url"
        )


async def _migrate_intelligence() -> None:
    pg = get_pg_client()
    for schema_name in ("ts_ods", "ts_ods_hist"):
        await pg.execute(
            f"""
            UPDATE {schema_name}.ods_intelligence
            SET url = COALESCE(original_file, url)
            WHERE original_file IS NOT NULL
            """
        )
        await pg.execute(
            f"ALTER TABLE {schema_name}.ods_intelligence DROP COLUMN IF EXISTS original_file"
        )


async def main() -> None:
    pg = get_pg_client()
    await pg.connect()
    try:
        await ensure_ddlregistry_table(pg)
        registry_changed = await _update_registry()
        await _migrate_news()
        await _migrate_patent()
        await _migrate_intelligence()
        print(f"[done] registry_changed={registry_changed}")
    finally:
        await pg.close()


if __name__ == "__main__":
    asyncio.run(main())
