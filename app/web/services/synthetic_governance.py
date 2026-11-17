"""Admin route authorization for this synthetic dataset family; never derives grants from request claims."""

from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.web.api.models.synthetic_governance import SandboxMarking
from app.web.core.config import settings

PROTECTED_SYNTHETIC_SCHEMA = "ts_cdc_sandbox"
BASE, RESTRICTED = 1, 2
BASE_TAG: SandboxMarking = "sandbox-synthetic"
RESTRICTED_TAG: SandboxMarking = "sandbox-restricted"


def marking_limit(actor_id: UUID) -> int | None:
    """None is legacy-only mode, 0 is denied; 1/2 admit exactly the fixed known marking levels."""
    if not settings.ONTOLOGY_SYNTHETIC_GOVERNANCE_ENABLED:
        return None
    if (settings.ENVIRONMENT != "local" or "ENVIRONMENT" not in settings.model_fields_set
            or not settings.CDC_SANDBOX_VERSIONS_ENABLED):
        return 0
    grants = settings.ONTOLOGY_SYNTHETIC_GOVERNANCE_GRANTS.get(actor_id, ())
    if BASE_TAG not in grants:
        return 0
    return RESTRICTED if RESTRICTED_TAG in grants else BASE


def require_marking_access(limit: int | None) -> None:
    if limit == 0:
        raise HTTPException(403, "This admin has no grant for the local synthetic dataset")


def allowed_level(column: Any, limit: int | None) -> Any:
    """SQL authorization predicate, applied before history LIMIT or protected row lookup."""
    if limit is None:
        return column.is_(None)
    return column.in_((BASE, RESTRICTED) if limit == RESTRICTED else (BASE,) if limit == BASE else ())


def markings(level: int | None) -> list[SandboxMarking] | None:
    if level == BASE:
        return [BASE_TAG]
    if level == RESTRICTED:
        return [BASE_TAG, RESTRICTED_TAG]
    return None


def builder_marking(limit: int | None, additional: SandboxMarking | None) -> int | None:
    if additional is not None and limit != RESTRICTED:
        raise HTTPException(403, "The restricted synthetic marking is not available for this admin")
    if limit is None:
        return None
    require_marking_access(limit)
    return RESTRICTED if additional else BASE
