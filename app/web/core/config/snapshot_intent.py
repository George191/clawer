"""Deployment-owned approval for the one fixed synthetic publication intent scope."""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

Proof = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class SnapshotIntentGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    sourceMarker: UUID
    sourceProofSha256: Proof
    privateMarker: UUID
    privateProofSha256: Proof
    configRevision: UUID
