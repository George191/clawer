"""Bound, one-hop sandbox dependency triggers and durable commit events."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field as ApiField, model_validator
from sqlalchemy import ARRAY, BigInteger, CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint, Identity, Index, UniqueConstraint, Uuid, text
from sqlmodel import Field, SQLModel


Operator = Literal["AND", "OR"]


class UpstreamGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operator: Operator
    parentPlanIds: tuple[UUID, UUID]

    @model_validator(mode="after")
    def canonical(self):
        if self.parentPlanIds[0] == self.parentPlanIds[1]:
            raise ValueError("Choose two distinct grouped upstream plans")
        self.parentPlanIds = tuple(sorted(self.parentPlanIds))
        return self


class UpstreamParentReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    planId: UUID
    expectedUpdatedAt: AwareDatetime


class UpstreamTriggerChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    operator: Operator
    group: UpstreamGroup | None = None
    parents: list[UpstreamParentReview] = ApiField(max_length=3)
    expectedUpdatedAt: AwareDatetime
    expectedGeneration: int | None = ApiField(ge=1)

    @model_validator(mode="after")
    def validate_parents(self):
        ids = {parent.planId for parent in self.parents}
        if self.enabled and (not ids or len(ids) != len(self.parents)):
            raise ValueError("Choose one to three distinct reviewed upstream plans")
        if self.group is not None and (
            not self.enabled or len(ids) != 3 or self.group.operator == self.operator
            or not set(self.group.parentPlanIds).issubset(ids)
        ):
            raise ValueError("A nested rule requires three approved parents and a two-parent opposite-operator group")
        if not self.enabled and self.parents:
            raise ValueError("Pausing must not change the reviewed upstream plans")
        return self


class UpstreamTriggerParent(BaseModel):
    planId: UUID
    planName: str
    armedForUpdatedAt: datetime


class UpstreamTrigger(BaseModel):
    available: bool
    enabled: bool
    configuredEnabled: bool
    operator: Operator = "OR"
    group: UpstreamGroup | None = ApiField(default=None, exclude_if=lambda value: value is None)
    parents: list[UpstreamTriggerParent] = ApiField(default_factory=list)
    generation: int | None = None
    armedForUpdatedAt: datetime | None = None
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


class UpstreamTriggerEvent(BaseModel):
    eventId: str
    eventRecordedAt: datetime
    parentPlanId: UUID
    parentReceiptId: UUID


class UpstreamTriggerJob(BaseModel):
    id: UUID
    operator: Operator
    group: UpstreamGroup | None = ApiField(default=None, exclude_if=lambda value: value is None)
    ruleParentPlanIds: list[UUID] | None = ApiField(default=None, exclude_if=lambda value: value is None)
    events: list[UpstreamTriggerEvent]
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
        CheckConstraint("generation > 0 AND operator IN ('AND','OR')",
                        name="ck_ontology_transfer_upstream_trigger"),
        CheckConstraint("""((group_operator IS NULL AND group_parent_ids IS NULL)
 OR (group_operator IN ('AND','OR') AND group_operator <> operator
     AND array_ndims(group_parent_ids)=1 AND array_lower(group_parent_ids,1)=1
     AND cardinality(group_parent_ids)=2 AND group_parent_ids[1] < group_parent_ids[2])) IS TRUE""", name="ck_ontology_transfer_upstream_trigger_group"),
    )

    plan_id: UUID = Field(primary_key=True, foreign_key="ontology_transfer_plans.id")
    generation: int
    enabled: bool
    operator: str = Field(max_length=3)
    group_operator: str | None = Field(default=None, max_length=3)
    group_parent_ids: list[UUID] | None = Field(default=None, sa_column=Column(ARRAY(Uuid)))
    armed_by: UUID
    child_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    physical_digest: str = Field(max_length=64)
    updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class UpstreamTriggerParentRow(SQLModel, table=True):
    __tablename__ = "ontology_transfer_upstream_parents"
    __table_args__ = (
        CheckConstraint("plan_id <> parent_plan_id AND cutoff_event_id >= 0",
                        name="ck_ontology_transfer_upstream_parent"),
    )

    plan_id: UUID = Field(primary_key=True, foreign_key="ontology_transfer_upstream_triggers.plan_id")
    parent_plan_id: UUID = Field(primary_key=True, foreign_key="ontology_transfer_plans.id")
    parent_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    cutoff_event_id: int = Field(sa_column=Column(BigInteger, nullable=False))


class UpstreamTriggerJobRow(SQLModel, table=True):
    __tablename__ = "ontology_transfer_upstream_jobs"
    __table_args__ = (
        UniqueConstraint("plan_id", "generation", "event_id", name="uq_ontology_transfer_upstream_jobs_event"),
        UniqueConstraint("id", "plan_id", "generation", name="uq_ontology_transfer_upstream_job_scope"),
        CheckConstraint("operator IN ('AND','OR')", name="ck_ontology_transfer_upstream_job_operator"),
        CheckConstraint("""((group_operator IS NULL AND group_parent_ids IS NULL AND rule_parent_ids IS NULL)
 OR (group_operator IN ('AND','OR') AND group_operator <> operator
     AND array_ndims(group_parent_ids)=1 AND array_lower(group_parent_ids,1)=1
     AND cardinality(group_parent_ids)=2 AND group_parent_ids[1] < group_parent_ids[2]
     AND array_ndims(rule_parent_ids)=1 AND array_lower(rule_parent_ids,1)=1
     AND cardinality(rule_parent_ids)=3 AND rule_parent_ids[1] < rule_parent_ids[2]
     AND rule_parent_ids[2] < rule_parent_ids[3] AND rule_parent_ids @> group_parent_ids)) IS TRUE""", name="ck_ontology_transfer_upstream_job_group"),
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
    operator: str = Field(max_length=3)
    group_operator: str | None = Field(default=None, max_length=3)
    group_parent_ids: list[UUID] | None = Field(default=None, sa_column=Column(ARRAY(Uuid)))
    rule_parent_ids: list[UUID] | None = Field(default=None, sa_column=Column(ARRAY(Uuid)))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    child_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    armed_by: UUID
    dispatch_after: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    state: str = Field(default="pending", max_length=16)
    dispatched_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    finished_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    receipt_id: UUID | None = Field(default=None, foreign_key="ontology_transfer_runs.id")
    message: str | None = Field(default=None, max_length=240)


class UpstreamTriggerJobEventRow(SQLModel, table=True):
    __tablename__ = "ontology_transfer_upstream_job_events"
    __table_args__ = (
        ForeignKeyConstraint(("job_id", "plan_id", "generation"),
                             ("ontology_transfer_upstream_jobs.id", "ontology_transfer_upstream_jobs.plan_id",
                              "ontology_transfer_upstream_jobs.generation"),
                             name="fk_ontology_transfer_job_event_scope"),
        UniqueConstraint("job_id", "event_id", name="uq_ontology_transfer_upstream_job_event"),
    )

    plan_id: UUID = Field(primary_key=True)
    generation: int = Field(primary_key=True)
    event_id: int = Field(sa_column=Column(BigInteger, ForeignKey("ontology_transfer_commit_events.id"), primary_key=True))
    job_id: UUID
