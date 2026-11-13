from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from app.web.api.dependencies import (
    SessionDep,
    CurrentInstanceTenant, InstanceStatementTenant,
    ValidateCreateInTenant, ValidateUpdateTenant
)
from app.web.api.models import (
    Message,
    TenantUpdate,
    Tenant,
    TenantCreate,
    TenantOut,
)
from fastapi_pagination.ext.sqlmodel import apaginate
from fastapi_pagination.links import Page

from fastapi_filter import FilterDepends

from ..filters import TenantFilter
from app.web.api.dependencies.common import get_current_active_superuser


router = APIRouter(
    prefix="/tenant", tags=["tenant"], 
)

@router.get(
    "/",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=Page[TenantOut]
)
async def read_tenants(
    session: SessionDep, 
    statement: InstanceStatementTenant,
    tenant_filter: TenantFilter = FilterDepends(TenantFilter)
) -> Any:
    """
    Retrieve tenants.
    """
    statement = tenant_filter.filter(statement)
    statement = tenant_filter.sort(statement)
    return await apaginate(session, statement)


@router.post(
    "/", response_model=TenantOut
)
async def create_tenant(
    *, 
    session: SessionDep, 
    tenant_in: TenantCreate,
    _: ValidateCreateInTenant
) -> Any:
    """
    Create new tenant.
    """
    tenant: Tenant = Tenant.model_validate(tenant_in)
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)
    return tenant



@router.get("/{id}", response_model=TenantOut)
async def read_tenant_by_id(
    tenant: CurrentInstanceTenant
) -> Any:
    """Get a specific tenant by id."""
    return tenant


@router.patch(
    "/{id}",
    response_model=TenantOut,
)
async def update_user(
    *,
    session: SessionDep,
    tenant: ValidateUpdateTenant,
    tenant_in: TenantUpdate,
) -> Any:
    """
    Update a Tenant.
    """
    tenant.sqlmodel_update(tenant_in)
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)
    return tenant


@router.delete("/{id}", dependencies=[Depends(get_current_active_superuser)])
async def delete_user(
    session: SessionDep,
    tenant: CurrentInstanceTenant
) -> Message:
    """
    Delete a Tenant.
    """
    try:
        await session.delete(tenant)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="This organization contains teams or other resources and cannot be deleted")
    return Message(message="Organization deleted successfully")
