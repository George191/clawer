"""Scoped operator wire types for read head and prepare-only snapshot."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, UUID4


class SnapshotIntentWebCapability(BaseModel):
    available: bool
    configRevision: UUID | None = None


class SnapshotIntentWebHeadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedConfigRevision: UUID


class SnapshotIntentWebPrepareRequest(SnapshotIntentWebHeadRequest):
    requestId: UUID4
    expectedPreviousGeneration: int = Field(strict=True, ge=0, le=15)


class SnapshotIntentWebHead(BaseModel):
    generation: int


class SnapshotIntentWebPrepared(BaseModel):
    id: UUID
    expectedPreviousGeneration: int
    rowCount: int
    createdAt: datetime
