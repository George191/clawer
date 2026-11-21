"""Published object-set handoff and saved work, independent of physical Explorer."""

import json
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import delete, select, update

from app.web.api.dependencies import CurrentUser, SessionDep
from app.web.api.models.ontology_explorer import ExplorerQuery, OntologyExplorerSavedItem as Item, SaveExplorerItem, ShareExplorerItem
from app.web.services.ontology_action_runtime import _definition_access
from app.web.services.ontology_explorer import evaluate, validate_query
from app.web.api.routes.ontology_explorer_details import router as details_router
from app.web.api.routes.ontology_explorer_layouts import router as layouts_router

router = APIRouter(prefix="/ontology/v1/object-types/{definition_id}/explorer", tags=["Published Explorer"])
router.include_router(details_router)
router.include_router(layouts_router)
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0, le=100_000)]
Kind = Literal["exploration", "list"]


def metadata(item):
    return {"id": item.id, "kind": item.kind, "name": item.name, "version": item.version, "shared": item.shared}


async def require_visible_keys(session, user, definition_id, query):
    if query.keys:
        visible = await evaluate(session, user, definition_id, ExplorerQuery(keys=query.keys), 1, 0)
        if visible["count"] != len(query.keys):
            raise HTTPException(403, "Saved work references unavailable objects")


@router.post("/query")
async def query_objects(definition_id: UUID, payload: ExplorerQuery, session: SessionDep, user: CurrentUser, response: Response, limit: Limit = 25, offset: Offset = 0, groupOffset: Offset = 0):
    response.headers["Cache-Control"] = "private, no-store"
    return await evaluate(session, user, definition_id, payload, limit, offset, groupOffset)


@router.get("/saved-items")
async def list_items(definition_id: UUID, kind: Kind, session: SessionDep, user: CurrentUser, response: Response, offset: int = Query(0, ge=0, le=10000), search: str = Query("", max_length=100), sharing: Literal["all", "private", "shared"] = "all"):
    await _definition_access(session, user, definition_id)
    conditions = [Item.definition_id == definition_id, Item.owner_id == user.id, Item.kind == kind]
    if search:
        conditions.append(Item.name.icontains(search, autoescape=True))
    if sharing != "all":
        conditions.append(Item.shared.is_(sharing == "shared"))
    items = (await session.scalars(select(Item).where(*conditions).order_by(Item.name, Item.id).offset(offset).limit(26))).all()
    response.headers["Cache-Control"] = "private, no-store"
    return {"items": [metadata(item) for item in items[:25]], "hasMore": len(items) > 25}


@router.post("/saved-items", status_code=201)
async def create_item(definition_id: UUID, payload: SaveExplorerItem, session: SessionDep, user: CurrentUser):
    grant = await _definition_access(session, user, definition_id)
    content = payload.content.model_dump(mode="json", exclude_none=True)
    if not payload.name.strip() or len(json.dumps(content).encode()) > 65536:
        raise HTTPException(422, "Name and content must fit the supported bounds")
    if payload.kind == "list":
        keys = payload.content.keys
        if not keys:
            raise HTTPException(422, "Select objects to save a list")
        if payload.content.query != ExplorerQuery():
            raise HTTPException(422, "A list stores object identities only")
        query = ExplorerQuery(keys=keys)
        validate_query(grant, query)
        result = await evaluate(session, user, definition_id, query, 1, 0)
        if result["count"] != len(keys):
            raise HTTPException(422, "A list contains unavailable objects")
        content = {"keys": keys}
    else:
        if payload.content.keys is not None:
            raise HTTPException(422, "An exploration stores its query")
        validate_query(grant, payload.content.query)
        if payload.content.chart and payload.content.chart not in grant[3]:
            raise HTTPException(403, "Chart references an unavailable property")
        await require_visible_keys(session, user, definition_id, payload.content.query)
    item = Item(definition_id=definition_id, owner_id=user.id, kind=payload.kind, name=payload.name.strip(), content=content)
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return metadata(item)


@router.get("/saved-items/{item_id}")
async def open_item(definition_id: UUID, item_id: UUID, session: SessionDep, user: CurrentUser, response: Response):
    grant = await _definition_access(session, user, definition_id)
    item = await session.scalar(select(Item).where(Item.definition_id == definition_id, Item.id == item_id, (Item.owner_id == user.id) | Item.shared.is_(True)))
    if item is None:
        raise HTTPException(404, "Saved item not found")
    query = ExplorerQuery(keys=item.content["keys"]) if item.kind == "list" else ExplorerQuery.model_validate(item.content["query"])
    validate_query(grant, query)
    if item.content.get("chart") and item.content["chart"] not in grant[3]:
        raise HTTPException(403, "Chart references an unavailable property")
    await require_visible_keys(session, user, definition_id, query)
    response.headers["Cache-Control"] = "private, no-store"
    return {**metadata(item), "content": item.content, "owned": item.owner_id == user.id}


@router.put("/saved-items/{item_id}/sharing")
async def share_item(definition_id: UUID, item_id: UUID, payload: ShareExplorerItem, session: SessionDep, user: CurrentUser):
    grant = await _definition_access(session, user, definition_id)
    if payload.shared:
        current = await session.scalar(select(Item).where(Item.definition_id == definition_id, Item.id == item_id, Item.owner_id == user.id, Item.version == payload.version))
        if current is None:
            raise HTTPException(409, "Saved item changed or is unavailable")
        query = ExplorerQuery(keys=current.content["keys"]) if current.kind == "list" else ExplorerQuery.model_validate(current.content["query"])
        validate_query(grant, query)
        if current.content.get("chart") and current.content["chart"] not in grant[3]:
            raise HTTPException(403, "Chart references an unavailable property")
        await require_visible_keys(session, user, definition_id, query)
    item = await session.scalar(update(Item).where(Item.definition_id == definition_id, Item.id == item_id, Item.owner_id == user.id, Item.version == payload.version).values(version=uuid4(), shared=payload.shared).returning(Item))
    if item is None:
        raise HTTPException(409, "Saved item changed or is unavailable")
    result = metadata(item)
    await session.commit()
    return result


@router.delete("/saved-items/{item_id}")
async def delete_item(definition_id: UUID, item_id: UUID, version: UUID, session: SessionDep, user: CurrentUser):
    await _definition_access(session, user, definition_id)
    changed = await session.scalar(delete(Item).where(Item.definition_id == definition_id, Item.id == item_id, Item.owner_id == user.id, Item.version == version).returning(Item.id))
    if changed is None:
        raise HTTPException(409, "Saved item changed or is unavailable")
    await session.commit()
    return {"deleted": True}
