"""Deployment-owned review grants for one metadata-only synthetic sink."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

HandoffScope = Literal["synthetic-review-sink"]
HandoffClearance = Literal["sandbox-synthetic", "sandbox-restricted"]


class HandoffSinkGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope: HandoffScope
    generation: UUID
    streamMarker: UUID
    clearance: HandoffClearance


class HandoffCopyGrant(HandoffSinkGrant):
    databaseMarker: UUID
    physicalDigest: str = Field(pattern=r"^[0-9a-f]{64}$")
