"""Safe wire types for exact locally recorded snapshot-delivery metadata."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, UUID4


class SnapshotDeliveryEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intentId: UUID4
    requestId: UUID4
    expectedConfigRevision: UUID


class SnapshotDeliveryEvidenceCapability(BaseModel):
    available: bool
    configRevision: UUID | None = None


class SnapshotDeliveryEvidence(BaseModel):
    state: Literal["not_admitted", "unconfirmed", "confirmed_recorded_receipt"]
    generation: int | None = None
    rowCount: int | None = None
    recordedAt: datetime | None = None
