from __future__ import annotations

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from app.storage.etl_metadata_store import ensure_ddlregistry_table, get_registered_ddl_record
from app.storage.postgres_client import get_pg_client


async def ensure_hash_partitions(*, layer: str, table: str, table_role: str = "current") -> int:
    pg = get_pg_client()
    ddl_record = await get_registered_ddl_record(pg, layer, table, table_role)
    if not ddl_record:
        raise RuntimeError(f"Missing ddlregistry record for {layer}/{table} ({table_role})")

    if ddl_record.get("partition_type") != "hash":
        raise RuntimeError(f"{layer}/{table} ({table_role}) is not hash-partitioned")

    partition_count = int(ddl_record["partition_count"])
    schema_name = str(ddl_record["schema_name"])
    table_name = str(ddl_record["table_name"])
    parent_table = f"{schema_name}.{table_name}"
    width = max(2, len(str(partition_count - 1)))

    created = 0
    for remainder in range(partition_count):
        partition_name = f"{schema_name}.{table_name}_p{remainder:0{width}d}"
        sql = (
            f"CREATE TABLE IF NOT EXISTS {partition_name} "
            f"PARTITION OF {parent_table} "
            f"FOR VALUES WITH (MODULUS {partition_count}, REMAINDER {remainder})"
        )
        await pg.execute(sql)
        created += 1
    return created


async def main() -> None:
    pg = get_pg_client()
    await pg.connect()
    try:
        await ensure_ddlregistry_table(pg)
        created = await ensure_hash_partitions(layer="ods", table="news", table_role="current")
        print(f"[done] ensured_hash_partitions={created}")
    finally:
        await pg.close()


if __name__ == "__main__":
    asyncio.run(main())
