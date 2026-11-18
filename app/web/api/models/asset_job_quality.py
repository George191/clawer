"""Exact Daily UTC schedule review and sanitized job-outcome evidence."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field as ApiField
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime
from sqlmodel import Field, SQLModel

from app.web.api.models.asset_freshness import AssetQualityIncidentLists

JobQualityState = Literal["disabled", "not_configured", "not_checked", "healthy", "unhealthy", "unknown", "superseded"]
JobQualityReason = Literal[
    "checking_disabled", "target_unavailable", "no_eligible_schedule", "policy_changed", "schedule_changed",
    "target_changed", "evaluation_stale", "no_post_review_slot", "missing_slot", "nonterminal", "expired", "canceled", "outcome_unknown",
    "zero_rows", "unverified_receipt", "unproven_rejection", "target_rejected",
]
JobState = Literal["pending", "dispatched", "committed", "rejected", "outcome_unknown", "expired", "canceled"]
Version = Annotated[int, ApiField(strict=True, ge=1)]


class AssetJobQualityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedGeneration: Version | None
    planId: UUID
    expectedPlanUpdatedAt: AwareDatetime
    expectedFreshnessGeneration: Version
    expectedScheduleGeneration: Version


class JobQualityCandidate(BaseModel):
    planId: UUID
    planUpdatedAt: datetime
    freshnessGeneration: int
    scheduleGeneration: int
    plannedTimeUtc: str


class JobQualityBinding(JobQualityCandidate):
    generation: int
    reviewedAt: datetime


class AssetJobObservation(BaseModel):
    slot: datetime
    state: JobState
    finishedAt: datetime | None = None
    receiptId: UUID | None = None
    rowsAffected: int | None = None


class AssetJobQualityStatus(BaseModel):
    available: bool
    state: JobQualityState
    reason: JobQualityReason | None = None
    checkedAt: datetime | None = None
    validUntil: datetime | None = None
    candidate: JobQualityCandidate | None = None
    binding: JobQualityBinding | None = None
    observation: AssetJobObservation | None = None
    incidents: AssetQualityIncidentLists = ApiField(default_factory=AssetQualityIncidentLists)


class AssetJobQualityRow(SQLModel, table=True):
    __tablename__ = "ontology_asset_daily_job_quality"
    __table_args__ = (CheckConstraint(
        "generation>0 AND freshness_generation>0 AND schedule_generation>0 AND target_oid>0 AND target_filenode>0 "
        "AND planned_time_utc ~ '^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$' "
        "AND state IN ('not_checked','healthy','unhealthy','unknown','superseded')",
        name="ck_asset_daily_job_review",
    ),)

    target_table: str = Field(primary_key=True, foreign_key="ontology_asset_freshness_policies.target_table", max_length=63)
    generation: int
    freshness_generation: int
    schedule_generation: int
    plan_id: UUID
    plan_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    armed_by: UUID
    target_oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    target_filenode: int = Field(sa_column=Column(BigInteger, nullable=False))
    planned_time_utc: str = Field(max_length=5)
    reviewed_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    checked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    confirmed_slot: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    observed_slot: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    job_state: str | None = Field(default=None, max_length=16)
    state: str = Field(default="not_checked", max_length=16)
    reason: str | None = Field(default=None, max_length=32)
    receipt_id: UUID | None = None
    rows_affected: int | None = None



class WeeklyJobQualityCandidate(JobQualityCandidate):
    plannedWeekdayUtc: int = ApiField(ge=1, le=7)


class WeeklyJobQualityBinding(JobQualityBinding):
    plannedWeekdayUtc: int = ApiField(ge=1, le=7)


class WeeklyJobQualityStatus(AssetJobQualityStatus):
    candidate: WeeklyJobQualityCandidate | None = None
    binding: WeeklyJobQualityBinding | None = None


class _WeeklyEvidence(BaseModel):
    jobId: UUID
    slot: datetime
    finishedAt: datetime


class WeeklyReceiptEvidence(_WeeklyEvidence):
    kind: Literal["receipt"] = "receipt"
    receiptId: UUID
    recordedAt: datetime
    rowsAffected: int = ApiField(ge=1, le=100)


class WeeklyTargetConstraintEvidence(_WeeklyEvidence):
    kind: Literal["target_constraint"] = "target_constraint"
    attemptId: UUID
    startedAt: datetime
    attemptFinishedAt: datetime


WeeklyJobEvidence = Annotated[WeeklyReceiptEvidence | WeeklyTargetConstraintEvidence, ApiField(discriminator="kind")]


class WeeklyJobQualityRow(SQLModel, table=True):
    __tablename__ = "ontology_asset_weekly_job_quality"
    __table_args__ = (CheckConstraint(
        "generation>0 AND freshness_generation>0 AND schedule_generation>0 AND target_oid>0 AND target_filenode>0 "
        "AND planned_time_utc ~ '^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$' "
        "AND planned_weekday_utc BETWEEN 1 AND 7 AND state IN ('not_checked','healthy','unhealthy','unknown','superseded')",
        name="ck_asset_weekly_job_review",
    ),)

    target_table: str = Field(primary_key=True, foreign_key="ontology_asset_freshness_policies.target_table", max_length=63)
    generation: int
    freshness_generation: int
    schedule_generation: int
    plan_id: UUID
    plan_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    armed_by: UUID
    target_oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    target_filenode: int = Field(sa_column=Column(BigInteger, nullable=False))
    planned_time_utc: str = Field(max_length=5)
    planned_weekday_utc: int
    reviewed_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    checked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    confirmed_slot: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    observed_slot: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    job_state: str | None = Field(default=None, max_length=16)
    state: str = Field(default="not_checked", max_length=16)
    reason: str | None = Field(default=None, max_length=32)
    receipt_id: UUID | None = None
    rows_affected: int | None = None


class TransferTargetRejectionRow(SQLModel, table=True):
    __tablename__ = "ontology_transfer_schedule_target_rejections"
    __table_args__ = (CheckConstraint("code='target_constraint' AND target_oid>0 AND target_filenode>0",
                                      name="ck_transfer_target_rejection_scope"),)

    job_id: UUID = Field(primary_key=True, foreign_key="ontology_transfer_schedule_jobs.id")
    attempt_id: UUID = Field(unique=True, foreign_key="ontology_transfer_attempts.id")
    code: str = Field(default="target_constraint", max_length=20)
    target_oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    target_filenode: int = Field(sa_column=Column(BigInteger, nullable=False))
