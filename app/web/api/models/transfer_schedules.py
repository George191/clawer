"""Local sandbox schedule bindings and UTC-slot job history."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field as ApiField
from sqlalchemy import CheckConstraint, Column, DateTime, Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class TransferScheduleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    expectedUpdatedAt: AwareDatetime


class TransferScheduleJobCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedGeneration: int = ApiField(strict=True, ge=1, le=2147483647)
    expectedSlot: AwareDatetime


class TransferSchedule(BaseModel):
    available: bool
    jobCancelAvailable: Literal[True] | None = ApiField(default=None, exclude_if=lambda value: value is None)
    enabled: bool
    generation: int | None = None
    plannedTimeUtc: str | None = None
    plannedWeekdayUtc: int | None = ApiField(default=None, exclude_if=lambda value: value is None)
    plannedDayOfMonthUtc: int | None = ApiField(default=None, exclude_if=lambda value: value is None)
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
    scheduleWeekdayUtc: int | None = ApiField(default=None, exclude_if=lambda value: value is None)
    scheduleDayOfMonthUtc: int | None = ApiField(default=None, exclude_if=lambda value: value is None)
    state: Literal["pending", "dispatched", "committed", "rejected", "outcome_unknown", "expired", "canceled"]
    generation: int | None = ApiField(default=None, exclude_if=lambda value: value is None)
    canceledAt: datetime | None = ApiField(default=None, exclude_if=lambda value: value is None)
    cancelable: Literal[True] | None = ApiField(default=None, exclude_if=lambda value: value is None)
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
        CheckConstraint("state IN ('pending','dispatched','committed','rejected','outcome_unknown','expired','canceled')",
                        name="ck_ontology_transfer_schedule_jobs_state"),
        CheckConstraint("""((state = 'canceled' AND canceled_by IS NOT NULL AND canceled_at IS NOT NULL
              AND finished_at = canceled_at AND canceled_at >= slot AND canceled_at < expires_at
              AND receipt_id IS NULL) OR (state <> 'canceled' AND canceled_by IS NULL AND canceled_at IS NULL)) IS TRUE""",
                        name="ck_transfer_schedule_job_cancel"),
        CheckConstraint("schedule_weekday_utc IS NULL OR (schedule_weekday_utc BETWEEN 1 AND 7 "
                        "AND schedule_weekday_utc = EXTRACT(ISODOW FROM slot AT TIME ZONE 'UTC') AND state <> 'canceled')",
                        name="ck_transfer_schedule_job_weekday"),
        CheckConstraint(
            "schedule_day_of_month_utc IS NULL OR (schedule_weekday_utc IS NULL AND schedule_day_of_month_utc BETWEEN 1 AND 28 "
            "AND schedule_day_of_month_utc=EXTRACT(DAY FROM slot AT TIME ZONE 'UTC') AND state <> 'canceled')",
            name="ck_transfer_schedule_job_month_day",
        ),
        Index("ix_ontology_transfer_schedule_jobs_due", "state", "dispatch_after"),
        Index("ix_ontology_transfer_schedule_jobs_plan", "plan_id", "slot"),
    )

    id: UUID = Field(primary_key=True)
    plan_id: UUID = Field(foreign_key="ontology_transfer_schedules.plan_id")
    schedule_weekday_utc: int | None = None
    schedule_day_of_month_utc: int | None = None
    generation: int
    slot: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    dispatch_after: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    plan_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    armed_by: UUID
    state: str = Field(default="pending", max_length=16)
    dispatched_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    canceled_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    canceled_by: UUID | None = None
    receipt_id: UUID | None = Field(default=None, foreign_key="ontology_transfer_runs.id")
    message: str | None = Field(default=None, max_length=240)
