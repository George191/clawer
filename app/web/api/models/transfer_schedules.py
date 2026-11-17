"""Local sandbox schedule bindings and UTC-slot job history."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import CheckConstraint, Column, DateTime, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class TransferScheduleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    expectedUpdatedAt: AwareDatetime


class TransferSchedule(BaseModel):
    available: bool
    enabled: bool
    generation: int | None = None
    plannedTimeUtc: str | None = None
    armedForUpdatedAt: datetime | None = None
    updatedAt: datetime | None = None


class TransferScheduleSummary(TransferSchedule):
    planId: UUID
    planName: str
    sourceTable: str
    targetTable: str


class TransferScheduleJob(BaseModel):
    id: UUID
    slot: datetime
    state: Literal["pending", "dispatched", "committed", "rejected", "outcome_unknown", "expired"]
    dispatchedAt: datetime | None = None
    finishedAt: datetime | None = None
    receiptId: UUID | None = None
    message: str | None = None


class TransferScheduleRow(SQLModel, table=True):
    __tablename__ = "ontology_transfer_schedules"
    __table_args__ = (CheckConstraint("generation > 0", name="ck_ontology_transfer_schedules_generation"),)

    plan_id: UUID = Field(primary_key=True, foreign_key="ontology_transfer_plans.id")
    enabled: bool
    generation: int
    armed_by: UUID
    armed_for_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class TransferScheduleJobRow(SQLModel, table=True):
    __tablename__ = "ontology_transfer_schedule_jobs"
    __table_args__ = (
        UniqueConstraint("plan_id", "generation", "slot", name="uq_ontology_transfer_schedule_jobs_slot"),
        CheckConstraint("state IN ('pending','dispatched','committed','rejected','outcome_unknown','expired')",
                        name="ck_ontology_transfer_schedule_jobs_state"),
        Index("ix_ontology_transfer_schedule_jobs_due", "state", "dispatch_after"),
        Index("ix_ontology_transfer_schedule_jobs_plan", "plan_id", "slot"),
    )

    id: UUID = Field(primary_key=True)
    plan_id: UUID = Field(foreign_key="ontology_transfer_schedules.plan_id")
    generation: int
    slot: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    dispatch_after: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    plan_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    armed_by: UUID
    state: str = Field(default="pending", max_length=16)
    dispatched_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    receipt_id: UUID | None = Field(default=None, foreign_key="ontology_transfer_runs.id")
    message: str | None = Field(default=None, max_length=240)
