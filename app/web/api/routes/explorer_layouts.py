"""Personal and shared layouts for physical Explorer results."""

import re
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from app.web.api.models.explorer_layouts import ExplorerResultLayout

from app.web.api.dependencies.common import CurrentUser
from app.web.api.dependencies.session import SessionDep
from app.web.services.etl_layers import get_etl_layers


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


Name = Annotated[str, Field(min_length=1, max_length=63)]


class ColumnLayout(StrictModel):
    field: Name
    visible: bool = Field(strict=True)
    width: int = Field(default=180, ge=80, le=1200, strict=True)
    presentation: Literal["value", "timeSeries", "geoJson", "media"] = "value"


class ColumnSort(StrictModel):
    field: Name
    direction: Literal["asc", "desc"]


class Layout(StrictModel):
    columns: list[ColumnLayout] = Field(max_length=1600)
    sorts: list[ColumnSort] = Field(max_length=20)
    wrap: bool = Field(strict=True)

    @model_validator(mode="after")
    def distinct_fields(self):
        for group in (self.columns, self.sorts):
            if len(set(item.field for item in group)) != len(group):
                raise ValueError("Each column may appear only once")
        return self


class LayoutWrite(StrictModel):
    version: UUID | None
    layout: Layout | None


router = APIRouter(prefix="/etl", tags=["Explorer layouts"])


async def asset(layer, table, user):
    warehouse = get_etl_layers()
    target = await warehouse.runtime_table(layer, table, include_hidden=user.is_superuser)
    columns = await warehouse.columns(target["schemaName"], table)
    return target["schemaName"], {column["name"]: column["type"] for column in columns}


def can_display(mode: str, sql_type: str) -> bool:
    if mode == "value" or sql_type in {"json", "jsonb"}:
        return True
    return mode == "media" and bool(re.fullmatch(r"text|(?:varchar|character varying)(?:\(\d+\))?", sql_type))


def reconcile(layout, fields):
    if layout is None:
        return None
    visible = set(fields)
    columns = [
        {
            **col,
            "presentation": (
                col["presentation"] if can_display(col["presentation"], fields[col["field"]]) else "value"
            ),
        }
        for col in layout["columns"]
        if col["field"] in visible
    ]
    known = {col["field"] for col in columns}
    columns.extend(
        ColumnLayout(field=name, visible=True).model_dump() for name in fields if name not in known
    )
    return {
        **layout,
        "columns": columns,
        "sorts": [sort for sort in layout["sorts"] if sort["field"] in visible],
    }


@router.get("/{layer}/{table}/result-layout")
async def read_layout(layer: str, table: str, session: SessionDep, user: CurrentUser, response: Response):
    schema, fields = await asset(layer, table, user)
    model = ExplorerResultLayout
    rows = await session.scalars(
        select(model).where(
            model.schema_name == schema,
            model.table_name == table,
            (model.owner_id == user.id) | model.owner_id.is_(None),
        )
    )
    result = {which: {"version": None, "layout": None} for which in ("personal", "default")}
    for row in rows:
        result["default" if row.owner_id is None else "personal"] = {
            "version": row.version,
            "layout": reconcile(row.layout, fields),
        }
    response.headers["Cache-Control"] = "private, no-store"
    return result


@router.put("/{layer}/{table}/result-layout/{scope}")
async def write_layout(
    layer: str,
    table: str,
    scope: Literal["personal", "default"],
    body: LayoutWrite,
    session: SessionDep,
    user: CurrentUser,
    response: Response,
):
    shared = scope == "default"
    if shared and not user.is_superuser:
        raise HTTPException(403, "Only administrators can change the table default")
    schema, fields = await asset(layer, table, user)
    layout = body.layout
    if layout is not None:
        saved_fields = {col.field for col in layout.columns}
        if saved_fields != set(fields) or any(sort.field not in saved_fields for sort in layout.sorts):
            raise HTTPException(409, "Table columns have changed. Reload the layout.")
        if any(not can_display(c.presentation, fields[c.field]) for c in layout.columns):
            raise HTTPException(422, "The selected presentation does not support this column type.")
    model = ExplorerResultLayout
    owner = None if shared else user.id
    version = uuid4()
    values = {"version": version, "layout": layout.model_dump() if layout else None}
    if body.version is None:
        statement = (
            insert(model)
            .values(id=uuid4(), schema_name=schema, table_name=table, owner_id=owner, **values)
            .on_conflict_do_nothing()
        )
    else:
        statement = (
            update(model)
            .where(
                model.schema_name == schema,
                model.table_name == table,
                model.owner_id == owner,
                model.version == body.version,
            )
            .values(**values)
        )
    if (await session.scalar(statement.returning(model.id))) is None:
        raise HTTPException(409, "This layout changed in another session. Reload the layout.")
    await session.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return values
