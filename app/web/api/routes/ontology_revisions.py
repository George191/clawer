"""Team-scoped proposal, independent review, history and restoration routes."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from sqlmodel import select

from app.web.api.dependencies import CurrentUser, SessionDep
from app.web.api.models.ontology_registry import Definition, Revision
from app.web.api.models.ontology_revisions import (
    OntologyRevisionProposal,
    OntologyRevisionSnapshot,
    ProposalCreate,
    RestoreProposal,
    ReviewDecision,
    RevisionRoleUpdate,
)
from app.web.services import ontology_revisions as service
from app.web.services.ontology_registry_identity import project_principals

router = APIRouter(prefix="/api/ontology/v1/object-types/{definition_id}", tags=["ontology revisions"])


@router.get("/revision-workspace")
async def revision_workspace(definition_id: UUID, session: SessionDep, user: CurrentUser):
    row, definition, rights = await service.authorized_type(session, user, definition_id)
    return {
        "revision": row.revision, "definition": service.dump(definition),
        "canEdit": rights.edit, "canReview": rights.review, "canManageRoles": rights.manage,
    }


@router.get("/revisions")
async def history(
    definition_id: UUID, session: SessionDep, user: CurrentUser,
    limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=100_000),
):
    row, _, _ = await service.authorized_type(session, user, definition_id)
    snapshot = OntologyRevisionSnapshot
    proposal = OntologyRevisionProposal
    rows = (await session.execute(
        select(snapshot.revision, snapshot.created_at, snapshot.actor_id, proposal.id, proposal.restore_revision)
        .select_from(snapshot).outerjoin(proposal, snapshot.proposal_id == proposal.id)
        .where(snapshot.definition_id == row.id)
        .order_by(snapshot.revision.desc()).limit(limit + 1).offset(offset)
    )).all()
    return {
        "items": [{"revision": revision, "createdAt": created, "actorId": actor,
                   "proposalId": proposal_id, "restoreRevision": restored}
                  for revision, created, actor, proposal_id, restored in rows[:limit]],
        "hasMore": len(rows) > limit,
    }


@router.get("/revisions/{revision}")
async def revision_details(definition_id: UUID, revision: int, session: SessionDep, user: CurrentUser):
    row, current, _ = await service.authorized_type(session, user, definition_id)
    snapshot = await service.read_snapshot(session, row, revision)
    definition = service.dump(Definition.model_validate(snapshot.snapshot))
    return {
        "revision": snapshot.revision, "definition": definition,
        "changesFromCurrent": service.definition_diff(service.dump(current), definition),
    }


@router.post("/revisions/{revision}/restore-proposals", status_code=201)
async def propose_restore(
    definition_id: UUID, revision: int, payload: RestoreProposal, session: SessionDep, user: CurrentUser,
):
    row, current, _ = await service.authorized_type(session, user, definition_id, need="edit", lock="read")
    service.check_revision(row, payload.revision)
    if revision >= row.revision:
        raise HTTPException(422, "Choose an earlier published revision")
    snapshot = await service.read_snapshot(session, row, revision)
    return await service.create_proposal(
        session, row, current, user, payload.revision, Definition.model_validate(snapshot.snapshot), payload.summary, revision,
    )


@router.get("/revision-proposals")
async def proposals(
    definition_id: UUID, session: SessionDep, user: CurrentUser,
    limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=100_000),
):
    row, _, _ = await service.authorized_type(session, user, definition_id)
    proposal = OntologyRevisionProposal
    rows = (await session.scalars(
        select(proposal).where(proposal.definition_id == row.id)
        .order_by(proposal.created_at.desc(), proposal.id.desc()).limit(limit + 1).offset(offset)
    )).all()
    return {"items": [service.proposal_view(item, row, user.id) for item in rows[:limit]], "hasMore": len(rows) > limit}


@router.post("/revision-proposals", status_code=201)
async def propose(definition_id: UUID, payload: ProposalCreate, session: SessionDep, user: CurrentUser):
    row, current, _ = await service.authorized_type(session, user, definition_id, need="edit", lock="read")
    return await service.create_proposal(session, row, current, user, payload.revision, payload.definition, payload.summary)


@router.get("/revision-proposals/{proposal_id}")
async def proposal_details(definition_id: UUID, proposal_id: UUID, session: SessionDep, user: CurrentUser):
    row, _, _ = await service.authorized_type(session, user, definition_id)
    proposal = await service.get_proposal(session, row, proposal_id)
    baseline = await service.read_snapshot(session, row, proposal.base_revision)
    return {
        **service.proposal_view(proposal, row, user.id),
        "definition": proposal.proposed_definition,
        "changes": service.definition_diff(service.dump(Definition.model_validate(baseline.snapshot)), proposal.proposed_definition),
    }


@router.post("/revision-proposals/{proposal_id}/review")
async def review(
    definition_id: UUID, proposal_id: UUID, payload: ReviewDecision, session: SessionDep, user: CurrentUser,
):
    row, _, _ = await service.authorized_type(session, user, definition_id, need="review", lock="read")
    proposal = await service.get_proposal(session, row, proposal_id, lock=True)
    return await service.review_proposal(session, row, proposal, user, payload)


@router.post("/revision-proposals/{proposal_id}/merge")
async def merge(
    definition_id: UUID, proposal_id: UUID, payload: Revision, session: SessionDep, user: CurrentUser,
):
    row, current, _ = await service.authorized_type(session, user, definition_id, need="edit", lock="write")
    service.check_revision(row, payload.revision)
    proposal = await service.get_proposal(session, row, proposal_id, lock=True)
    return await service.merge_proposal(session, row, current, proposal, user)


@router.get("/revision-roles")
async def revision_roles(
    definition_id: UUID, session: SessionDep, user: CurrentUser,
    limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=100_000),
):
    row, _, _ = await service.authorized_type(session, user, definition_id, need="manage")
    return await service.current_role_members(session, row.project_id, limit, offset)


@router.get("/revision-principals")
async def revision_principals(
    definition_id: UUID, session: SessionDep, user: CurrentUser,
    q: str = Query("", max_length=120),
):
    row, _, _ = await service.authorized_type(session, user, definition_id, need="edit")
    return {"items": await project_principals(session, row.project_id, search=q.strip())}


@router.put("/revision-roles/{member_id}", status_code=204, response_class=Response)
async def change_role(
    definition_id: UUID, member_id: UUID, payload: RevisionRoleUpdate, session: SessionDep, user: CurrentUser,
):
    row, _, _ = await service.authorized_type(session, user, definition_id, need="manage")
    await service.set_member_roles(session, row, user, member_id, payload)
    return Response(status_code=204)
