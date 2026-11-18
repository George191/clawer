"""Typed, fixed-mapping local outbound export and its durable sender ledger."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKeyConstraint, Index
from sqlmodel import Field as DbField, SQLModel

SYNTHETIC_EXPORT_MARKING = "sandbox-synthetic"
ExportMarking = Literal["sandbox-synthetic"]
ExportDestination = Literal["primary", "secondary"]
ExportWriteMode = Literal["upsert", "append"]

ExportOutcome = Literal["started", "committed", "replayed", "rejected", "retryable", "outcome_unknown"]


class TableExportReview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120, pattern=r"^[^\x00-\x1f\x7f]+$")
    expectedUpdatedAt: AwareDatetime | None
    writeMode: ExportWriteMode | None = None


class TableExportPlan(BaseModel):
    id: UUID
    name: str
    updatedAt: datetime
    writeMode: Literal["append"] | None = Field(default=None, exclude_if=lambda value: value is None)


class TableExportRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID
    expectedUpdatedAt: AwareDatetime
    expectedGrantGeneration: int = Field(ge=1)
    writeMode: ExportWriteMode | None = None


class TableExportGrantChange(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    approved: bool = Field(strict=True)
    expectedPlanUpdatedAt: AwareDatetime
    expectedGeneration: int | None = Field(ge=1)
    marking: ExportMarking
    reason: str = Field(min_length=1, max_length=240, pattern=r"^[^\x00-\x1f\x7f]+$")
    writeMode: ExportWriteMode | None = None


class TableExportGrant(BaseModel):
    marking: ExportMarking = SYNTHETIC_EXPORT_MARKING
    available: bool
    effective: bool
    generation: int | None = None
    status: Literal["not_granted", "active", "stale", "revoked"]
    planUpdatedAt: datetime | None = None
    approvedAt: datetime | None = None
    revokedAt: datetime | None = None
    writeMode: Literal["append"] | None = Field(default=None, exclude_if=lambda value: value is None)


class TableExportGrantEvent(BaseModel):
    id: UUID
    action: Literal["approved", "revoked"]
    generation: int
    marking: ExportMarking
    recordedAt: datetime
    planUpdatedAt: datetime
    reason: str
    writeMode: Literal["append"] | None = Field(default=None, exclude_if=lambda value: value is None)


class TableExportMapping(BaseModel):
    sourceColumn: str
    targetColumn: str
    type: str


class TableExportDestinationOption(BaseModel):
    id: ExportDestination
    label: str


class TableExportCapabilities(BaseModel):
    enabled: bool
    provisioned: bool
    sourceSchema: str
    sourceTable: str
    targetSchema: str
    targetTable: str
    maxRows: int
    maxCaptionBytes: int
    mapping: list[TableExportMapping]
    approvedDestinations: list[TableExportDestinationOption] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    destinationLabel: str | None = Field(default=None, exclude_if=lambda value: value is None)
    appendAvailable: Literal[True] | None = Field(default=None, exclude_if=lambda value: value is None)


class TableExportSnapshotPreviewCapability(BaseModel):
    available: bool
    configRevision: UUID | None = Field(default=None, exclude_if=lambda value: value is None)


class TableExportSnapshotPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedConfigRevision: UUID


class TableExportSnapshotPreview(BaseModel):
    configRevision: UUID
    sourceRows: int = Field(ge=1, le=100)
    targetRows: int = Field(ge=0, le=100)
    added: int = Field(ge=0, le=100)
    changed: int = Field(ge=0, le=100)
    unchanged: int = Field(ge=0, le=100)
    wouldRemove: int = Field(ge=0, le=100)


class TableExportReceipt(BaseModel):
    id: UUID
    requestId: UUID
    status: Literal["committed"] = "committed"
    rowsAffected: int
    recordedAt: datetime
    grantGeneration: int
    writeMode: Literal["append"] | None = Field(default=None, exclude_if=lambda value: value is None)


class TableExportAttempt(BaseModel):
    id: UUID
    requestId: UUID
    planUpdatedAt: datetime
    grantGeneration: int
    status: ExportOutcome
    startedAt: datetime
    finishedAt: datetime | None = None
    receiptId: UUID | None = None
    code: str | None = None
    message: str | None = None
    writeMode: Literal["append"] | None = Field(default=None, exclude_if=lambda value: value is None)


class TableExportLineageEndpoint(BaseModel):
    schema_name: str = Field(alias="schema")
    table: str


class TableExportLineageDestination(TableExportLineageEndpoint):
    label: str


class TableExportLineageDeclaration(BaseModel):
    id: UUID
    name: str
    reviewedAt: datetime
    state: Literal["enabled", "inactive", "stale", "blocked"]
    writeMode: Literal["append"] | None = Field(default=None, exclude_if=lambda value: value is None)


class TableExportLineageReceipt(BaseModel):
    id: UUID
    recordedAt: datetime
    rowsAffected: int
    reviewedAt: datetime
    reviewRelation: Literal["current_review", "earlier_review", "different_binding"]
    observed: bool
    writeMode: Literal["append"] | None = Field(default=None, exclude_if=lambda value: value is None)


class TableExportLineage(BaseModel):
    source: TableExportLineageEndpoint
    destination: TableExportLineageDestination
    declaration: TableExportLineageDeclaration | None
    receipts: list[TableExportLineageReceipt]
    observed: bool
    latestObservation: TableExportLineageReceipt | None


class TableExportPlanRow(SQLModel, table=True):
    __tablename__ = "ontology_table_export_plan"
    __table_args__ = (
        CheckConstraint("write_mode IN ('upsert', 'append')", name="ck_table_export_plan_write_mode"),
        CheckConstraint("singleton = true", name="ck_ontology_table_export_singleton"),
        CheckConstraint("(destination = 'primary' AND write_mode = 'upsert' AND receiver_proof IS NULL) OR "
                        "((destination = 'secondary' OR (destination = 'primary' AND write_mode = 'append')) "
                        "AND (receiver_proof ~ '^[0-9a-f]{64}$') IS TRUE)",
                        name="ck_ontology_table_export_plan_destination"),
    )

    singleton: bool = DbField(default=True)
    write_mode: str = DbField(default="upsert", max_length=6)
    destination: str = DbField(default="primary", max_length=9, primary_key=True)
    receiver_proof: str | None = DbField(default=None, max_length=64)
    id: UUID = DbField(default_factory=uuid4)
    name: str = DbField(max_length=120)
    reviewed_by: UUID
    binding_digest: str = DbField(max_length=64)
    updated_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))


class TableExportGrantRow(SQLModel, table=True):
    """Immutable approval or revocation event for the singleton export scope."""

    __tablename__ = "ontology_table_export_grants"
    __table_args__ = (
        CheckConstraint("write_mode IN ('upsert', 'append')", name="ck_table_export_grants_write_mode"),
        CheckConstraint("destination IN ('primary', 'secondary')", name="ck_ontology_table_export_grants_destination"),
        CheckConstraint("generation > 0", name="ck_ontology_table_export_grant_generation"),
        CheckConstraint("marking = 'sandbox-synthetic'", name="ck_ontology_table_export_grant_marking"),
        CheckConstraint("action IN ('approved', 'revoked')", name="ck_ontology_table_export_grant_action"),
    )

    write_mode: str = DbField(default="upsert", max_length=6)
    destination: str = DbField(default="primary", max_length=9, primary_key=True)
    generation: int = DbField(primary_key=True)
    id: UUID = DbField(default_factory=uuid4, unique=True)
    action: str = DbField(max_length=8)
    marking: str = DbField(max_length=17)
    reason: str = DbField(max_length=240)
    actor_id: UUID
    scope_digest: str = DbField(max_length=64)
    plan_updated_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    recorded_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))


class TableExportRequestRow(SQLModel, table=True):
    __tablename__ = "ontology_table_export_requests"
    __table_args__ = (
        CheckConstraint("write_mode IN ('upsert', 'append')", name="ck_table_export_requests_write_mode"),
        CheckConstraint("destination IN ('primary', 'secondary')", name="ck_ontology_table_export_requests_destination"),
        ForeignKeyConstraint(["destination", "grant_generation"],
                             ["ontology_table_export_grants.destination", "ontology_table_export_grants.generation"],
                             name="fk_table_export_request_grant"),
        Index("ix_ontology_table_export_requests_grant_generation", "destination", "grant_generation"),
    )

    write_mode: str = DbField(default="upsert", max_length=6)
    destination: str = DbField(default="primary", max_length=9)
    request_id: UUID = DbField(primary_key=True)
    plan_updated_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    binding_digest: str = DbField(max_length=64)
    payload_digest: str | None = DbField(default=None, max_length=64)
    grant_generation: int | None = DbField(default=None)


class TableExportReceiptRow(SQLModel, table=True):
    __tablename__ = "ontology_table_export_receipts"
    __table_args__ = (CheckConstraint("rows_affected BETWEEN 0 AND 100", name="ck_ontology_table_export_rows"),)

    id: UUID = DbField(default_factory=uuid4, primary_key=True)
    request_id: UUID = DbField(foreign_key="ontology_table_export_requests.request_id", unique=True)
    rows_affected: int
    recorded_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))


class TableExportAttemptRow(SQLModel, table=True):
    __tablename__ = "ontology_table_export_attempts"
    __table_args__ = (
        CheckConstraint(
            "(status = 'started' AND finished_at IS NULL AND receipt_id IS NULL AND code IS NULL AND message IS NULL)"
            " OR (status IN ('committed','replayed') AND finished_at IS NOT NULL AND receipt_id IS NOT NULL"
            " AND code IS NULL AND message IS NULL)"
            " OR (status IN ('rejected','retryable','outcome_unknown') AND finished_at IS NOT NULL"
            " AND receipt_id IS NULL AND code IS NOT NULL AND message IS NOT NULL)",
            name="ck_ontology_table_export_attempt_status",
        ),
        Index("ix_ontology_table_export_attempt_started", "started_at"),
    )

    id: UUID = DbField(default_factory=uuid4, primary_key=True)
    request_id: UUID = DbField(foreign_key="ontology_table_export_requests.request_id")
    initiated_by: UUID
    status: str = DbField(default="started", max_length=16)
    started_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    finished_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
    receipt_id: UUID | None = DbField(default=None, foreign_key="ontology_table_export_receipts.id")
    code: str | None = DbField(default=None, max_length=40)
    message: str | None = DbField(default=None, max_length=240)
