"""Read-only incremental Batch lineage and receipt-backed timeline DTOs."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

BatchLineageKind = Literal["receipt", "attempt"]
BatchEntryStatus = Literal["started", "committed", "no_new_data", "replayed", "rejected", "retryable", "outcome_unknown"]


class BatchLineageEdge(BaseModel):
    state: Literal["unreviewed", "reviewed", "observed", "archived"]
    sourceSchema: Literal["batch_sync_stream"] = "batch_sync_stream"
    sourceTable: Literal["batches"] = "batches"
    targetSchema: Literal["ts_batch_sync_sandbox"] = "ts_batch_sync_sandbox"
    targetTable: Literal["committed_batch_fixture"] = "committed_batch_fixture"
    appliedThrough: str | None = None
    latestPositiveReceiptId: UUID | None = None
    latestPositiveRecordedAt: datetime | None = None


class BatchLineageEntry(BaseModel):
    id: UUID
    kind: BatchLineageKind
    occurredAt: datetime
    planUpdatedAt: datetime
    status: BatchEntryStatus
    finishedAt: datetime | None = None
    receiptId: UUID | None = None
    physicalScope: Literal["current", "archived"]
    trigger: Literal["manual", "daily_utc"] | None = None
    rowsAffected: int | None = None
    fromExclusive: str | None = None
    throughInclusive: str | None = None
    sourceHighWater: str | None = None
    eventsRead: int | None = None
    code: str | None = None


class BatchLineageJob(BaseModel):
    slot: datetime
    state: Literal["pending", "dispatched", "committed", "no_new_data", "rejected", "outcome_unknown", "expired"]
    dispatchedAt: datetime | None = None
    finishedAt: datetime | None = None


class BatchLineageDetail(BatchLineageEntry):
    job: BatchLineageJob | None = None


class BatchLineagePage(BaseModel):
    lineage: BatchLineageEdge
    entries: list[BatchLineageEntry]
    nextCursor: str | None = None
    from_at: datetime = Field(serialization_alias="from")
    to: datetime
