from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.storage.etl_metadata_store import ensure_ddlregistry_table, get_registered_ddl_record
from app.storage.postgres_client import get_pg_client

_DDL_STMT_SEP = re.compile(r";\s*\n\s*")


def _split_ddl(ddl_sql: str) -> list[str]:
    return [stmt.strip() for stmt in _DDL_STMT_SEP.split(ddl_sql) if stmt.strip()]


async def rebuild_current_hash_table(*, layer: str, table: str) -> None:
    pg = get_pg_client()
    ddl_record = await get_registered_ddl_record(pg, layer, table, "current")
    if not ddl_record:
        raise RuntimeError(f"Missing current ddlregistry record for {layer}/{table}")
    if ddl_record.get("partition_type") != "hash":
        raise RuntimeError(f"{layer}/{table} current table is not hash partitioned")

    for stmt in _split_ddl(str(ddl_record["ddl_sql"])):
        await pg.execute(stmt)

    schema_name = str(ddl_record["schema_name"])
    table_name = str(ddl_record["table_name"])
    partition_count = int(ddl_record["partition_count"])
    parent_table = f"{schema_name}.{table_name}"
    width = max(2, len(str(partition_count - 1)))

    for remainder in range(partition_count):
        partition_name = f"{schema_name}.{table_name}_p{remainder:0{width}d}"
        sql = (
            f"CREATE TABLE IF NOT EXISTS {partition_name} "
            f"PARTITION OF {parent_table} "
            f"FOR VALUES WITH (MODULUS {partition_count}, REMAINDER {remainder})"
        )
        await pg.execute(sql)


async def main() -> None:
    pg = get_pg_client()
    await pg.connect()
    try:
        await ensure_ddlregistry_table(pg)
        await rebuild_current_hash_table(layer="ods", table="news")
        print("[done] rebuilt ts_ods.ods_news with hash partitions")
    finally:
        await pg.close()


if __name__ == "__main__":
    asyncio.run(main())
