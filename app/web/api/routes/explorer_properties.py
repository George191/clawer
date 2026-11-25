"""Resolve physical object property metadata against current columns and permitted semantic publications."""

import hashlib
import json
import re
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert

from app.web.api.dependencies.common import CurrentActiveSuperuser, CurrentUser
from app.web.api.dependencies.session import SessionDep
from app.web.api.models.explorer_properties import ExplorerPropertyProfile
from app.web.api.models.ontology_registry import OntologyDefinitionRow, PropertyType
from app.web.services.etl_layers import get_etl_layers
from app.web.services.ontology_action_runtime import _definition_access
from app.web.services.ontology_registry import (
    WAREHOUSE_SCHEMAS,
    ensure_source_identity,
    published_catalog_statement,
    warehouse_table,
)
from app.web.api.routes.explorer_layouts import can_display

router = APIRouter(prefix="/etl", tags=["Explorer properties"])
Name = Annotated[str, Field(min_length=1, max_length=120)]
ColumnName = Annotated[str, Field(min_length=1, max_length=63)]
# Stable business property IDs and existing PostgreSQL identifiers are both supported.
PropertyId = Annotated[str, Field(min_length=1, max_length=63)]
Icon = Annotated[str, Field(max_length=63, pattern=r"^[a-zA-Z0-9_-]*$")]
Color = Annotated[str, Field(pattern=r"^(?:#[a-fA-F0-9]{6})?$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Property(Strict):
    id: PropertyId
    name: Name
    description: str = Field(default="", max_length=4000)
    type: PropertyType
    sourceColumn: ColumnName | None
    visibility: Literal["normal", "prominent", "hidden"] = "normal"
    summary: bool = Field(default=False, strict=True)
    group: Name | None = None
    status: Literal["active", "experimental", "deprecated"] = "active"
    formatter: Literal["default", "number", "percent", "date", "dateTime"] = "default"
    presentation: Literal["value", "timeSeries", "geoJson", "media"] = "value"


class Settings(Strict):
    titleProperty: ColumnName | None
    displayName: Name
    description: str = Field(default="", max_length=4000)
    icon: Icon = ""
    color: Color = ""
    groups: list[Name] = Field(max_length=100)
    properties: list[Property] = Field(max_length=1600)


class Write(Settings):
    version: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    sourceDefinitionId: UUID | None


def business_type(sql_type):
    t = sql_type.lower()
    if t in {"integer", "bigint", "smallint"}:
        return "integer"
    if t in {"real", "double precision"} or re.fullmatch(r"(?:numeric|decimal)(?:\([^)]*\))?", t):
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "date":
        return "date"
    if t.startswith("timestamp") and not t.endswith("[]"):
        return "datetime"
    if t in {"json", "jsonb"} or t.endswith("[]"):
        return "json"
    return "string"


def compatible(kind, sql_type):
    physical = business_type(sql_type)
    return kind == physical or (physical == "integer" and kind == "number")


def format_allowed(formatter, kind):
    return (
        formatter == "default"
        or (formatter in {"number", "percent"} and kind in {"integer", "number"})
        or (formatter in {"date", "dateTime"} and kind in {"date", "datetime"})
    )


async def warehouse_asset(layer, table, user):
    warehouse = get_etl_layers()
    target = await warehouse.runtime_table(layer, table, include_hidden=user.is_superuser)
    schema = target["schemaName"]
    columns = await warehouse.columns(schema, table)
    async with warehouse.session() as db:
        found = (
            (
                await db.execute(
                    text(
                        """
            SELECT COALESCE(NULLIF(obj_description(c.oid,'pg_class'),''),o.description,'') AS description,
                   COALESCE(o.data_type,'') AS data_type,
                   p.name, p.comment
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN app.ontology_objects o ON o.schema_name=n.nspname AND o.table_name=c.relname
            LEFT JOIN app.ontology_object_properties p ON p.object_id=o.id
            WHERE n.nspname=:schema AND c.relname=:table AND COALESCE(o.is_active,true)
              AND (:admin OR COALESCE(o.visibility,'normal') <> 'hidden')
              AND has_schema_privilege(n.oid,'USAGE') AND has_table_privilege(c.oid,'SELECT')
            """
                    ),
                    {"schema": schema, "table": table, "admin": user.is_superuser},
                )
            )
            .mappings()
            .all()
        )
    if not found:
        raise HTTPException(404, "Object type not found")
    comments = {r["name"]: r["comment"] for r in found}
    fields = {c["name"]: {**c, "comment": c["comment"] or comments.get(c["name"]) or ""} for c in columns}
    return schema, found[0]["description"], found[0]["data_type"], fields


async def source(session, user, schema, table, requested):
    if requested is None:
        return None
    if schema not in WAREHOUSE_SCHEMAS or not user.is_superuser:
        raise HTTPException(404, "Object type not found")
    model = OntologyDefinitionRow
    binding = model.published_definition["source"]
    query = published_catalog_statement(user).where(
        binding["mode"].astext == "existing",
        binding["schema"].astext == schema,
        binding["table"].astext == table,
        model.id == requested,
    )
    found = (await session.execute(query)).first()
    if not found:
        raise HTTPException(404, "Object type not found")
    row, definition, _, permitted = await _definition_access(session, user, found[0].id)
    if definition.source.schema_name != schema or definition.source.table != table:
        raise HTTPException(409, "The published source changed. Reload the property mapping.")
    ensure_source_identity(row, definition, await warehouse_table(definition.source))
    return row, definition, set(permitted)


def physical_property(field, meta):
    return Property(
        id=field,
        name=field,
        type=business_type(meta["type"]),
        description=meta["comment"],
        sourceColumn=field,
    ).model_dump()


def present(setting, fields):
    prop = dict(setting)
    col = fields.get(prop["sourceColumn"])
    if not col or not compatible(prop["type"], col["type"]):
        prop["sourceColumn"] = None
        col = None
    prop["sqlType"] = col["type"] if col else None
    prop.setdefault("description", col["comment"] if col else "")
    prop["primaryKeyPosition"] = col["primaryPosition"] if col else None
    if not col or not can_display(prop["presentation"], col["type"]):
        prop["presentation"] = "value"
    if not col or prop["visibility"] == "hidden":
        prop["summary"] = False
    if not format_allowed(prop["formatter"], prop["type"]):
        prop["formatter"] = "default"
    return prop


DISPLAY = ("description", "visibility", "summary", "group", "status", "formatter", "presentation")


async def resolve(session, user, layer, table, requested):
    schema, description, data_type, fields = await warehouse_asset(layer, table, user)
    bound = await source(session, user, schema, table, requested)
    source_id = str(bound[0].id) if bound else None
    key = (schema, table, source_id or "physical")
    stored = await session.get(ExplorerPropertyProfile, key, populate_existing=True)
    saved = stored.settings if stored else {}
    if bound:
        row, definition, allowed = bound
        overlays = saved.get("display", {})
        props = []
        for prop in definition.properties:
            if prop.id not in allowed:
                continue
            base = Property(
                id=prop.id,
                name=prop.name,
                type=prop.type,
                sourceColumn=prop.sourceColumn,
                description=fields.get(prop.sourceColumn, {}).get("comment", ""),
            ).model_dump()
            props.append(present({**base, **overlays.get(prop.id, {})}, fields))
        header = {
            "titleProperty": next((p["sourceColumn"] for p in props if p["id"] == definition.titleKey), None),
            "displayName": definition.name,
            "description": definition.description,
            "icon": definition.icon,
            "color": definition.color,
            "iconColor": definition.iconColor,
            "groups": saved.get("groups", []),
        }
    else:
        header = {
            "titleProperty": None,
            "displayName": table,
            "description": description,
            "icon": "",
            "color": "",
            "groups": [],
        }
        header.update(saved.get("header", {}))
        props = [present(p, fields) for p in saved.get("properties", [])]
        known = set(saved.get("knownColumns", []))
        used = {p["id"] for p in props}
        for field, meta in fields.items():
            if field not in known and field not in used:
                props.append(present(physical_property(field, meta), fields))
    pk = [
        c["name"]
        for c in sorted((c for c in fields.values() if c["primary"]), key=lambda c: c["primaryPosition"])
    ]
    if not user.is_superuser:
        props = [p for p in props if p["visibility"] != "hidden"]
    if not user.is_superuser or (bound and len(bound[2]) != len(bound[1].properties)):
        used_groups = {p["group"] for p in props}
        header["groups"] = [g for g in header["groups"] if g in used_groups]
    if not any(p["sourceColumn"] == header["titleProperty"] and p["visibility"] != "hidden" for p in props):
        header["titleProperty"] = None
    response = {
        "schema": schema,
        "table": table,
        "dataType": data_type,
        "sourceDefinitionId": source_id,
        "primaryKey": pk,
        **header,
        "properties": props,
    }
    signature = [
        response,
        str(stored.version) if stored else None,
        str(bound[0].published_at) if bound else None,
        [(c["name"], c["type"], c["primaryPosition"]) for c in fields.values()],
    ]
    response["version"] = hashlib.sha256(
        json.dumps(signature, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return response, key, stored, fields, bound


@router.get("/{layer}/{table}/property-mapping")
async def get_properties(
    layer: str,
    table: str,
    user: CurrentUser,
    session: SessionDep,
    response: Response,
    definitionId: UUID | None = None,
):
    response.headers["Cache-Control"] = "private, no-store"
    return (await resolve(session, user, layer, table, definitionId))[0]


def validate(body, fields, current, bound):
    ids = [p.id for p in body.properties]
    mapped = [p.sourceColumn for p in body.properties if p.sourceColumn is not None]
    if (
        len(set(ids)) != len(ids)
        or len(set(mapped)) != len(mapped)
        or len(set(body.groups)) != len(body.groups)
    ):
        raise HTTPException(422, "Property IDs, mapped source columns and group names must be unique")
    existing = {p["id"] for p in current["properties"]}
    for p in body.properties:
        if p.id not in existing and not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", p.id):
            raise HTTPException(422, "Invalid property identifier")
        col = fields.get(p.sourceColumn)
        if p.sourceColumn is not None and (not col or not compatible(p.type, col["type"])):
            raise HTTPException(422, "A mapped property must match its source column type")
        if p.group is not None and p.group not in body.groups:
            raise HTTPException(422, "Choose an existing property group")
        if not format_allowed(p.formatter, p.type) or (
            p.presentation != "value" and (not col or not can_display(p.presentation, col["type"]))
        ):
            raise HTTPException(422, "Formatter or presentation is incompatible with the property")
        if p.summary and (p.visibility == "hidden" or not col):
            raise HTTPException(422, "Summary properties must be visible and mapped")
    if body.titleProperty is not None and not any(
        p.sourceColumn == body.titleProperty and p.type != "json" and p.visibility != "hidden"
        for p in body.properties
    ):
        raise HTTPException(422, "Select one visible mapped scalar property as the title")
    if bound:
        if len(bound[2]) != len(bound[1].properties):
            raise HTTPException(403, "Full property clearance is required to configure a published type")
        identity = ("id", "name", "type", "sourceColumn")
        if any(
            getattr(body, k) != current[k]
            for k in ("displayName", "description", "icon", "color", "titleProperty")
        ) or [tuple(getattr(p, k) for k in identity) for p in body.properties] != [
            tuple(p[k] for k in identity) for p in current["properties"]
        ]:
            raise HTTPException(409, "Edit the published identity and property mappings in Model revisions")


@router.put("/{layer}/{table}/property-mapping")
async def save_properties(
    layer: str, table: str, body: Write, user: CurrentActiveSuperuser, session: SessionDep, response: Response
):
    current, key, stored, fields, bound = await resolve(session, user, layer, table, body.sourceDefinitionId)
    if current["version"] != body.version or current["sourceDefinitionId"] != (
        str(body.sourceDefinitionId) if body.sourceDefinitionId else None
    ):
        raise HTTPException(409, "The property mapping changed. Reload before saving.")
    validate(body, fields, current, bound)
    properties = []
    for p in body.properties:
        value = p.model_dump()
        if value["description"] == fields.get(p.sourceColumn, {}).get("comment", ""):
            value.pop("description")
        properties.append(value)
    if bound:
        settings = {
            "groups": body.groups,
            "display": {p["id"]: {k: p[k] for k in DISPLAY if k in p} for p in properties},
        }
    else:
        header = body.model_dump(
            include={"titleProperty", "displayName", "description", "icon", "color", "groups"}
        )
        previous_header = stored.settings.get("header", {}) if stored else {}
        if "description" not in previous_header and header["description"] == current["description"]:
            header.pop("description")
        settings = {
            "knownColumns": list(fields),
            "header": header,
            "properties": properties,
        }
    model = ExplorerPropertyProfile
    values = {"settings": settings, "version": uuid4()}
    if stored:
        stmt = (
            update(model)
            .where(
                model.schema_name == key[0],
                model.table_name == key[1],
                model.source_key == key[2],
                model.version == stored.version,
            )
            .values(**values)
        )
    else:
        stmt = (
            insert(model)
            .values(schema_name=key[0], table_name=key[1], source_key=key[2], **values)
            .on_conflict_do_nothing()
        )
    if await session.scalar(stmt.returning(model.version)) is None:
        raise HTTPException(409, "Another administrator changed the property mapping")
    saved = (await resolve(session, user, layer, table, body.sourceDefinitionId))[0]
    await session.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return saved
