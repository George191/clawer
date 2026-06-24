from __future__ import annotations

import asyncio
import re

from sqlalchemy import text

from app.storage.etl_metadata_store import ensure_ddlregistry_table
from app.storage.postgres_client import get_pg_client

_DDL_STMT_SEP = re.compile(r";\s*\n\s*")
_BACKUP_SCHEMA_SUFFIX = "_bak"


def _split_ddl(ddl_sql: str) -> list[str]:
    return [stmt.strip() for stmt in _DDL_STMT_SEP.split(ddl_sql) if stmt.strip()]


def _hash_partition_sqls(
    *,
    schema_name: str,
    table_name: str,
    partition_count: int,
) -> list[str]:
    width = max(2, len(str(partition_count - 1)))
    parent_table = f"{schema_name}.{table_name}"
    sqls: list[str] = []
    for remainder in range(partition_count):
        partition_name = f"{schema_name}.{table_name}_p{remainder:0{width}d}"
        sqls.append(
            f"CREATE TABLE IF NOT EXISTS {partition_name} "
            f"PARTITION OF {parent_table} "
            f"FOR VALUES WITH (MODULUS {partition_count}, REMAINDER {remainder})"
        )
    return sqls


async def _table_exists(session, *, schema_name: str, table_name: str) -> bool:
    row = await session.execute(
        text("SELECT to_regclass(:table_name) AS table_name"),
        {"table_name": f"{schema_name}.{table_name}"},
    )
    return row.mappings().first()["table_name"] is not None


async def _is_partitioned_table(session, *, schema_name: str, table_name: str) -> bool:
    row = await session.execute(
        text("""
            SELECT 1
            FROM pg_partitioned_table pt
            JOIN pg_class c ON c.oid = pt.partrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :schema_name
              AND c.relname = :table_name
            LIMIT 1
        """),
        {"schema_name": schema_name, "table_name": table_name},
    )
    return row.mappings().first() is not None


async def _row_count(session, *, schema_name: str, table_name: str) -> int:
    row = await session.execute(
        text(f"SELECT COUNT(*)::bigint AS cnt FROM {schema_name}.{table_name}")
    )
    return int(row.mappings().first()["cnt"])


async def _schema_is_empty(session, *, schema_name: str) -> bool:
    row = await session.execute(
        text("""
            SELECT COUNT(*)::int AS cnt
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = :schema_name
              AND c.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
        """),
        {"schema_name": schema_name},
    )
    return int(row.mappings().first()["cnt"]) == 0


async def _migrate_current_table(pg, ddl_record: dict[str, object]) -> None:
    schema_name = str(ddl_record["schema_name"])
    table_name = str(ddl_record["table_name"])
    partition_count = int(ddl_record["partition_count"])
    parent_table = f"{schema_name}.{table_name}"
    backup_schema = f"{schema_name}{_BACKUP_SCHEMA_SUFFIX}"
    backup_table = f"{backup_schema}.{table_name}"

    async with pg.locked_transaction(f"hash-migrate:{parent_table}") as session:
        if await _is_partitioned_table(session, schema_name=schema_name, table_name=table_name):
            for stmt in _hash_partition_sqls(
                schema_name=schema_name,
                table_name=table_name,
                partition_count=partition_count,
            ):
                await session.execute(text(stmt))
            print(f"[skip] already partitioned: {parent_table}")
            return

        if not await _table_exists(session, schema_name=schema_name, table_name=table_name):
            for stmt in _split_ddl(str(ddl_record["ddl_sql"])):
                await session.execute(text(stmt))
            for stmt in _hash_partition_sqls(
                schema_name=schema_name,
                table_name=table_name,
                partition_count=partition_count,
            ):
                await session.execute(text(stmt))
            print(f"[create] {parent_table} -> hash/{partition_count}")
            return

        if await _table_exists(session, schema_name=backup_schema, table_name=table_name):
            raise RuntimeError(f"Backup table already exists: {backup_table}")

        source_count = await _row_count(session, schema_name=schema_name, table_name=table_name)
        await session.execute(text(f"CREATE SCHEMA IF NOT EXISTS {backup_schema}"))
        await session.execute(text(f"ALTER TABLE {parent_table} SET SCHEMA {backup_schema}"))

        for stmt in _split_ddl(str(ddl_record["ddl_sql"])):
            await session.execute(text(stmt))
        for stmt in _hash_partition_sqls(
            schema_name=schema_name,
            table_name=table_name,
            partition_count=partition_count,
        ):
            await session.execute(text(stmt))

        await session.execute(
            text(f"INSERT INTO {parent_table} SELECT * FROM {backup_table}")
        )
        target_count = await _row_count(session, schema_name=schema_name, table_name=table_name)
        if target_count != source_count:
            raise RuntimeError(
                f"Row count mismatch for {parent_table}: source={source_count}, target={target_count}"
            )

        await session.execute(text(f"DROP TABLE {backup_table}"))
        if await _schema_is_empty(session, schema_name=backup_schema):
            await session.execute(text(f"DROP SCHEMA {backup_schema}"))
        print(
            f"[migrate] {parent_table} -> hash/{partition_count} rows={target_count}"
        )


async def main() -> None:
    pg = get_pg_client()
    await pg.connect()
    try:
        await ensure_ddlregistry_table(pg)
        rows = await pg.fetch_all(
            """
            SELECT layer, schema_name, table_name, ddl_sql, partition_type, partition_count
            FROM ts_meta.ddlregistry
            WHERE is_active = TRUE
              AND table_role = 'current'
              AND layer IN ('rds', 'ods')
            ORDER BY layer, table_name
            """
        )
        migrated = 0
        for row in rows:
            if row.get("partition_type") != "hash":
                print(f"[skip] unsupported partition type: {row['schema_name']}.{row['table_name']}")
                continue
            await _migrate_current_table(pg, row)
            migrated += 1
        print(f"[done] processed {migrated} current tables")
    finally:
        await pg.close()


if __name__ == "__main__":
    asyncio.run(main())
