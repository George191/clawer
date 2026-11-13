
# We'll declare the type hint annotation here but define the actual function in team.py
# This breaks the circular dependency

from typing import Annotated, AsyncGenerator
from app.web.core.storage.s3 import StorageClient

import uuid
from fastapi.security import OAuth2PasswordBearer
from pydantic import ValidationError
from sqlmodel import SQLModel, select
from fastapi import Depends, HTTPException, status
from app.web.api.dependencies.session import SessionDep
from app.web.api.utils.models import StatusTypes
from app.web.core import security
from app.web.core.config import settings
from app.web.api.models import Team, TeamUserJoin, Tenant, User, TokenPayload
from jwt.exceptions import InvalidTokenError


reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/token"
)

TokenDep = Annotated[str, Depends(reusable_oauth2)]


async def get_current_user(session: SessionDep, token: TokenDep) -> User:
    try:
        payload = security.decode_session_token(token, security.TokenType.ACCESS_TOKEN)
        token_data = TokenPayload(**payload)

        if token_data.type != security.TokenType.ACCESS_TOKEN.value:
            raise InvalidTokenError

    except (InvalidTokenError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await session.get(User, token_data.sub)
    if not user:
        raise HTTPException(status_code=401, detail="Could not validate credentials")
    if user.status != StatusTypes.ENABLE:
        raise HTTPException(status_code=401, detail="Inactive user")
    return user


CurrentUser = Annotated[User, Depends(dependency=get_current_user)]


class TeamAndUser(SQLModel):
    team: Team
    user: User


async def get_current_team_and_user(
    session: SessionDep,
    team_id: uuid.UUID,
    current_user: CurrentUser,
) -> TeamAndUser:
    """Resolve a team only within the current user's accessible tenant scope."""
    if not (team := await session.get(Team, team_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    if not current_user.is_superuser:
        membership = select(TeamUserJoin).join(Team).join(Tenant).where(
            TeamUserJoin.user_id == current_user.id,
            TeamUserJoin.status == StatusTypes.ENABLE,
            Team.tenant_id == team.tenant_id,
            Tenant.status == StatusTypes.ENABLE,
        )
        if not current_user.is_tenant_admin:
            membership = membership.where(TeamUserJoin.team_id == team.id)
        if not await session.scalar(membership):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this team")
    return TeamAndUser(team=team, user=current_user)


CurrentTeamAndUser = Annotated[TeamAndUser, Depends(get_current_team_and_user)]


def get_current_active_superuser(current_user: CurrentUser) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=403, detail="The user doesn't have enough privileges"
        )
    return current_user


CurrentActiveSuperuser = Annotated[User, Depends(get_current_active_superuser)]


async def get_storage_client() -> AsyncGenerator[StorageClient, None]:
    async with StorageClient() as client:
        yield client


StorageClientDep = Annotated[StorageClient, Depends(get_storage_client)]
