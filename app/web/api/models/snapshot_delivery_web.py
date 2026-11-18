"""Explicit reviewed one-shot delivery of one authorized frozen synthetic intent."""

from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, UUID4


class SnapshotDeliveryWebCapability(BaseModel):
    available: bool


class SnapshotDeliveryWebLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intentId: UUID
    requestId: UUID4


class SnapshotDeliveryWebRun(SnapshotDeliveryWebLookup):
    expectedWebRevision: UUID


class SnapshotDeliveryWebReview(BaseModel):
    expectedPreviousGeneration: Annotated[int, Field(ge=0, le=15)]
    rowCount: Annotated[int, Field(ge=1, le=100)]
    webRevision: UUID


class SnapshotDeliveryWebReceipt(BaseModel):
    intentId: UUID
    requestId: UUID
    generation: Annotated[int, Field(ge=1, le=16)]
    rowCount: Annotated[int, Field(ge=1, le=100)]
    recordedAt: AwareDatetime
