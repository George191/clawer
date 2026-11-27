"""Local sandbox schedule bindings and UTC-slot job history."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field as ApiField
from sqlalchemy import CheckConstraint, Column, DateTime, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel


class BatchSyncScheduleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    plannedTimeUtc: str = ApiField(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    expectedUpdatedAt: AwareDatetime
    expectedGeneration: int | None = ApiField(ge=1)


class BatchSyncSchedule(BaseModel):
    available: bool
    enabled: bool
    configuredEnabled: bool
    sourcePaused: Literal[True] | None = ApiField(default=None, exclude_if=lambda value: value is None)
    generation: int | None = None
    plannedTimeUtc: str | None = None
    armedForUpdatedAt: datetime | None = None
    armedForSourceUpdatedAt: datetime | None = None
    updatedAt: datetime | None = None


class BatchSyncScheduleSummary(BatchSyncSchedule):
    planId: UUID
    connectionId: UUID
    planName: str
    sourceTable: str
    targetTable: str


class BatchSyncScheduleJob(BaseModel):
    id: UUID
    slot: datetime
    state: Literal["pending", "dispatched", "committed", "no_new_data", "rejected", "outcome_unknown", "expired"]
    dispatchedAt: datetime | None = None
    finishedAt: datetime | None = None
    receiptId: UUID | None = None
    message: str | None = None


class BatchSyncScheduleRow(SQLModel, table=True):
    __tablename__ = "helio_batch_sync_schedules"
    __table_args__ = (
        CheckConstraint("generation > 0", name="ck_ontology_batch_sync_schedules_generation"),
        CheckConstraint("planned_time_utc ~ '^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$'",
                        name="ck_ontology_batch_sync_schedules_time"),
        Index("ix_ontology_batch_sync_schedules_time", "enabled", "planned_time_utc"),
    )

    plan_id: UUID = Field(primary_key=True, foreign_key="helio_batch_sync_plans.id")
    enabled: bool
    generation: int
    armed_by: UUID
    execution_scope: str = Field(max_length=128)
    planned_time_utc: str = Field(max_length=5)
    source_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    armed_for_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class BatchSyncScheduleJobRow(SQLModel, table=True):
    __tablename__ = "helio_batch_sync_schedule_jobs"
    __table_args__ = (
        UniqueConstraint("plan_id", "generation", "slot", name="uq_ontology_batch_sync_schedule_jobs_slot"),
        CheckConstraint("state IN ('pending','dispatched','committed','no_new_data','rejected','outcome_unknown','expired')",
                        name="ck_ontology_batch_sync_schedule_jobs_state"),
        CheckConstraint("(state IN ('committed','no_new_data')) = (receipt_id IS NOT NULL)",
                        name="ck_ontology_batch_sync_schedule_job_receipt"),
        Index("ix_ontology_batch_sync_schedule_expiry", "expires_at",
              postgresql_where=text("state IN ('pending','dispatched')")),
        Index("ix_ontology_batch_sync_schedule_jobs_due", "state", "dispatch_after"),
        Index("ix_ontology_batch_sync_schedule_jobs_plan", "plan_id", "slot"),
    )

    id: UUID = Field(primary_key=True)
    plan_id: UUID = Field(foreign_key="helio_batch_sync_schedules.plan_id")
    generation: int
    slot: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    dispatch_after: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    source_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    plan_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    armed_by: UUID
    state: str = Field(default="pending", max_length=16)
    dispatched_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    receipt_id: UUID | None = Field(default=None, foreign_key="helio_batch_sync_receipts.id")
    message: str | None = Field(default=None, max_length=240)
