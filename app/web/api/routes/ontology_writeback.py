"""Explicit delivery of a committed transaction to a configured business service."""

from uuid import UUID

from fastapi import APIRouter, Path, Response

from app.web.api.dependencies.common import CurrentUser
from app.web.api.dependencies.session import SessionDep
from app.web.services import ontology_writeback as writeback

router = APIRouter()


@router.get("/projects/{project_id}/transactions/{tx_id}/writeback")
async def status(project_id: UUID, tx_id: UUID, user: CurrentUser, session: SessionDep):
    return await writeback.list_status(session, user, project_id, tx_id)


@router.post("/projects/{project_id}/transactions/{tx_id}/writeback/{receiver}")
async def deliver(
    project_id: UUID,
    tx_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    response: Response,
    receiver: str = Path(pattern=r"^[a-z][a-z0-9_-]{0,63}$"),
):
    result = await writeback.deliver(session, user, project_id, tx_id, receiver)
    if result["status"] == "failed":
        response.status_code = 502
    return result
