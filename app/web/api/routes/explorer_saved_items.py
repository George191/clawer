"""Private saved explorations and lists with opt-in, permission-checked sharing."""

import json
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update

from app.web.api.dependencies.common import CurrentUser
from app.web.api.dependencies.session import SessionDep
from app.web.api.models.explorer_saved_items import ExplorerSavedItem as Item
from app.web.api.routes.explorer_layouts import asset, ColumnSort
from app.web.services.etl_layers import get_etl_layers

router = APIRouter(prefix="/etl", tags=["Explorer saved items"])
Kind = Literal["exploration", "list"]


class SavedFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=160)
    field: str = Field(max_length=63)
    operator: str = Field(min_length=1, max_length=64)
    value: str = Field(max_length=4096)
    active: bool
    join: Literal["and", "or"]


class ItemContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search: str = Field(default="", max_length=1000)
    filters: list[SavedFilter] = Field(default_factory=list, max_length=100)
    sorts: list[ColumnSort] = Field(default_factory=list, max_length=20)
    partition: str | None = Field(None, max_length=63)
    chart: str | None = Field(None, max_length=63)
    view: Literal["explore", "results"] = "results"
    keys: list[dict[str, str]] = Field(default_factory=list, max_length=100)


class SaveItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind
    name: str = Field(min_length=1, max_length=100)
    content: ItemContent


class ShareItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: UUID
    shared: bool


def scope(schema, table):
    return Item.schema_name == schema, Item.table_name == table


def metadata(item):
    return {
        "id": item.id,
        "kind": item.kind,
        "name": item.name,
        "version": item.version,
        "shared": item.shared,
    }


@router.get("/{layer}/{table}/saved-items")
async def list_items(
    layer: str,
    table: str,
    kind: Kind,
    user: CurrentUser,
    session: SessionDep,
    response: Response,
    offset: int = Query(0, ge=0, le=10000),
):
    schema, _ = await asset(layer, table, user)
    result = await session.scalars(
        select(Item)
        .where(*scope(schema, table), Item.owner_id == user.id, Item.kind == kind)
        .order_by(Item.name, Item.id)
        .offset(offset)
        .limit(26)
    )
    rows = result.all()
    response.headers["Cache-Control"] = "private, no-store"
    return {"items": [metadata(r) for r in rows[:25]], "hasMore": len(rows) > 25}


@router.post("/{layer}/{table}/saved-items", status_code=201)
async def save_item(layer: str, table: str, body: SaveItem, user: CurrentUser, session: SessionDep):
    schema, fields = await asset(layer, table, user)
    content = body.content.model_dump()
    if not body.name.strip() or len(json.dumps(content).encode()) > 65536:
        raise HTTPException(422, "Name and content must fit the supported bounds")
    if body.kind == "list":
        primary = {c["name"] for c in await get_etl_layers().columns(schema, table) if c["primary"]}
        keys = body.content.keys
        if not primary or not keys or any(set(key) != primary for key in keys):
            raise HTTPException(422, "A list requires complete primary keys from this table")
        if len({json.dumps(k, sort_keys=True) for k in keys}) != len(keys):
            raise HTTPException(422, "A list cannot contain duplicate objects")
        content = {"keys": keys}
    else:
        if (
            content.pop("keys")
            or any(s.field not in fields for s in body.content.sorts)
            or (body.content.chart and body.content.chart not in fields)
        ):
            raise HTTPException(422, "The exploration does not match this table")
        if any(rule.field and rule.field not in fields for rule in body.content.filters):
            raise HTTPException(422, "A filter references an unknown property")
    item = Item(
        schema_name=schema,
        table_name=table,
        owner_id=user.id,
        name=body.name.strip(),
        kind=body.kind,
        content=content,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return metadata(item)


@router.get("/{layer}/{table}/saved-items/{item_id}")
async def open_item(
    layer: str,
    table: str,
    item_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    response: Response,
):
    schema, _ = await asset(layer, table, user)
    row = await session.scalar(
        select(Item).where(
            *scope(schema, table),
            Item.id == item_id,
            (Item.owner_id == user.id) | Item.shared.is_(True),
        )
    )
    if row is None:
        raise HTTPException(404, "Saved item not found")
    response.headers["Cache-Control"] = "private, no-store"
    return {**metadata(row), "content": row.content, "owned": row.owner_id == user.id}


@router.put("/{layer}/{table}/saved-items/{item_id}/sharing")
async def change_sharing(
    layer: str, table: str, item_id: UUID, body: ShareItem, user: CurrentUser, session: SessionDep
):
    schema, _ = await asset(layer, table, user)
    row = await session.scalar(
        update(Item)
        .where(
            *scope(schema, table),
            Item.id == item_id,
            Item.owner_id == user.id,
            Item.version == body.version,
        )
        .values(version=uuid4(), shared=body.shared)
        .returning(Item)
    )
    if row is None:
        raise HTTPException(409, "Saved item changed or is unavailable")
    result = metadata(row)
    await session.commit()
    return result


@router.delete("/{layer}/{table}/saved-items/{item_id}")
async def delete_item(
    layer: str, table: str, item_id: UUID, version: UUID, user: CurrentUser, session: SessionDep
):
    schema, _ = await asset(layer, table, user)
    changed = await session.scalar(
        delete(Item)
        .where(
            *scope(schema, table),
            Item.id == item_id,
            Item.owner_id == user.id,
            Item.version == version,
        )
        .returning(Item.id)
    )
    if not changed:
        raise HTTPException(409, "Saved item changed or is unavailable")
    await session.commit()
    return {"deleted": True}
