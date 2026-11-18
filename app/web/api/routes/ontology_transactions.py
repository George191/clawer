"""Project action transactions, personal receipts and permission-checked undo."""

from uuid import UUID

from fastapi import APIRouter, Path, Query
from sqlalchemy import select

from app.web.api.dependencies.common import CurrentUser
from app.web.api.dependencies.session import SessionDep
from app.web.api.models.ontology_runtime import OntologyTransaction
from app.web.api.routes.ontology_functions import router as functions_router
from app.web.services import ontology_transactions as tx
from app.web.api.routes.ontology_writeback import router as writeback_router

router = APIRouter(prefix="/api/ontology/v1", tags=["ontology transactions"])
router.include_router(writeback_router)
router.include_router(functions_router)


@router.post("/projects/{project_id}/transactions", response_model=tx.TransactionReceipt)
async def execute_transaction(
    project_id: UUID, body: tx.TransactionRequest, user: CurrentUser, session: SessionDep
):
    return await tx.commit_operation(session, tx.execute(session, user, project_id, body))


@router.get("/projects/{project_id}/transactions")
async def transactions(
    project_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100_000),
):
    await tx.require_member(session, user, project_id)
    results = (
        await session.scalars(
            select(OntologyTransaction)
            .where(
                OntologyTransaction.project_id == project_id,
                OntologyTransaction.actor_id == user.id,
                tx.visible_receipt(user, project_id),
            )
            .order_by(OntologyTransaction.created_at.desc(), OntologyTransaction.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
    ).all()
    return {"items": [tx.receipt(row) for row in results[:limit]], "hasMore": len(results) > limit}


@router.post(
    "/projects/{project_id}/transactions/{transaction_id}/undo",
    response_model=tx.TransactionReceipt,
)
async def undo_transaction(
    project_id: UUID, transaction_id: UUID, user: CurrentUser, session: SessionDep
):
    return await tx.commit_operation(session, tx.undo(session, user, project_id, transaction_id))


@router.post("/object-types/{definition_id}/records/{record_id}/history/{revision}/undo")
async def undo_edit(
    definition_id: UUID,
    record_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    revision: int = Path(ge=1, le=2_147_483_646),
):
    return await tx.commit_operation(
        session, tx.undo_single(session, user, definition_id, record_id, revision)
    )
