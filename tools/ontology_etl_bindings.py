"""Preview and backfill missing semantic types on verified legacy ETL registrations."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.etl.schema.catalogs import CATALOG_ODS_COLUMNS, validate_table_name
from app.etl.schema.ddl import ddl_creates_table
from app.records.types import SYNC_DATA_TYPES
from app.storage.business_database import get_layer_pg_client
from app.storage.postgres_client import get_pg_client


_BUILTIN_TYPES = {"rds": frozenset(SYNC_DATA_TYPES), "ods": frozenset(CATALOG_ODS_COLUMNS)}
_FIXED_LEGACY = {
    ("task", "ts_task", "task_vulnerability"): ("vulnerability", {"task", "result", "result_type"}),
    ("task", "ts_task", "task_patent"): ("patent", {"task", "result", "result_type"}),
    ("dwd", "ts_dwd", "dwd_patent"): ("patent", {"title", "task_results"}),
}
_RETIRED_LEGACY = {("rds", "ts_rds", "rds_twitter")}
_REQUIRED_FIELDS = {
    "rds": {"record_id", "data_source", "data_type", "raw_data", "kafka_topic", "kafka_partition", "kafka_offset"},
    "ods": {"record_id", "data_source", "data_type", "created_at", "updated_at"},
    "task": {"record_id", "data_source", "data_type"},
    "dwd": {"record_id", "data_source", "data_type"},
}
_WORKER_REGISTRANTS = {
    "rds": {"system:rds", "system:current-bootstrap"},
    "ods": {"system:ods", "system:current-bootstrap"},
    "task": {"system:task"},
    "dwd": set(),
}


def _legacy_type(row: dict) -> tuple[str, set[str]] | None:
    layer, schema, table = (row[key] for key in ("layer", "schema_name", "table_name"))
    if fixed := _FIXED_LEGACY.get((layer, schema, table)):
        return fixed
    if layer not in _BUILTIN_TYPES or schema != f"ts_{layer}" or not table.startswith(f"{layer}_"):
        return None
    candidate = table.removeprefix(f"{layer}_")
    if candidate not in _BUILTIN_TYPES[layer] and row["updated_by"] not in _WORKER_REGISTRANTS[layer]:
        return None
    if not ddl_creates_table(row["ddl_sql"], schema, table):
        return None
    try:
        return validate_table_name(candidate), set()
    except ValueError:
        return None


async def _verify_columns(warehouse, row: dict, required: set[str]) -> bool:
    columns = await warehouse.fetch_all(
        """SELECT a.attname AS name FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        WHERE n.nspname = :schema_name AND c.relname = :table_name
          AND c.relkind IN ('r', 'p') AND NOT c.relispartition""",
        {key: row[key] for key in ("schema_name", "table_name")},
    )
    return required <= {column["name"] for column in columns}


async def backfill(metadata, *, apply: bool = False) -> dict:
    proposals, unresolved, retained = [], [], []
    warehouses = {}
    try:
        async with metadata.session() as session:
            if apply:
                await session.execute(text("SET LOCAL lock_timeout = '5s'"))
                await session.execute(text("LOCK TABLE app.helio_objects IN SHARE ROW EXCLUSIVE MODE"))
            rows = [dict(row) for row in (await session.execute(text("""
                SELECT id, layer, table_role, data_type, schema_name, table_name, ddl_sql, updated_by
                FROM app.helio_objects
                WHERE is_active AND table_role = 'current' AND layer IN ('rds', 'ods', 'task', 'dwd')
                ORDER BY id
            """))).mappings()]
            for row in rows:
                if row["data_type"]:
                    continue
                layer = row["layer"]
                identity = {key: row[key] for key in ("id", "layer", "table_role", "schema_name", "table_name")}
                if (layer, row["schema_name"], row["table_name"]) in _RETIRED_LEGACY:
                    # This table contains retired twitter rows, separate from the active tweet binding.
                    retained.append({**identity, "reason": "retired table remains queryable"})
                    continue
                candidate = _legacy_type(row)
                if candidate is None:
                    if row["updated_by"] in _WORKER_REGISTRANTS[layer]:
                        unresolved.append({**identity, "reason": "worker registration needs an explicit semantic type"})
                    continue
                data_type, required = candidate
                if layer not in warehouses:
                    warehouses[layer] = get_layer_pg_client(layer)
                if not await _verify_columns(warehouses[layer], row, required | _REQUIRED_FIELDS[layer]):
                    unresolved.append({**identity, "reason": "physical ETL columns could not be verified"})
                    continue
                proposals.append({**identity, "data_type": data_type})
            key = lambda row: (row["layer"], row["table_role"], row["data_type"])
            counts = Counter(key(row) for row in rows if row["data_type"])
            counts.update(key(row) for row in proposals)
            conflicts = [{"layer": layer, "table_role": role, "data_type": kind, "count": count}
                         for (layer, role, kind), count in sorted(counts.items()) if count > 1]
            blocked = bool(unresolved or conflicts)
            if apply and not blocked:
                for row in proposals:
                    result = await session.execute(text("""
                        UPDATE app.helio_objects SET data_type = :data_type, data_type_source = 'registration'
                        WHERE id = :id AND layer = :layer AND table_role = :table_role AND is_active
                          AND schema_name = :schema_name AND table_name = :table_name AND data_type = ''
                        RETURNING id
                    """), row)
                    if result.scalar_one() != row["id"]:
                        raise RuntimeError(f"Ontology object {row['id']} changed during migration")
            return {"applied": apply and not blocked, "blocked": blocked, "updates": proposals,
                    "unresolved": unresolved, "conflicts": conflicts, "retained": retained}
    finally:
        for warehouse in set(warehouses.values()):
            await warehouse.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    options = parser.parse_args()
    metadata = get_pg_client()
    try:
        report = await backfill(metadata, apply=options.apply)
        print(json.dumps(report, indent=2))
        if report["blocked"]:
            raise SystemExit(1)
    finally:
        await metadata.close()


if __name__ == "__main__":
    asyncio.run(main())
