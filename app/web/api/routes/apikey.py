from typing import Any

from fastapi import APIRouter, Query, Response
from uuid import UUID
from app.web.api.dependencies.common import CurrentActiveSuperuser
from app.web.services.etl_workspace import get_etl_workspace

from app.web.core.security import generate_apikey, generate_short_apikey, get_password_hash

from app.web.api.models import ApiKey, ApiKeyCreate, ApiKeyOut, ApiKeyOutPublic, Message, ApiKeyUpdate
from app.web.api.dependencies import SessionDep, CurrentTeamAndUser, CurrentInstanceApiKey, InstanceStatementApiKey

from fastapi_pagination.ext.sqlmodel import apaginate
from fastapi_pagination.links import Page

from fastapi_filter import FilterDepends

from ..filters import ApiKeyFilter


router = APIRouter(
    prefix="/apikey",
    tags=["apikey"],
)


@router.get("/publications")
async def published_keys(
    _: CurrentActiveSuperuser,
    q: str = Query("", max_length=200),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1_000_000),
):
    return await get_etl_workspace().publication_keys(q.strip(), limit, offset)


@router.post("/publications/{publication_id}/rotate")
async def rotate_publication_key(publication_id: UUID, _: CurrentActiveSuperuser, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await get_etl_workspace().change_publication_key(str(publication_id), revoke=False)


@router.delete("/publications/{publication_id}/key")
async def revoke_publication_key(publication_id: UUID, _: CurrentActiveSuperuser):
    return await get_etl_workspace().change_publication_key(str(publication_id), revoke=True)


@router.get("/{team_id}", response_model=Page[ApiKeyOutPublic])
async def read_api_key(
    session: SessionDep,
    statement: InstanceStatementApiKey,
    api_key_filter: ApiKeyFilter = FilterDepends(ApiKeyFilter)
) -> Any:
    """Read api keys"""
    statement = api_key_filter.filter(statement)
    statement = api_key_filter.sort(statement)
    statement = statement.order_by(ApiKey.created_at.desc(), ApiKey.id)
    return await apaginate(session, statement)


@router.post("/", response_model=ApiKeyOut)
async def create_api_key(
    session: SessionDep,
    current_team_and_user: CurrentTeamAndUser,
    apikey_in: ApiKeyCreate,
    response: Response,
) -> Any:
    
    # Generate API key and hash it
    key = generate_apikey()
    
    # Create the API key object
    apikey = ApiKey.model_validate(apikey_in, update={
        "hashed_key": get_password_hash(key),
        "short_key": generate_short_apikey(key),
        "team_id": current_team_and_user.team.id,
        "owner_id": current_team_and_user.user.id
    })
    # Save the new API key to the database
    session.add(apikey)
    await session.commit()
    await session.refresh(apikey)
    
    response.headers["Cache-Control"] = "no-store"
    return ApiKeyOut(key=key, **apikey.model_dump())


@router.put("/", response_model=ApiKeyOutPublic)
async def update_api_key(
    session: SessionDep,
    apikey: CurrentInstanceApiKey,
    apikey_in: ApiKeyUpdate,
) -> Any:
    """
    Update ApiKey by ID.
    """        
    apikey.sqlmodel_update(apikey_in)
    session.add(apikey)
    await session.commit()
    await session.refresh(apikey)
    return apikey


@router.post("/{id}/rotate", response_model=ApiKeyOut)
async def rotate_team_key(session: SessionDep, apikey: CurrentInstanceApiKey, response: Response):
    key = generate_apikey()
    apikey.hashed_key = get_password_hash(key)
    apikey.short_key = generate_short_apikey(key)
    await session.commit()
    await session.refresh(apikey)
    response.headers["Cache-Control"] = "no-store"
    return ApiKeyOut(key=key, **apikey.model_dump())


@router.delete("/{id}")
async def delete_api_key(
    session: SessionDep, apikey: CurrentInstanceApiKey
) -> Any:
    """Delete API key for a team."""

    await session.delete(apikey)
    await session.commit()
    return Message(message="Api key deleted successfully")
