"""Exact-sandbox policy and evaluated write-cadence evidence."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field as DbField, SQLModel

FreshnessState = Literal["not_configured", "not_checked", "healthy", "overdue", "unknown", "paused"]


QualityState = Literal["not_configured", "not_checked", "healthy", "unhealthy", "unknown", "paused"]
DecimalBytes = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,18})$")]
_FINGERPRINT = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class AssetHeapSizePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minBytes: DecimalBytes | None = None
    maxBytes: DecimalBytes | None = None

    @model_validator(mode="after")
    def ordered_bounds(self):
        values = [int(value) for value in (self.minBytes, self.maxBytes) if value is not None]
        if not values or any(value > 2**63 - 1 for value in values) or values != sorted(values):
            raise ValueError("Specify at least one ordered byte bound within PostgreSQL bigint range")
        return self


class AssetSchemaColumn(BaseModel):
    name: str
    type: str
    typmod: int
    nullable: bool
    primaryKeyPosition: int | None = None


class AssetSchemaPreview(BaseModel):
    fingerprint: _FINGERPRINT
    columns: list[AssetSchemaColumn]


class AssetSchemaBaseline(AssetSchemaPreview):
    capturedAt: datetime


class AssetSchemaPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedGeneration: int = Field(ge=1)


class AssetSchemaCaptureRequest(AssetSchemaPreviewRequest):
    expectedFingerprint: _FINGERPRINT


class AssetQualityTransition(BaseModel):
    id: UUID
    generation: int
    state: QualityState
    reason: str | None = None
    checkedAt: datetime | None = None
    heapBytes: DecimalBytes | None = None


class AssetQualityAssessment(BaseModel):
    state: QualityState
    reason: str | None = None
    checkedAt: datetime | None = None
    validUntil: datetime | None = None
    heapBytes: DecimalBytes | None = None
    history: list[AssetQualityTransition] = Field(default_factory=list)


class AssetQualityChecks(BaseModel):
    schema_assessment: AssetQualityAssessment = Field(
        alias="schema", default_factory=lambda: AssetQualityAssessment(state="not_configured"),
    )
    heapSize: AssetQualityAssessment = Field(default_factory=lambda: AssetQualityAssessment(state="not_configured"))


class AssetFreshnessChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planId: UUID
    expectedPlanUpdatedAt: AwareDatetime
    expectedGeneration: int | None = Field(ge=1)
    thresholdMinutes: int = Field(ge=5, le=10080)
    enabled: bool
    schemaCheckEnabled: bool | None = None
    heapSize: AssetHeapSizePolicy | None = None


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
    schemaCheckEnabled: bool = False
    schemaBaseline: AssetSchemaBaseline | None = None
    heapSize: AssetHeapSizePolicy | None = None


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


AssetQualityIncidentKind = Literal["freshness", "schema", "heap_size", "daily_job", "field_time", "minimum_rows"]
AssetQualityIncidentReason = Literal["write_overdue", "schema_drift", "below_minimum", "above_maximum", "target_rejected", "field_lag", "row_threshold"]
AssetQualityIncidentClose = Literal["healthy", "policy_paused", "policy_reviewed", "schema_baseline_captured", "schedule_changed", "target_changed"]


class AssetQualityIncident(BaseModel):
    id: UUID
    generation: int
    kind: AssetQualityIncidentKind
    status: Literal["open", "acknowledged", "recovered", "superseded"]
    reason: AssetQualityIncidentReason
    openedAt: datetime
    lastConfirmedAt: datetime
    acknowledgedAt: datetime | None = None
    closedAt: datetime | None = None
    closeReason: AssetQualityIncidentClose | None = None


class AssetQualityIncidentLists(BaseModel):
    active: list[AssetQualityIncident] = Field(default_factory=list)
    history: list[AssetQualityIncident] = Field(default_factory=list)


class AssetQualityIncidentAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedGeneration: int = Field(ge=1)


class AssetFreshnessResponse(BaseModel):
    available: bool
    policy: AssetFreshnessPolicy | None = None
    assessment: AssetFreshnessAssessment
    history: list[AssetFreshnessTransition] = Field(default_factory=list)
    checks: AssetQualityChecks = Field(default_factory=AssetQualityChecks)
    incidents: AssetQualityIncidentLists = Field(default_factory=AssetQualityIncidentLists)


class AssetFreshnessPolicyRow(SQLModel, table=True):
    __tablename__ = "ontology_asset_freshness_policies"
    __table_args__ = (
        CheckConstraint("threshold_minutes BETWEEN 5 AND 10080 AND generation > 0",
                        name="ck_ontology_asset_freshness_policy_values"),
        CheckConstraint("state IN ('not_checked','healthy','overdue','unknown','paused')",
                        name="ck_ontology_asset_freshness_policy_state"),
        CheckConstraint("(heap_min_bytes IS NULL OR heap_min_bytes >= 0) AND "
                        "(heap_max_bytes IS NULL OR heap_max_bytes >= 0) AND "
                        "(heap_min_bytes IS NULL OR heap_max_bytes IS NULL OR heap_min_bytes <= heap_max_bytes)",
                        name="ck_ontology_asset_heap_size_bounds"),
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
    schema_check_enabled: bool = False
    schema_baseline: dict | None = DbField(default=None, sa_column=Column(JSONB(none_as_null=True)))
    heap_min_bytes: int | None = DbField(default=None, sa_column=Column(BigInteger))
    heap_max_bytes: int | None = DbField(default=None, sa_column=Column(BigInteger))
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


class AssetQualityObservationRow(SQLModel, table=True):
    __tablename__ = "ontology_asset_quality_observations"
    __table_args__ = (
        CheckConstraint("kind IN ('schema','heap_size') AND state IN ('healthy','unhealthy','unknown') "
                        "AND generation > 0 AND (heap_bytes IS NULL OR (kind = 'heap_size' AND heap_bytes >= 0))",
                        name="ck_ontology_asset_quality_observation"),
    )

    target_table: str = DbField(foreign_key="ontology_asset_freshness_policies.target_table", primary_key=True)
    kind: str = DbField(max_length=12, primary_key=True)
    generation: int
    state: str = DbField(max_length=15)
    reason: str | None = DbField(default=None, max_length=40)
    checked_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    heap_bytes: int | None = DbField(default=None, sa_column=Column(BigInteger))


class AssetQualityTransitionRow(SQLModel, table=True):
    __tablename__ = "ontology_asset_quality_transitions"
    __table_args__ = (
        CheckConstraint("kind IN ('schema','heap_size') AND state IN ('healthy','unhealthy','unknown') "
                        "AND generation > 0 AND (heap_bytes IS NULL OR (kind = 'heap_size' AND heap_bytes >= 0))",
                        name="ck_ontology_asset_quality_transition"),
        Index("ix_ontology_asset_quality_history", "target_table", "kind", "generation", "checked_at", "id"),
    )

    id: UUID = DbField(default_factory=uuid4, primary_key=True)
    target_table: str = DbField(foreign_key="ontology_asset_freshness_policies.target_table")
    kind: str = DbField(max_length=12)
    generation: int
    state: str = DbField(max_length=15)
    reason: str | None = DbField(default=None, max_length=40)
    checked_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    heap_bytes: int | None = DbField(default=None, sa_column=Column(BigInteger))


class AssetQualityIncidentRow(SQLModel, table=True):
    __tablename__ = "ontology_asset_quality_incidents"
    __table_args__ = (
        CheckConstraint("generation > 0 AND ((kind = 'freshness' AND reason = 'write_overdue') OR "
                        "(kind = 'schema' AND reason = 'schema_drift') OR "
                        "(kind = 'heap_size' AND reason IN ('below_minimum','above_maximum')) OR "
                        "(kind = 'daily_job' AND reason = 'target_rejected') OR "
                        "(kind = 'field_time' AND reason = 'field_lag') OR "
                        "(kind = 'minimum_rows' AND reason = 'row_threshold'))",
                        name="ck_ontology_asset_quality_incident_kind"),
        CheckConstraint("(closed_at IS NULL) = (close_reason IS NULL) AND "
                        "(close_reason IS NULL OR close_reason IN "
                        "('healthy','policy_paused','policy_reviewed','schema_baseline_captured','schedule_changed','target_changed')) AND "
                        "(acknowledged_at IS NULL) = (acknowledged_by IS NULL)",
                        name="ck_ontology_asset_quality_incident_lifecycle"),
        Index("uq_ontology_asset_quality_incident_active", "target_table", "kind", unique=True,
              postgresql_where=text("closed_at IS NULL")),
        Index("ix_ontology_asset_quality_incident_history", "target_table", "closed_at", "id",
              postgresql_where=text("closed_at IS NOT NULL")),
    )

    id: UUID = DbField(default_factory=uuid4, primary_key=True)
    target_table: str = DbField(foreign_key="ontology_asset_freshness_policies.target_table")
    generation: int
    kind: str = DbField(max_length=12)
    reason: str = DbField(max_length=16)
    opened_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    last_confirmed_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    acknowledged_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
    acknowledged_by: UUID | None = None
    closed_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
    close_reason: str | None = DbField(default=None, max_length=24)
