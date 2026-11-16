"""Authorization and source verification for independent semantic object definitions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models import Team, TeamUserJoin, Tenant, User
from app.web.api.models.ontology_registry import (
    Definition,
    DefinitionIssue,
    OntologyDefinitionRow,
    Source,
    ValidationResult,
)
from app.web.api.models.user import RoleTypes
from app.web.api.utils.models import StatusTypes
from app.web.services.etl_layers import get_etl_layers, quote_identifier
from app.web.services.ontology_registry_policy import definition_policy_errors, visibility

WAREHOUSE_SCHEMAS = frozenset({"ts_ods", "ts_dwd"})
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
COLUMN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
SQL_TYPES: dict[str, str] = {
    "text": "string", "varchar": "string", "bpchar": "string", "uuid": "string",
    "int2": "integer", "int4": "integer", "int8": "integer",
    "float4": "number", "float8": "number", "numeric": "number",
    "bool": "boolean", "date": "date", "timestamp": "datetime", "timestamptz": "datetime",
    "json": "json", "jsonb": "json",
}


def projects_statement(user: User, *, manage: bool = False):
    statement = select(Team).join(Tenant, Tenant.id == Team.tenant_id).where(
        Tenant.status == StatusTypes.ENABLE,
    )
    if not user.is_superuser:
        statement = statement.join(TeamUserJoin, TeamUserJoin.team_id == Team.id).where(
            TeamUserJoin.user_id == user.id, TeamUserJoin.status == StatusTypes.ENABLE,
        )
        if manage:
            statement = statement.where(TeamUserJoin.role.in_((RoleTypes.ADMIN, RoleTypes.OWNER)))
    return statement


async def get_project(session: AsyncSession, user: User, project_id: UUID, *, manage: bool = False) -> Team:
    project = await session.scalar(projects_statement(user, manage=manage).where(Team.id == project_id))
    if project is None:
        raise HTTPException(404, "Project not found or not accessible")
    return project


def require_source_access(user: User, source: Source) -> None:
    if source.mode == "existing" and not user.is_superuser:
        raise HTTPException(403, "Warehouse source binding is restricted to platform administrators")


def check_revision(row: OntologyDefinitionRow, revision: int):
    if row.revision != revision:
        raise HTTPException(409, "This draft changed. Reload it before continuing.")


async def get_published(session: AsyncSession, user: User, definition_id: UUID) -> OntologyDefinitionRow:
    accessible = projects_statement(user).with_only_columns(Team.id)
    row = await session.scalar(select(OntologyDefinitionRow).where(
        OntologyDefinitionRow.id == definition_id, OntologyDefinitionRow.published_at.is_not(None),
        OntologyDefinitionRow.project_id.in_(accessible),
    ))
    if row is None:
        raise HTTPException(404, "Object type not found")
    source = Definition.model_validate(row.published_definition).source
    if source.mode == "existing" and not user.is_superuser:
        raise HTTPException(404, "Object type not found")
    return row


async def visible_definition(session: AsyncSession, user: User, row: OntologyDefinitionRow) -> dict[str, Any] | None:
    from app.web.services.ontology_registry_identity import project_access_context

    definition = Definition.model_validate(row.published_definition)
    if definition.source.mode == "existing" and not user.is_superuser:
        return None
    context = await project_access_context(
        session, user, row.project_id, source_access=definition.source.mode == 'none' or user.is_superuser,
    )
    allowed, fields = visibility(definition.security, [item.id for item in definition.properties], context)
    if not allowed:
        return None
    payload = definition.model_dump(mode="json", by_alias=True)
    payload["properties"] = [item for item in payload["properties"] if item["id"] in fields]
    payload["primaryKey"] = [field for field in payload["primaryKey"] if field in fields]
    if payload["titleKey"] not in fields:
        payload["titleKey"] = ""
    # Identity and policy definitions are editable in the draft, not public object metadata.
    payload["actions"] = []
    payload["security"]["objectReaders"] = []
    payload["security"]["fieldReaders"] = {}
    return payload


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str
    nullable: bool
    kind: str


@dataclass(frozen=True)
class WarehouseTable:
    oid: int
    columns: dict[str, Column]
    unique_keys: tuple[tuple[str, ...], ...]


def assess_definition(
    definition: Definition, source: WarehouseTable | None = None, *, eligible=None,
) -> ValidationResult:
    result = ValidationResult()

    def error(path: str, message: str):
        result.errors.append(DefinitionIssue(path=path, message=message))

    if not IDENTIFIER.fullmatch(definition.id):
        error("id", "Use a lowercase identifier beginning with a letter")
    if not definition.name:
        error("name", "Enter a display name")
    if not definition.pluralName:
        error("pluralName", "Enter a plural display name")
    if definition.icon and not re.fullmatch(r"[a-z][a-z0-9_-]*", definition.icon):
        error("icon", "Choose a supported icon identifier")
    if definition.color and not re.fullmatch(r"#[0-9a-fA-F]{6}", definition.color):
        error("color", "Enter a six-digit hexadecimal color")
    if any(not name for name in definition.groups) or len(definition.groups) != len(set(definition.groups)):
        error("groups", "Group names must be nonempty and unique")

    properties = {item.id: item for item in definition.properties}
    if not properties or len(properties) != len(definition.properties):
        error("properties", "Define at least one property and give each property a unique identifier")
    for index, prop in enumerate(definition.properties):
        if not IDENTIFIER.fullmatch(prop.id) or not prop.name:
            error(f"properties.{index}", "Enter a valid property identifier and display name")
    if not definition.primaryKey or len(set(definition.primaryKey)) != len(definition.primaryKey):
        error("primaryKey", "Select one or more distinct primary key properties")
    for key in definition.primaryKey:
        prop = properties.get(key)
        if prop is None or prop.type not in ("string", "integer", "date", "datetime", "boolean"):
            error("primaryKey", "Primary key fields must refer to supported scalar properties")
        elif not prop.required:
            error("primaryKey", "Primary key properties must be required")
    if not definition.titleKey or definition.titleKey not in properties:
        error("titleKey", "Select an existing property to display the title")
    elif properties[definition.titleKey].type == "json":
        error("titleKey", "A title must be a scalar property")

    mode = definition.source.mode
    if mode == "existing":
        selected = definition.source
        if selected.schema_name not in WAREHOUSE_SCHEMAS or not COLUMN.fullmatch(selected.table or ""):
            error("source", "Choose a table in the managed ts_ods or ts_dwd schema")
        elif source is None:
            error("source", "Selected warehouse table is unavailable")
        else:
            mapped: set[str] = set()
            for index, prop in enumerate(definition.properties):
                column = source.columns.get(prop.sourceColumn or "")
                if column is None:
                    error(f"properties.{index}.sourceColumn", "Choose an existing source column")
                    continue
                if column.name in mapped:
                    error(f"properties.{index}.sourceColumn", "Each source column can be mapped once")
                mapped.add(column.name)
                if SQL_TYPES.get(column.kind) != prop.type:
                    error(f"properties.{index}.type", f"Does not match the source type ({column.sql_type})")
                if prop.required and column.nullable:
                    error(f"properties.{index}.required", "Source does not guarantee a non-null value")
            keys = tuple(properties[key].sourceColumn for key in definition.primaryKey if key in properties)
            if not any(len(keys) == len(candidate) and set(keys) == set(candidate) for candidate in source.unique_keys):
                error("primaryKey", "Select the columns of a non-null database primary or unique key")
    elif definition.source.schema_name or definition.source.table or any(item.sourceColumn for item in definition.properties):
        error("source", "Objects without a source cannot have warehouse column mappings")

    for issue in definition_policy_errors(
        source_mode=mode, actions=definition.actions, security=definition.security,
        property_ids=set(properties), primary_fields=set(definition.primaryKey),
        required_fields={item.id for item in definition.properties if item.required}, eligible=eligible,
    ):
        error("security", issue)
    if not definition.description:
        result.warnings.append(DefinitionIssue(path="description", message="No description is configured"))
    if definition.titleKey in properties and not properties[definition.titleKey].required:
        result.warnings.append(DefinitionIssue(path="titleKey", message="Some instances may have no display title"))
    return result


async def _warehouse_table(session: AsyncSession, source: Source) -> WarehouseTable | None:
    if source.schema_name not in WAREHOUSE_SCHEMAS or not COLUMN.fullmatch(source.table or ""):
        return None
    params = {"schema": source.schema_name, "table": source.table}
    oid = await session.scalar(text("""
        SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :schema AND c.relname = :table
          AND c.relkind IN ('r', 'p') AND NOT c.relispartition
          AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid)
          AND has_table_privilege(current_user, c.oid, 'SELECT')
        """), params)
    if oid is None:
        return None
    records = (await session.execute(text("""
        SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS sql_type,
               NOT a.attnotnull AS nullable,
               CASE WHEN t.typnamespace = 'pg_catalog'::regnamespace THEN t.typname ELSE '' END AS kind
        FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid
        WHERE a.attrelid = CAST(:oid AS oid) AND a.attnum > 0 AND NOT a.attisdropped
        """), {"oid": oid})).mappings()
    columns = {
        row["attname"]: Column(row["attname"], row["sql_type"], row["nullable"], row["kind"])
        for row in records if COLUMN.fullmatch(row["attname"])
    }
    keys = (await session.execute(text("""
        SELECT ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum, ord)
                     JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
                     WHERE k.ord <= i.indnkeyatts ORDER BY k.ord)
        FROM pg_index i
        WHERE i.indrelid = CAST(:oid AS oid) AND i.indisunique AND i.indisvalid AND i.indisready
          AND i.indimmediate AND i.indpred IS NULL AND i.indexprs IS NULL
        """), {"oid": oid})).scalars()
    unique_keys = tuple(tuple(key) for key in keys if key and all(
        item in columns and not columns[item].nullable for item in key
    ))
    return WarehouseTable(int(oid), columns, unique_keys)


async def warehouse_table(source: Source) -> WarehouseTable | None:
    if source.mode != "existing" or source.schema_name not in WAREHOUSE_SCHEMAS:
        return None
    async with get_etl_layers().session() as session:
        return await _warehouse_table(session, source)


def source_signature(definition: Definition, table: WarehouseTable) -> dict[str, Any]:
    columns = sorted({prop.sourceColumn for prop in definition.properties if prop.sourceColumn})
    return {
        "columns": {
            name: [table.columns[name].sql_type, table.columns[name].nullable, table.columns[name].kind]
            for name in columns
        },
        "primaryKey": [
            next(prop.sourceColumn for prop in definition.properties if prop.id == key)
            for key in definition.primaryKey
        ],
    }


def ensure_source_identity(row: OntologyDefinitionRow, definition: Definition, table: WarehouseTable | None) -> None:
    try:
        signature = source_signature(definition, table) if table is not None else None
    except KeyError:
        signature = None
    if table is None or table.oid != row.source_oid or signature is None or signature != row.source_signature:
        raise HTTPException(409, "The bound warehouse source has changed or is unavailable")
    keys = signature["primaryKey"]
    if not any(len(keys) == len(candidate) and set(keys) == set(candidate) for candidate in table.unique_keys):
        raise HTTPException(409, "The bound warehouse source key is no longer valid")


async def read_bound_records(row: OntologyDefinitionRow, columns: list[str], limit: int, offset: int) -> dict:
    definition = Definition.model_validate(row.published_definition)
    assert definition.source.mode == "existing"
    source = definition.source
    if source.schema_name not in WAREHOUSE_SCHEMAS or not COLUMN.fullmatch(source.table or ""):
        raise HTTPException(409, "Invalid warehouse binding")
    allowed = set(columns)
    props = [item for item in definition.properties if item.id in allowed]
    if any(not IDENTIFIER.fullmatch(item.id) or not COLUMN.fullmatch(item.sourceColumn or "") for item in props):
        raise HTTPException(409, "Invalid published property mapping")
    async with get_etl_layers().session() as session:
        relation = f"{quote_identifier(source.schema_name)}.{quote_identifier(source.table)}"
        # Prevent concurrent rename/drop between verifying its identity and reading rows.
        # Only PostgreSQL identifiers from the published schema/table are interpolated.
        try:
            await session.execute(text(f"LOCK TABLE {relation} IN ACCESS SHARE MODE"))
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) in {"42P01", "3F000", "42501"}:
                raise HTTPException(409, "The bound warehouse source has changed or is unavailable") from exc
            raise
        table = await _warehouse_table(session, source)
        ensure_source_identity(row, definition, table)
        if not props:
            return {"items": [], "hasMore": False}
        fields = ", ".join(
            quote_identifier(prop.sourceColumn) + ' AS "' + prop.id + '"' for prop in props
        )
        order = ", ".join(quote_identifier(key) for key in row.source_signature["primaryKey"])
        result = await session.execute(text(
            f"SELECT to_jsonb(source_row) FROM (SELECT {fields} FROM {relation} "
            f"ORDER BY {order} LIMIT :limit OFFSET :offset) AS source_row"
        ), {"limit": limit + 1, "offset": offset})
        records = result.scalars().all()
    return {"items": records[:limit], "hasMore": len(records) > limit}
