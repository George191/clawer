"""Typed, fixed-mapping local outbound export and its durable sender ledger."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import CheckConstraint, Column, DateTime, Index
from sqlmodel import Field as DbField, SQLModel

ExportOutcome = Literal["started", "committed", "replayed", "rejected", "retryable", "outcome_unknown"]


class TableExportReview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120, pattern=r"^[^\x00-\x1f\x7f]+$")
    expectedUpdatedAt: AwareDatetime | None


class TableExportPlan(BaseModel):
    id: UUID
    name: str
    updatedAt: datetime


class TableExportRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID
    expectedUpdatedAt: AwareDatetime


class TableExportMapping(BaseModel):
    sourceColumn: str
    targetColumn: str
    type: str


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


class TableExportReceipt(BaseModel):
    id: UUID
    requestId: UUID
    status: Literal["committed"] = "committed"
    rowsAffected: int
    recordedAt: datetime


class TableExportAttempt(BaseModel):
    id: UUID
    requestId: UUID
    planUpdatedAt: datetime
    status: ExportOutcome
    startedAt: datetime
    finishedAt: datetime | None = None
    receiptId: UUID | None = None
    code: str | None = None
    message: str | None = None


class TableExportLineageEndpoint(BaseModel):
    schema_name: str = Field(alias="schema")
    table: str


class TableExportLineageDestination(TableExportLineageEndpoint):
    label: str


class TableExportLineageDeclaration(BaseModel):
    id: UUID
    name: str
    reviewedAt: datetime
    state: Literal["enabled", "inactive", "stale"]


class TableExportLineageReceipt(BaseModel):
    id: UUID
    recordedAt: datetime
    rowsAffected: int
    reviewedAt: datetime
    reviewRelation: Literal["current_review", "earlier_review", "different_binding"]
    observed: bool


class TableExportLineage(BaseModel):
    source: TableExportLineageEndpoint
    destination: TableExportLineageDestination
    declaration: TableExportLineageDeclaration | None
    receipts: list[TableExportLineageReceipt]
    observed: bool
    latestObservation: TableExportLineageReceipt | None


class TableExportPlanRow(SQLModel, table=True):
    __tablename__ = "ontology_table_export_plan"
    __table_args__ = (CheckConstraint("singleton = true", name="ck_ontology_table_export_singleton"),)

    singleton: bool = DbField(default=True, primary_key=True)
    id: UUID = DbField(default_factory=uuid4)
    name: str = DbField(max_length=120)
    reviewed_by: UUID
    binding_digest: str = DbField(max_length=64)
    updated_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))


class TableExportRequestRow(SQLModel, table=True):
    __tablename__ = "ontology_table_export_requests"

    request_id: UUID = DbField(primary_key=True)
    plan_updated_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    binding_digest: str = DbField(max_length=64)
    payload_digest: str | None = DbField(default=None, max_length=64)


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
