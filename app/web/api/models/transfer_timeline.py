"""Bounded transfer attempt and receipt timelines for exact sandbox destinations."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.web.api.models.transfer_plans import TransferRun
from app.web.api.models.transfer_upstream import Operator, UpstreamGroup, UpstreamTriggerEvent

TimelineKind = Literal["attempt", "receipt"]
TimelineTrigger = Literal["manual", "daily_utc", "upstream"]


class TransferTimelineEntry(BaseModel):
    id: UUID
    kind: TimelineKind
    occurredAt: datetime
    planId: UUID
    planUpdatedAt: datetime
    status: Literal["started", "committed", "replayed", "rejected", "outcome_unknown"]
    receiptId: UUID | None = None
    rowsAffected: int | None = None
    physicalScope: Literal["current", "historical", "unknown"]
    trigger: TimelineTrigger | None = None
    sourceSchema: str | None = None
    sourceTable: str | None = None
    finishedAt: datetime | None = None
    message: str | None = None


class TransferTimelineJob(BaseModel):
    id: UUID
    kind: Literal["daily_utc", "upstream"]
    dueAt: datetime
    state: Literal["pending", "dispatched", "committed", "rejected", "outcome_unknown", "expired"]
    parentEvents: list[UpstreamTriggerEvent]
    operator: Operator | None = Field(default=None, exclude_if=lambda value: value is None)
    group: UpstreamGroup | None = Field(default=None, exclude_if=lambda value: value is None)
    ruleParentPlanIds: list[UUID] | None = Field(default=None, exclude_if=lambda value: value is None)


class TransferTimelineDetail(TransferTimelineEntry):
    job: TransferTimelineJob | None = None


class TransferTimelinePage(BaseModel):
    entries: list[TransferTimelineEntry]
    nextCursor: str | None = None
    latestCurrentReceipt: TransferRun | None = None
