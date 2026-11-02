"""Authenticated, read-only CDC diagnostics."""

from fastapi import APIRouter, Depends

from app.web.api.dependencies.common import get_current_active_superuser
from app.web.api.models.cdc import CdcStatus
from app.web.services import cdc_runtime

router = APIRouter(prefix="/cdc", tags=["cdc"], dependencies=[Depends(get_current_active_superuser)])


@router.get("/status", response_model=CdcStatus)
async def status() -> CdcStatus:
    """Inspect connector, database and CDC consumer health without changing runtime state."""
    return await cdc_runtime.status()
