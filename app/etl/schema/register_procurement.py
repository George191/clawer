"""Register and create the four SAM RDS/ODS tables only.

Run ``python -m app.etl.schema.register_procurement`` to inspect the DDL without connecting
to PostgreSQL. Add ``--apply`` to register the DDL and create hash partitions.
The ETL metadata registry must already exist; this is not a database bootstrap.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from typing import Any

from app.etl.schema.baselines import baseline_current_ddls
from app.etl.schema.ddl import build_current_ddl, ddl_checksum
from app.etl.schema.layout import default_table_layout

TARGETS = (("rds", "contract"), ("rds", "tender"), ("ods", "contract"), ("ods", "tender"))


def registration_plan() -> list[dict[str, Any]]:
    baselines = baseline_current_ddls()
    plan = []
    for layer, table in TARGETS:
        layout = default_table_layout(layer, table, "current")
        ddl_sql = build_current_ddl(
            layer=layer,
            logical_table=table,
            current_ddl_sql=baselines[layer][table],
            partition_type=layout.partition_type,
            partition_column=layout.partition_column,
        )
        metadata = asdict(layout)
        checksum = ddl_checksum(
            ddl_sql,
            table_role=layout.table_role,
            partition_type=layout.partition_type,
            partition_column=layout.partition_column,
            partition_granularity=layout.partition_granularity,
            partition_count=layout.partition_count,
        )
        plan.append({**metadata, "checksum": checksum, "ddl_sql": ddl_sql})
    return plan


async def register(*, apply: bool = False) -> dict[str, Any]:
    plan = registration_plan()
    report: dict[str, Any] = {"mode": "apply" if apply else "dry-run", "tables": plan}
    if not apply:
        return report

    from app.etl.base import ETLBase
    from app.etl.schema import registry
    from app.storage.postgres_client import get_pg_client

    pg = get_pg_client()
    await pg.connect(max_retries=1)
    registry_was_ready = registry._DDLREGISTRY_READY
    try:
        existing_registry = await pg.fetch_one(
            "SELECT to_regclass('ts_meta.ddlregistry') AS table_name"
        )
        if not existing_registry or not existing_registry["table_name"]:
            raise RuntimeError("ts_meta.ddlregistry must already exist; initialize the ETL database first")
        # Verify the existing registry layout before suppressing its bootstrap.
        await pg.fetch_all("""
            SELECT id, layer, table_role, schema_name, table_name, ddl_sql,
                   version, checksum, partition_type, partition_column,
                   partition_granularity, partition_count, description,
                   updated_by, is_active, created_at, updated_at
            FROM ts_meta.ddlregistry WHERE FALSE
        """)
        # Normal registry initialization seeds every baseline. This standalone
        # maintenance process has verified the registry and must touch SAM only.
        registry._DDLREGISTRY_READY = True
        for item in plan:
            registration = await registry.save_registered_ddl(
                pg,
                layer=item["layer"],
                table=item["logical_table"],
                ddl_sql=item["ddl_sql"],
                table_role=item["table_role"],
                partition_type=item["partition_type"],
                partition_column=item["partition_column"],
                partition_granularity=item["partition_granularity"],
                partition_count=item["partition_count"],
                description=f"SAM {item['layer']}/{item['logical_table']} current table",
                updated_by="script:register_procurement",
            )
            worker = ETLBase()
            worker._layer = item["layer"]
            worker._pg = pg
            await worker._ensure_registered_table(item["logical_table"], "current")
            physical_table = f"{item['schema_name']}.{item['table_name']}"
            physical = await pg.fetch_one("""
                SELECT c.relkind::text AS relkind, count(i.inhrelid) AS partition_count
                FROM pg_class c
                LEFT JOIN pg_inherits i ON i.inhparent = c.oid
                WHERE c.oid = to_regclass(:table_name)
                GROUP BY c.oid, c.relkind
            """, {"table_name": physical_table})
            if (
                not physical
                or physical["relkind"] != "p"
                or physical["partition_count"] != item["partition_count"]
            ):
                raise RuntimeError(f"{physical_table}: expected {item['partition_count']} hash partitions")
            item["registration"] = registration
            item["physical_partition_count"] = physical["partition_count"]
    finally:
        registry._DDLREGISTRY_READY = registry_was_ready
        await pg.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Register and create the SAM tables")
    args = parser.parse_args()
    report = (
        asyncio.run(register(apply=True)) if args.apply
        else {"mode": "dry-run", "tables": registration_plan()}
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
