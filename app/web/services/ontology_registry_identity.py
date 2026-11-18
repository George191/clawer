"""Resolve policy identities from enabled project membership, not client claims."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.web.api.models import Team, TeamUserJoin, Tenant, User
from app.web.api.utils.models import StatusTypes
from app.web.services.ontology_registry_policy import AccessContext, Principal


class PrincipalOption(Principal):
    """An eligible project-scoped principal and its display label."""

    name: str


class PrincipalOptions(BaseModel):
    """A bounded list of assignable identities for an authorized project."""

    items: list[PrincipalOption]


async def _scope(session: AsyncSession, project_id: UUID) -> tuple[Team, Tenant] | None:
    result = await session.execute(
        select(Team, Tenant)
        .join(Tenant, Tenant.id == Team.tenant_id)
        .where(Team.id == project_id, Tenant.status == StatusTypes.ENABLE)
    )
    row = result.first()
    return (row[0], row[1]) if row else None


async def project_principals(
    session: AsyncSession,
    project_id: UUID,
    *,
    search: str = "",
    limit: int = 200,
) -> list[PrincipalOption]:
    """List active project members and the project's team and organization."""
    scope = await _scope(session, project_id)
    if not scope:
        return []
    team, tenant = scope
    options = [
        PrincipalOption(kind="group", id=team.id, name=team.name),
        PrincipalOption(kind="organization", id=tenant.id, name=tenant.name),
    ]
    statement = (
        select(User)
        .join(TeamUserJoin, TeamUserJoin.user_id == User.id)
        .where(
            TeamUserJoin.team_id == project_id,
            TeamUserJoin.status == StatusTypes.ENABLE,
            User.status == StatusTypes.ENABLE,
        )
    )
    if search:
        pattern = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(User.full_name.ilike(f"%{pattern}%", escape="\\"))
        options = [option for option in options if search.casefold() in option.name.casefold()]
    users = await session.scalars(statement.order_by(User.full_name, User.id).limit(limit))
    options.extend(
        PrincipalOption(kind="user", id=user.id, name=user.full_name or str(user.id))
        for user in users
    )
    return options


async def eligible_principals(
    session: AsyncSession,
    project_id: UUID,
    principals: set[Principal],
) -> set[Principal]:
    """Resolve exact saved references without relying on the picker page limit."""
    if not principals:
        return set()
    scope = await _scope(session, project_id)
    if not scope:
        return set()
    team, tenant = scope
    allowed = {Principal(kind="group", id=team.id), Principal(kind="organization", id=tenant.id)}
    ids = [p.id for p in principals if p.kind == "user"]
    if ids:
        members = await session.scalars(
            select(User.id)
            .join(TeamUserJoin, TeamUserJoin.user_id == User.id)
            .where(
                TeamUserJoin.team_id == project_id,
                TeamUserJoin.status == StatusTypes.ENABLE,
                User.status == StatusTypes.ENABLE,
                User.id.in_(ids),
            )
        )
        allowed.update(Principal(kind="user", id=member) for member in members)
    return allowed.intersection(principals)


async def project_access_context(
    session: AsyncSession,
    user: User,
    project_id: UUID,
    source_access: bool,
    *,
    lock_membership: bool = False,
) -> AccessContext:
    """Calculate read/submit identity for actual or simulated user, without admin policy bypass."""
    if user.status != StatusTypes.ENABLE:
        return AccessContext(frozenset(), False, False)
    statement = (
        select(Team.tenant_id)
        .select_from(TeamUserJoin)
        .join(User, User.id == TeamUserJoin.user_id)
        .join(Team, Team.id == TeamUserJoin.team_id)
        .join(Tenant, Tenant.id == Team.tenant_id)
        .where(
            TeamUserJoin.user_id == user.id,
            TeamUserJoin.team_id == project_id,
            TeamUserJoin.status == StatusTypes.ENABLE,
            User.status == StatusTypes.ENABLE,
            Tenant.status == StatusTypes.ENABLE,
        )
    )
    if lock_membership:
        # Keep every row proving this grant valid until the action and audit event commit.
        statement = statement.with_for_update(read=True)
    tenant_id = await session.scalar(statement)
    if tenant_id is None:
        return AccessContext(frozenset(), False, False)
    principals = frozenset(
        (
            Principal(kind="user", id=user.id),
            Principal(kind="group", id=project_id),
            Principal(kind="organization", id=tenant_id),
        )
    )
    return AccessContext(principals, True, bool(source_access))
