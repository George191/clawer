from typing import Annotated, Union
import uuid
from fastapi import Depends, HTTPException
from sqlmodel import select
from sqlmodel.sql._expression_select_cls import SelectOfScalar

from app.web.api.models import TeamCreate, Team, TeamUpdate, TeamUserJoin, Tenant, RoleTypes
from app.web.api.utils.models import StatusTypes
from app.web.core.config import settings
from .session import SessionDep
from .common import CurrentUser


async def instance_statement(current_user: CurrentUser, tenant_id: uuid.UUID) -> SelectOfScalar[Team]:
    statement = select(Team).where(Team.tenant_id == tenant_id)
    if not current_user.is_superuser:
        statement = statement.join(Tenant).where(Tenant.status == StatusTypes.ENABLE)
        if current_user.is_tenant_admin:
            accessible = select(Team.tenant_id).join(TeamUserJoin).where(
                TeamUserJoin.user_id == current_user.id,
                TeamUserJoin.status == StatusTypes.ENABLE,
            )
            statement = statement.where(Team.tenant_id.in_(accessible))
        else:
            statement = statement.join(TeamUserJoin).where(
                TeamUserJoin.user_id == current_user.id,
                TeamUserJoin.status == StatusTypes.ENABLE,
            )
    return statement


async def current_instance(session: SessionDep, id: uuid.UUID, current_user: CurrentUser, tenant_id: uuid.UUID) -> Team:
    statement = (await instance_statement(current_user, tenant_id)).where(Team.id == id)
    team = await session.scalar(statement)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


async def manage_instance(session: SessionDep, id: uuid.UUID, current_user: CurrentUser, tenant_id: uuid.UUID) -> Team:
    team = await current_instance(session, id, current_user, tenant_id)
    if not (current_user.is_superuser or current_user.is_tenant_admin):
        role = await session.scalar(select(TeamUserJoin.role).where(
            TeamUserJoin.user_id == current_user.id, TeamUserJoin.team_id == team.id,
            TeamUserJoin.status == StatusTypes.ENABLE,
        ))
        if role not in (RoleTypes.ADMIN, RoleTypes.OWNER):
            raise HTTPException(status_code=403, detail="Only team administrators or owners can manage this team")
    return team


async def validate_name_in(session: SessionDep, team_in: Union[TeamUpdate, TeamCreate], id: uuid.UUID | None = None) -> Team | None:
    if not team_in.name:
        return None
    if team_in.name in settings.PROTECTED_NAMES:
        raise HTTPException(status_code=409, detail="Name is a protected name. Choose another name.")
    statement = select(Team).where(Team.name == team_in.name)
    if id is not None:
        statement = statement.where(Team.id != id)
    return await session.scalar(statement)


async def validate_create_in(session: SessionDep, team_in: TeamCreate, current_user: CurrentUser, tenant_id: uuid.UUID) -> None:
    tenant = await session.get(Tenant, tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not current_user.is_superuser:
        if not current_user.is_tenant_admin or not await session.scalar(await instance_statement(current_user, tenant_id)):
            raise HTTPException(status_code=403, detail="Only administrators of this organization can create teams")
    if await validate_name_in(session, team_in):
        raise HTTPException(status_code=409, detail="Team name already exists")


async def validate_update_in(session: SessionDep, team_in: TeamUpdate, id: uuid.UUID, current_user: CurrentUser, tenant_id: uuid.UUID) -> Team:
    team = await manage_instance(session, id, current_user, tenant_id)
    if await validate_name_in(session, team_in, id):
        raise HTTPException(status_code=409, detail="Team name already exists")
    return team


InstanceStatement = Annotated[SelectOfScalar[Team], Depends(instance_statement)]
CurrentInstance = Annotated[Team, Depends(current_instance)]
ManageInstance = Annotated[Team, Depends(manage_instance)]
ValidateCreateIn = Annotated[None, Depends(validate_create_in)]
ValidateUpdateOn = Annotated[Team, Depends(validate_update_in)]
