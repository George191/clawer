"""Exact-sandbox policy and evaluated write-cadence evidence."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index
from sqlmodel import Field as DbField, SQLModel

FreshnessState = Literal["not_configured", "not_checked", "healthy", "overdue", "unknown", "paused"]


class AssetFreshnessChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planId: UUID
    expectedPlanUpdatedAt: AwareDatetime
    expectedGeneration: int | None = Field(ge=1)
    thresholdMinutes: int = Field(ge=5, le=10080)
    enabled: bool


class AssetFreshnessCandidate(BaseModel):
    id: UUID
    name: str
    updatedAt: datetime


class AssetFreshnessPolicy(BaseModel):
    planId: UUID
    planUpdatedAt: datetime
    thresholdMinutes: int
    enabled: bool
    generation: int
    updatedAt: datetime


class AssetFreshnessAssessment(BaseModel):
    state: FreshnessState
    reason: str | None = None
    checkedAt: datetime | None = None
    receiptRecordedAt: datetime | None = None
    receiptId: UUID | None = None
    validUntil: datetime | None = None


class AssetFreshnessTransition(AssetFreshnessAssessment):
    id: UUID
    generation: int


class AssetFreshnessResponse(BaseModel):
    available: bool
    policy: AssetFreshnessPolicy | None = None
    assessment: AssetFreshnessAssessment
    history: list[AssetFreshnessTransition] = Field(default_factory=list)


class AssetFreshnessPolicyRow(SQLModel, table=True):
    __tablename__ = "ontology_asset_freshness_policies"
    __table_args__ = (
        CheckConstraint("threshold_minutes BETWEEN 5 AND 10080 AND generation > 0",
                        name="ck_ontology_asset_freshness_policy_values"),
        CheckConstraint("state IN ('not_checked','healthy','overdue','unknown','paused')",
                        name="ck_ontology_asset_freshness_policy_state"),
        Index("ix_ontology_asset_freshness_due", "enabled", "next_check_at", "target_table"),
    )

    target_table: str = DbField(max_length=63, primary_key=True)
    target_oid: int = DbField(sa_column=Column(BigInteger, nullable=False))
    target_filenode: int = DbField(sa_column=Column(BigInteger, nullable=False))
    plan_id: UUID = DbField(foreign_key="ontology_transfer_plans.id")
    plan_updated_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    threshold_minutes: int
    enabled: bool
    generation: int
    updated_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    next_check_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    state: str = DbField(max_length=15)
    reason: str | None = DbField(default=None, max_length=40)
    checked_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
    receipt_recorded_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
    receipt_id: UUID | None = DbField(default=None, foreign_key="ontology_transfer_runs.id")


class AssetFreshnessTransitionRow(SQLModel, table=True):
    __tablename__ = "ontology_asset_freshness_transitions"
    __table_args__ = (
        CheckConstraint("state IN ('healthy','overdue','unknown') AND generation > 0",
                        name="ck_ontology_asset_freshness_transition_state"),
        Index("ix_ontology_asset_freshness_history", "target_table", "checked_at", "id"),
    )

    id: UUID = DbField(default_factory=uuid4, primary_key=True)
    target_table: str = DbField(foreign_key="ontology_asset_freshness_policies.target_table")
    generation: int
    state: str = DbField(max_length=15)
    reason: str | None = DbField(default=None, max_length=40)
    checked_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    receipt_recorded_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
    receipt_id: UUID | None = DbField(default=None, foreign_key="ontology_transfer_runs.id")
