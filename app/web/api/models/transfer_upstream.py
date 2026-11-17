"""Bound, one-hop sandbox dependency triggers and durable commit events."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field as ApiField
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKey, Identity, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel


class UpstreamTriggerChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    parentPlanId: UUID | None
    expectedParentUpdatedAt: AwareDatetime | None
    expectedUpdatedAt: AwareDatetime
    expectedGeneration: int | None = ApiField(ge=1)


class UpstreamTrigger(BaseModel):
    available: bool
    enabled: bool
    configuredEnabled: bool
    generation: int | None = None
    parentPlanId: UUID | None = None
    parentPlanName: str | None = None
    armedForUpdatedAt: datetime | None = None
    armedForParentUpdatedAt: datetime | None = None
    updatedAt: datetime | None = None


class UpstreamTriggerSummary(UpstreamTrigger):
    planId: UUID
    planName: str
    sourceSchema: str
    sourceTable: str
    targetSchema: str
    targetTable: str


class UpstreamCandidate(BaseModel):
    id: UUID
    name: str
    updatedAt: datetime
    sourceTable: str
    targetTable: str


class UpstreamTriggerJob(BaseModel):
    id: UUID
    eventId: str
    eventRecordedAt: datetime
    parentReceiptId: UUID
    state: Literal["pending", "dispatched", "committed", "rejected", "outcome_unknown", "expired"]
    dispatchedAt: datetime | None = None
    finishedAt: datetime | None = None
    receiptId: UUID | None = None
    message: str | None = None


class TransferCommitEventRow(SQLModel, table=True):
    __tablename__ = "ontology_transfer_commit_events"
    __table_args__ = (
        Index("ix_ontology_transfer_commit_event_parent", "parent_plan_id", "id"),
        Index("ix_ontology_transfer_commit_event_time", "parent_plan_id", "recorded_at", "id"),
    )

    id: int | None = Field(default=None, sa_column=Column(BigInteger, Identity(), primary_key=True))
    parent_plan_id: UUID = Field(foreign_key="ontology_transfer_plans.id")
    parent_receipt_id: UUID = Field(foreign_key="ontology_transfer_runs.id", unique=True)
    parent_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    source_oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    source_node: int = Field(sa_column=Column(BigInteger, nullable=False))
    target_oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    target_node: int = Field(sa_column=Column(BigInteger, nullable=False))
    recorded_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class UpstreamTriggerRow(SQLModel, table=True):
    __tablename__ = "ontology_transfer_upstream_triggers"
    __table_args__ = (
        CheckConstraint("plan_id <> parent_plan_id AND generation > 0 AND cutoff_event_id >= 0",
                        name="ck_ontology_transfer_upstream_trigger"),
    )

    plan_id: UUID = Field(primary_key=True, foreign_key="ontology_transfer_plans.id")
    parent_plan_id: UUID = Field(foreign_key="ontology_transfer_plans.id")
    generation: int
    enabled: bool
    armed_by: UUID
    parent_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    child_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    cutoff_event_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    physical_digest: str = Field(max_length=64)
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class UpstreamTriggerJobRow(SQLModel, table=True):
    __tablename__ = "ontology_transfer_upstream_jobs"
    __table_args__ = (
        UniqueConstraint("plan_id", "generation", "event_id", name="uq_ontology_transfer_upstream_jobs_event"),
        CheckConstraint("state IN ('pending','dispatched','committed','rejected','outcome_unknown','expired')",
                        name="ck_ontology_transfer_upstream_job_state"),
        CheckConstraint("(state = 'committed') = (receipt_id IS NOT NULL)",
                        name="ck_ontology_transfer_upstream_job_receipt"),
        Index("ix_ontology_transfer_upstream_jobs_due", "state", "dispatch_after"),
        Index("ix_ontology_transfer_upstream_jobs_plan", "plan_id", "event_id"),
        Index("ix_ontology_transfer_upstream_expiry", "expires_at",
              postgresql_where=text("state IN ('pending','dispatched')")),
    )

    id: UUID = Field(primary_key=True)
    plan_id: UUID = Field(foreign_key="ontology_transfer_upstream_triggers.plan_id")
    event_id: int = Field(sa_column=Column(BigInteger, ForeignKey("ontology_transfer_commit_events.id"), nullable=False))
    generation: int
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    child_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    armed_by: UUID
    dispatch_after: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    state: str = Field(default="pending", max_length=16)
    dispatched_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    receipt_id: UUID | None = Field(default=None, foreign_key="ontology_transfer_runs.id")
    message: str | None = Field(default=None, max_length=240)
