"""PostgreSQL persistence and bootstrap lifecycle for Ontology object DDL."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.etl.schema.baselines import baseline_current_ddls
from app.etl.schema.ddl import build_current_ddl, ddl_checksum
from app.etl.schema.object_relations import OBJECT_RELATIONS_DDL
from app.etl.schema.ontology import (
    ONTOLOGY_DDL, ONTOLOGY_OBJECTS_TABLE, resource_group,
    sync_declared_object_properties, sync_object_metadata,
)
from app.etl.schema.layout import default_table_layout, logical_table_name, normalize_table_role
from app.storage.postgres_client import PostgresClient

_ONTOLOGY_READY = False


def _table_identity(layer: str, table: str, table_role: str | None = None) -> tuple[str, str, str]:
    layout = default_table_layout(layer, table, table_role)
    return layout.schema_name, layout.table_name, layout.table_role


async def _seed_current_objects(pg: PostgresClient) -> None:
    existing = await pg.fetch_all("SELECT schema_name, table_name FROM public.ontology_objects")
    identities = {(row["schema_name"], row["table_name"]) for row in existing}
    for layer, tables in baseline_current_ddls().items():
        for logical_table, ddl_sql in tables.items():
            layout = default_table_layout(layer, logical_table, "current")
            if (layout.schema_name, layout.table_name) in identities:
                continue
            await save_registered_ddl(
                pg, layer=layer, table=logical_table,
                ddl_sql=build_current_ddl(layer=layer, logical_table=logical_table,
                    current_ddl_sql=ddl_sql, partition_type=layout.partition_type,
                    partition_column=layout.partition_column),
                table_role="current", partition_type=layout.partition_type,
                partition_column=layout.partition_column, partition_count=layout.partition_count,
                partition_granularity=layout.partition_granularity,
                updated_by="system:current-bootstrap",
            )


async def ensure_ontology_tables(pg: PostgresClient) -> None:
    """Explicit worker bootstrap for a new/current database; no migration branches."""
    global _ONTOLOGY_READY
    if _ONTOLOGY_READY:
        return
    await pg.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    await pg.init_schema([ONTOLOGY_DDL, OBJECT_RELATIONS_DDL])
    row = await pg.fetch_one("SELECT to_regclass(:table_name) AS table_name", {"table_name": ONTOLOGY_OBJECTS_TABLE})
    if not row or not row.get("table_name"):
        raise RuntimeError(f"{ONTOLOGY_OBJECTS_TABLE} is missing; initialize Ontology before running ETL")
    await _seed_current_objects(pg)
    _ONTOLOGY_READY = True


async def get_registered_ddl(pg: PostgresClient, layer: str, table: str,
                             table_role: str | None = None) -> str | None:
    schema, physical, _ = _table_identity(layer, table, table_role)
    row = await pg.fetch_one("""SELECT ddl_sql FROM public.ontology_objects
        WHERE schema_name = :schema_name AND table_name = :table_name
          AND is_active AND ddl_sql <> '' LIMIT 1""", {"schema_name": schema, "table_name": physical})
    return row["ddl_sql"] if row else None


async def get_registered_ddl_record(pg: PostgresClient, layer: str, table: str,
                                    table_role: str | None = None) -> dict[str, Any] | None:
    schema, physical, _ = _table_identity(layer, table, table_role)
    return await pg.fetch_one("""SELECT id, layer, table_role, schema_name, table_name,
        ddl_sql, version, checksum, description, partition_type, partition_column,
        partition_granularity, partition_count, updated_by, created_at, updated_at
        FROM public.ontology_objects
        WHERE schema_name = :schema_name AND table_name = :table_name
          AND is_active AND ddl_sql <> '' LIMIT 1""", {"schema_name": schema, "table_name": physical})


async def list_registered_ddl_records(pg: PostgresClient, layer: str | None = None,
                                      table_role: str | None = None) -> list[dict[str, Any]]:
    sql = """SELECT id, layer, table_role, schema_name, table_name, version, checksum,
        description, partition_type, partition_column, partition_granularity,
        partition_count, updated_by, created_at, updated_at
        FROM public.ontology_objects WHERE is_active AND ddl_sql <> ''"""
    params: dict[str, Any] = {}
    if layer:
        sql += " AND layer = :layer"
        params["layer"] = layer
    if table_role:
        sql += " AND table_role = :table_role"
        params["table_role"] = normalize_table_role(table_role)
    return await pg.fetch_all(sql + " ORDER BY layer, schema_name, table_name", params)


async def save_registered_ddl(
    pg: PostgresClient, *, layer: str, table: str, ddl_sql: str,
    table_role: str | None = None, partition_type: str | None = None,
    partition_column: str | None = None, partition_granularity: str | None = None,
    partition_count: int | None = None, description: str | None = None,
    updated_by: str | None = None, allow_update: bool = True,
) -> dict[str, Any]:
    schema_name, table_name, role = _table_identity(layer, table, table_role)
    defaults = default_table_layout(layer, table, role)
    kind = partition_type or defaults.partition_type
    column = (partition_column if partition_column is not None else defaults.partition_column) if kind in {"hash", "range"} else None
    granularity = (partition_granularity if partition_granularity is not None else defaults.partition_granularity) if kind == "range" else None
    count = (partition_count if partition_count is not None else defaults.partition_count) if kind == "hash" else None
    checksum = ddl_checksum(ddl_sql, table_role=role, partition_type=kind,
        partition_column=column, partition_granularity=granularity, partition_count=count)
    params = {"layer": layer, "table_role": role, "schema_name": schema_name, "table_name": table_name}
    async with pg.locked_transaction(f"ontology:{schema_name}:{table_name}") as session:
        current = (await session.execute(text("""SELECT id, version, checksum FROM public.ontology_objects
            WHERE schema_name = :schema_name AND table_name = :table_name FOR UPDATE"""), params)).mappings().first()
        if current and current["checksum"] and not allow_update and current["checksum"] != checksum:
            raise ValueError(f"Schema change requires explicit review: {layer}/{table}")
        changed = not current or current["checksum"] != checksum
        version = (int(current["version"]) if current else 0) + int(changed)
        result = await session.execute(text("""INSERT INTO public.ontology_objects (
            layer, table_role, schema_name, table_name, ddl_sql, version, checksum,
            partition_type, partition_column, partition_granularity, partition_count,
            description, description_source, updated_by, provenance, resource_group)
            VALUES (:layer, :table_role, :schema_name, :table_name, :ddl_sql, :version,
                :checksum, :partition_type, :partition_column, :partition_granularity,
                :partition_count, :description, :description_source, :updated_by, 'registration', :resource_group)
            ON CONFLICT (schema_name, table_name) DO UPDATE SET layer = EXCLUDED.layer,
                table_role = EXCLUDED.table_role, ddl_sql = EXCLUDED.ddl_sql, version = EXCLUDED.version,
                checksum = EXCLUDED.checksum, partition_type = EXCLUDED.partition_type,
                partition_column = EXCLUDED.partition_column, partition_granularity = EXCLUDED.partition_granularity,
                partition_count = EXCLUDED.partition_count,
                description = COALESCE(EXCLUDED.description, ontology_objects.description),
                description_source = CASE WHEN EXCLUDED.description IS NULL THEN ontology_objects.description_source ELSE EXCLUDED.description_source END,
                updated_by = COALESCE(EXCLUDED.updated_by, ontology_objects.updated_by),
                resource_group = EXCLUDED.resource_group, updated_at = NOW() RETURNING id"""), {
                    **params, "ddl_sql": ddl_sql, "version": version, "checksum": checksum,
                    "partition_type": kind, "partition_column": column, "partition_granularity": granularity,
                    "partition_count": count, "description": description,
                    "description_source": "registration" if description and description.strip() else "not_configured",
                    "updated_by": updated_by, "resource_group": resource_group(layer),
                })
        object_id = int(result.scalar_one())
        physical = await sync_object_metadata(session, schema_name, table_name, allow_missing=True)
        if physical is None:
            logical = logical_table_name(layer, table_name)
            declared = await sync_declared_object_properties(session, object_id, layer, logical, role)
            if declared:
                await session.execute(text("""UPDATE public.ontology_objects
                    SET data_type = :data_type, data_type_source = 'table_definition'
                    WHERE id = :id AND data_type_source <> 'registration'"""), {"id": object_id, "data_type": logical})
    return {**params, "version": version, "changed": changed}
