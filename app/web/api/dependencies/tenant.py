from typing import Annotated, Union
import uuid
from fastapi import Depends, HTTPException
from sqlmodel import select
from sqlmodel.sql._expression_select_cls import SelectOfScalar

from app.web.api.models import Tenant, TeamUserJoin, Team, TenantUpdate, TenantCreate
from app.web.api.utils.models import StatusTypes
from app.web.core.config import settings
from .session import SessionDep
from .common import CurrentUser


async def instance_statement(current_user: CurrentUser) -> SelectOfScalar[Tenant]:
    statement = select(Tenant)
    if not current_user.is_superuser:
        accessible = select(Team.tenant_id).join(TeamUserJoin).where(
            TeamUserJoin.user_id == current_user.id,
            TeamUserJoin.status == StatusTypes.ENABLE,
        )
        statement = statement.where(Tenant.id.in_(accessible), Tenant.status == StatusTypes.ENABLE)
    return statement


async def current_instance(session: SessionDep, id: uuid.UUID, current_user: CurrentUser) -> Tenant:
    statement = (await instance_statement(current_user)).where(Tenant.id == id)
    tenant = await session.scalar(statement)
    if not tenant:
        raise HTTPException(status_code=404, detail="Organization not found")
    return tenant


async def validate_name_in(session: SessionDep, tenant_in: Union[TenantUpdate, TenantCreate], id: uuid.UUID | None = None) -> Tenant | None:
    if not tenant_in.name:
        return None
    if tenant_in.name in settings.PROTECTED_NAMES:
        raise HTTPException(status_code=409, detail="Name is a protected name. Choose another name.")
    statement = select(Tenant).where(Tenant.name == tenant_in.name)
    if id is not None:
        statement = statement.where(Tenant.id != id)
    return await session.scalar(statement)


async def validate_create_in(session: SessionDep, tenant_in: TenantCreate, current_user: CurrentUser) -> None:
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Only platform administrators can create organizations")
    if await validate_name_in(session, tenant_in):
        raise HTTPException(status_code=409, detail="Organization name already exists")


async def validate_update_in(session: SessionDep, tenant_in: TenantUpdate, id: uuid.UUID, current_user: CurrentUser) -> Tenant:
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Only platform administrators can update organizations")
    tenant = await current_instance(session, id, current_user)
    if await validate_name_in(session, tenant_in, id):
        raise HTTPException(status_code=409, detail="Organization name already exists")
    return tenant


InstanceStatement = Annotated[SelectOfScalar[Tenant], Depends(instance_statement)]
CurrentInstance = Annotated[Tenant, Depends(current_instance)]
ValidateCreateIn = Annotated[None, Depends(validate_create_in)]
ValidateUpdateOn = Annotated[Tenant, Depends(validate_update_in)]
