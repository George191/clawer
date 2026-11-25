"""Persistence for ETL bindings and registered Ontology DDL."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from app.etl.schema.ddl import ddl_checksum, ddl_creates_table
from app.etl.schema.layout import normalize_table_role, qualified_table_name
from app.etl.schema.ontology import ONTOLOGY_OBJECTS_TABLE, sync_declared_object_properties, sync_object_metadata
from app.storage.postgres_client import PostgresClient


_OBJECT_FIELDS = """id, layer, table_role, data_type, data_type_source, schema_name, table_name, ddl_sql,
    version, checksum, description, partition_type, partition_column,
    partition_granularity, partition_count, updated_by, created_at, updated_at"""


def object_table_ref(record: dict[str, Any]) -> str:
    return qualified_table_name(record["schema_name"], record["table_name"])


async def ensure_ontology_tables(pg: PostgresClient) -> None:
    """Check that deployment installed the registry before starting ETL."""
    row = await pg.fetch_one(
        "SELECT to_regclass(:table_name) AS table_name", {"table_name": ONTOLOGY_OBJECTS_TABLE},
    )
    if not row or not row.get("table_name"):
        raise RuntimeError(f"{ONTOLOGY_OBJECTS_TABLE} is missing; initialize Ontology before running ETL")


async def get_registered_object(
    pg: PostgresClient, layer: str, data_type: str, table_role: str | None = None,
) -> dict[str, Any] | None:
    role = normalize_table_role(table_role)
    if not data_type:
        raise ValueError("Ontology binding lookup requires a semantic data type")
    rows = await pg.fetch_all(f"""SELECT {_OBJECT_FIELDS} FROM app.ontology_objects
        WHERE layer = :layer AND table_role = :table_role AND data_type = :data_type AND is_active
        LIMIT 2""", {"layer": layer, "table_role": role, "data_type": data_type})
    if len(rows) > 1:
        raise ValueError(f"Ambiguous Ontology binding for {layer}/{role}/{data_type}")
    return rows[0] if rows else None


async def require_registered_object(
    pg: PostgresClient, layer: str, data_type: str, table_role: str | None = None,
) -> dict[str, Any]:
    role = normalize_table_role(table_role)
    row = await get_registered_object(pg, layer, data_type, role)
    if row is None:
        raise ValueError(f"No active Ontology binding for {layer}/{role}/{data_type}")
    return row


async def list_registered_data_types(pg: PostgresClient, layer: str, table_role: str) -> list[str]:
    rows = await pg.fetch_all("""SELECT DISTINCT data_type FROM app.ontology_objects
        WHERE is_active AND layer = :layer AND table_role = :table_role AND data_type <> ''
        ORDER BY data_type""", {"layer": layer, "table_role": normalize_table_role(table_role)})
    return [row["data_type"] for row in rows]


async def get_registered_ddl(pg: PostgresClient, layer: str, table: str,
                             table_role: str | None = None) -> str | None:
    record = await get_registered_ddl_record(pg, layer, table, table_role)
    return record["ddl_sql"] if record else None


async def get_registered_ddl_record(pg: PostgresClient, layer: str, table: str,
                                    table_role: str | None = None) -> dict[str, Any] | None:
    record = await get_registered_object(pg, layer, table, table_role)
    return record if record and record["ddl_sql"] else None


async def list_registered_ddl_records(pg: PostgresClient, layer: str | None = None,
                                      table_role: str | None = None) -> list[dict[str, Any]]:
    sql = """SELECT id, layer, table_role, data_type, schema_name, table_name, version, checksum,
        description, partition_type, partition_column, partition_granularity,
        partition_count, updated_by, created_at, updated_at
        FROM app.ontology_objects WHERE is_active AND ddl_sql <> ''"""
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
    catalog_pg: PostgresClient,
) -> dict[str, Any]:
    record = await require_registered_object(pg, layer, table, table_role)
    schema_name, table_name, role = (record[key] for key in ("schema_name", "table_name", "table_role"))
    if not ddl_creates_table(ddl_sql, schema_name, table_name):
        raise ValueError(f"Registered DDL must create {object_table_ref(record)}")
    params = {"id": record["id"], "layer": layer, "table_role": role, "data_type": table,
              "schema_name": schema_name, "table_name": table_name}
    async with pg.locked_transaction(f"ontology:{layer}:{role}:{schema_name}:{table_name}") as session:
        current = (await session.execute(text("""SELECT version, checksum, partition_type,
            partition_column, partition_granularity, partition_count FROM app.ontology_objects
            WHERE id = :id AND is_active AND layer = :layer AND table_role = :table_role
              AND data_type = :data_type AND schema_name = :schema_name AND table_name = :table_name
            FOR UPDATE"""), params)).mappings().first()
        if current is None:
            raise ValueError(f"Ontology binding changed during DDL registration: {layer}/{role}/{table}")
        kind = partition_type or current["partition_type"]
        column = (partition_column or current["partition_column"]) if kind in {"hash", "range"} else None
        granularity = (partition_granularity or current["partition_granularity"]) if kind == "range" else None
        count = (partition_count if partition_count is not None else current["partition_count"]) if kind == "hash" else None
        checksum = ddl_checksum(ddl_sql, table_role=role, partition_type=kind,
            partition_column=column, partition_granularity=granularity, partition_count=count)
        changed = current["checksum"] != checksum
        if current["checksum"] and not allow_update and changed:
            raise ValueError(f"Schema change requires explicit review: {layer}/{table}")
        version = int(current["version"]) + int(changed)
        await session.execute(text("""UPDATE app.ontology_objects SET
            ddl_sql = :ddl_sql, version = :version, checksum = :checksum, partition_type = :kind,
            partition_column = :column, partition_granularity = :granularity, partition_count = :count,
            description = COALESCE(:description, description),
            description_source = CASE WHEN :description IS NULL THEN description_source ELSE 'registration' END,
            updated_by = COALESCE(:updated_by, updated_by), updated_at = NOW() WHERE id = :id"""), {
                "id": record["id"], "ddl_sql": ddl_sql, "version": version, "checksum": checksum,
                "kind": kind, "column": column, "granularity": granularity, "count": count,
                "description": description, "updated_by": updated_by,
            })
        async with catalog_pg.session() as catalog_session:
            physical = await sync_object_metadata(
                session, schema_name, table_name, catalog_session=catalog_session,
                layer=layer, table_role=role,
                data_type=record["data_type"], data_type_source=record["data_type_source"], allow_missing=True,
            )
        if physical is None:
            await sync_declared_object_properties(session, record["id"], layer, table, role)
    return {**params, "version": version, "changed": changed}
