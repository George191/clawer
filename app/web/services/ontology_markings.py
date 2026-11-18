"""Shared marking clearance for catalogs, runtime operations and release review."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import String, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from app.web.api.models import Team
from app.web.api.models.ontology_markings import OntologyMarking as Marking
from app.web.services.ontology_registry_identity import project_access_context
from app.web.services.ontology_registry_policy import Principal


def allowed_ids(project_id, principals):
    """A correlated JSON array of all clearances held in this specific project."""
    empty = literal([], type_=JSONB)
    readers = [
        Marking.readers.contains(
            func.jsonb_build_array(
                func.jsonb_build_object(
                    "kind",
                    kind,
                    "id",
                    cast(str(identity) if isinstance(identity, UUID) else identity, String),
                )
            )
        )
        for kind, identity in principals
    ]
    return (
        select(func.coalesce(func.jsonb_agg(cast(Marking.id, String)), empty))
        .where(Marking.project_id == project_id, or_(*readers))
        .correlate_except(Marking)
        .scalar_subquery()
    )


def covers(required, allowed):
    return func.coalesce(required, literal([], type_=JSONB)).op("<@")(allowed)


def allowed_for(user_id, project_id):
    tenant = (
        select(Team.tenant_id).where(Team.id == project_id).correlate_except(Team).scalar_subquery()
    )
    return allowed_ids(
        project_id, [("user", user_id), ("group", project_id), ("organization", tenant)]
    )


async def cleared(session, project_id, required, context):
    if not required:
        return True
    if not context.project_access:
        return False
    ids = {UUID(str(value)) for value in required}
    rows = (
        await session.scalars(
            select(Marking)
            .where(Marking.project_id == project_id, Marking.id.in_(ids))
            .order_by(Marking.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    ).all()
    return len(rows) == len(ids) and all(
        any(Principal.model_validate(p) in context.principals for p in row.readers) for row in rows
    )


async def require_clearance(session, actor, project_id, required):
    if not required:
        return
    context = await project_access_context(
        session, actor, project_id, source_access=True, lock_membership=True
    )
    if not await cleared(session, project_id, required, context):
        raise HTTPException(404, "Project resource not found")
