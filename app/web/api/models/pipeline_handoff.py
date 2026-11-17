"""Read-only handoff eligibility for one fixed synthetic profile."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, UUID4

from app.web.api.models.synthetic_governance import SandboxMarking


class PipelineHandoffSink(BaseModel):
    id: Literal["synthetic-review-sink"] = "synthetic-review-sink"
    label: str = "Reviewed local synthetic sink"
    generation: UUID


class PipelineHandoffCapability(BaseModel):
    available: bool
    copyAvailable: Literal[True] | None = Field(default=None, exclude_if=lambda value: value is None)
    sink: PipelineHandoffSink | None = Field(default=None, exclude_if=lambda value: value is None)


class PipelineHandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outputId: UUID
    expectedConfigHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expectedResultHash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expectedSinkGeneration: UUID


class PipelineHandoffEligibility(BaseModel):
    outputId: UUID
    sink: PipelineHandoffSink
    eligible: bool
    reason: Literal["eligible_for_review", "clearance_mismatch"]
    markings: list[SandboxMarking]


class PipelineHandoffCopyRequest(PipelineHandoffRequest):
    requestId: UUID4


class PipelineHandoffCopyReceipt(BaseModel):
    id: UUID
    requestId: UUID
    outputId: UUID
    sink: PipelineHandoffSink
    markings: list[SandboxMarking]
    rowsCopied: int
    recordedAt: datetime
