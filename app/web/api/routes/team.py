from typing import Any
import uuid
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert
from fastapi import HTTPException

from fastapi import APIRouter

from app.web.api.dependencies import (
    SessionDep, CurrentInstanceTeam,
    ValidateCreateInTeam, ValidateUpdateOnTeam, CurrentUser, InstanceStatementTeam
)
from app.web.api.models import (
    InviteCodes,
    Message,
    RoleTypes,
    Team,
    TeamCreate,
    TeamOut,
    TeamUpdate,
    TeamUserJoin,
)
from app.web.api.utils.models import StatusTypes
from fastapi_pagination.ext.sqlmodel import apaginate
from fastapi_pagination.links import Page

from fastapi_filter import FilterDepends

from ..filters import TeamFilter
from app.web.api.dependencies.team import ManageInstance


router = APIRouter(
    prefix="/team", tags=["team"], 
)

@router.get("/", response_model=Page[TeamOut])
async def read_teams(
    session: SessionDep, statement: InstanceStatementTeam, 
    team_filter: TeamFilter = FilterDepends(TeamFilter)
) -> Any:
    """
    List of teams
    """
    statement = team_filter.filter(statement)
    statement = team_filter.sort(statement)
    return await apaginate(session, statement)


@router.get("/{id}", response_model=TeamOut)
async def read_team(team: CurrentInstanceTeam) -> Any:
    """
    Get team by ID.
    """
    return team


@router.post("/", response_model=TeamOut)
async def create_team(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    tenant_id: uuid.UUID,
    team_in: TeamCreate,
    _: ValidateCreateInTeam
) -> Team:
    """
    Create new team and it's team leader
    """
    team = await session.scalar(
        insert(Team).values(**team_in.model_dump(), tenant_id=tenant_id)
        .on_conflict_do_nothing(index_elements=[Team.name]).returning(Team)
    )
    if team is None:
        raise HTTPException(409, "Team name already exists")
    invite = InviteCodes(code=uuid.uuid4().hex, status=StatusTypes.DISABLE)
    session.add(invite)
    await session.flush()
    session.add(TeamUserJoin(
        team_id=team.id, user_id=current_user.id, role=RoleTypes.OWNER,
        invite_by=current_user.id, invite_code=invite.id,
    ))
    await session.commit()
    await session.refresh(team)
    return team



@router.put("/{id}", response_model=TeamOut)
async def update_team(
    session: SessionDep,
    team_in: TeamUpdate,
    team: ValidateUpdateOnTeam,
) -> Any:
    """
    Update a team.
    """
    team.sqlmodel_update(team_in)
    session.add(team)
    await session.commit()
    await session.refresh(team)
    return team


@router.delete("/{id}")
async def delete_team(session: SessionDep, team: ManageInstance) -> Any:
    """
    Delete a team.
    """
    try:
        await session.delete(team)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="This team is referenced by members or other resources and cannot be deleted")
    return Message(message="Team deleted successfully")
