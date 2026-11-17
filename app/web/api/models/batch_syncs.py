"""Saved sandbox batch sync plans and durable target transaction receipts."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field as DbField, SQLModel

TARGET_SCHEMA = "ts_batch_sync_sandbox"
BATCH_ROWS = 100
BATCH_BYTES = 1_048_576
MAX_MAPPINGS = 32
SourceName = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$")]
TargetName = Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")]


class BatchSyncMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sourceColumn: SourceName
    targetColumn: TargetName


class BatchSyncDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[^\x00-\x1f\x7f]+$")]
    connectionId: UUID
    sourceSchema: SourceName
    sourceTable: SourceName
    sourceKeyColumn: SourceName
    targetTable: TargetName
    mapping: list[BatchSyncMapping] = Field(min_length=1, max_length=MAX_MAPPINGS)

    @model_validator(mode="after")
    def validate_mapping(self):
        if not self.name.strip():
            raise ValueError("Enter a name for the batch sync")
        if self.sourceSchema == TARGET_SCHEMA:
            raise ValueError("A batch sync source cannot use the reserved destination schema")
        source = {item.sourceColumn for item in self.mapping}
        target = {item.targetColumn for item in self.mapping}
        if len(source) != len(self.mapping) or len(target) != len(self.mapping):
            raise ValueError("Column mappings must be one-to-one")
        if self.sourceKeyColumn not in source:
            raise ValueError("Map the selected source key column")
        return self


class BatchSyncPayload(BatchSyncDefinition):
    expectedSourceUpdatedAt: AwareDatetime


class BatchSyncPlan(BatchSyncDefinition):
    id: UUID
    sourceUpdatedAt: datetime
    updatedAt: datetime


class BatchSyncRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID
    expectedUpdatedAt: AwareDatetime


class BatchSyncReceipt(BaseModel):
    id: UUID
    planId: UUID
    status: Literal["committed"] = "committed"
    rowsAffected: int
    recordedAt: datetime
    sourceUpdatedAt: datetime
    targetTable: str


class BatchSyncAttempt(BaseModel):
    id: UUID
    planId: UUID
    requestId: UUID
    planUpdatedAt: datetime
    status: Literal["started", "committed", "replayed", "rejected", "retryable", "outcome_unknown"]
    startedAt: datetime
    finishedAt: datetime | None = None
    receiptId: UUID | None = None
    code: str | None = None
    message: str | None = None
    scheduledJobId: UUID | None = None


class BatchSyncCapabilities(BaseModel):
    enabled: bool
    targetSchema: Literal["ts_batch_sync_sandbox"] = TARGET_SCHEMA
    maxRows: int = BATCH_ROWS
    maxBytes: int = BATCH_BYTES
    scheduledBatchSync: bool = False


class BatchSyncPlanRow(SQLModel, table=True):
    __tablename__ = "ontology_batch_sync_plans"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(mapping) = 'array' AND jsonb_array_length(mapping) BETWEEN 1 AND 32",
                        name="ck_ontology_batch_sync_mapping"),
        Index("ix_ontology_batch_sync_plans_connection", "connection_id", "updated_at"),
    )

    id: UUID = DbField(default_factory=uuid4, primary_key=True)
    name: str = DbField(max_length=120)
    connection_id: UUID
    source_schema: str = DbField(max_length=128)
    source_table: str = DbField(max_length=128)
    source_key_column: str = DbField(max_length=128)
    target_table: str = DbField(max_length=63)
    target_oid: int = DbField(sa_column=Column(BigInteger, nullable=False))
    target_file_node: int = DbField(sa_column=Column(BigInteger, nullable=False))
    mapping: list[dict[str, str]] = DbField(sa_column=Column(JSONB, nullable=False))
    source_updated_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))


class BatchSyncReceiptRow(SQLModel, table=True):
    __tablename__ = "ontology_batch_sync_receipts"
    __table_args__ = (
        UniqueConstraint("plan_id", "request_id", name="uq_ontology_batch_sync_request"),
        CheckConstraint("rows_affected BETWEEN 0 AND 100", name="ck_ontology_batch_sync_rows"),
        Index("ix_ontology_batch_sync_receipts_plan", "plan_id", "recorded_at"),
    )

    id: UUID = DbField(default_factory=uuid4, primary_key=True)
    plan_id: UUID = DbField(foreign_key="ontology_batch_sync_plans.id")
    request_id: UUID
    initiated_by: UUID
    connection_id: UUID
    source_schema: str = DbField(max_length=128)
    source_table: str = DbField(max_length=128)
    target_table: str = DbField(max_length=63)
    plan_updated_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    source_updated_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    rows_affected: int
    recorded_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))


class BatchSyncRequestRow(SQLModel, table=True):
    """Bind an idempotency key to the reviewed plan version, even without a receipt."""

    __tablename__ = "ontology_batch_sync_requests"

    plan_id: UUID = DbField(primary_key=True, foreign_key="ontology_batch_sync_plans.id")
    request_id: UUID = DbField(primary_key=True)
    plan_updated_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))


class BatchSyncAttemptRow(SQLModel, table=True):
    __tablename__ = "ontology_batch_sync_attempts"
    __table_args__ = (
        ForeignKeyConstraint(["plan_id", "request_id"], [
            "ontology_batch_sync_requests.plan_id", "ontology_batch_sync_requests.request_id",
        ]),
        CheckConstraint(
            "(status = 'started' AND finished_at IS NULL AND receipt_id IS NULL AND code IS NULL AND message IS NULL)"
            " OR (status IN ('committed','replayed') AND finished_at IS NOT NULL AND receipt_id IS NOT NULL"
            " AND code IS NULL AND message IS NULL)"
            " OR (status IN ('rejected','retryable','outcome_unknown') AND finished_at IS NOT NULL"
            " AND receipt_id IS NULL AND code IS NOT NULL AND message IS NOT NULL)",
            name="ck_ontology_batch_sync_attempt_status",
        ),
        Index("ix_ontology_batch_sync_attempts_plan", "plan_id", "started_at"),
    )

    id: UUID = DbField(default_factory=uuid4, primary_key=True)
    plan_id: UUID
    request_id: UUID
    initiated_by: UUID
    status: str = DbField(default="started", max_length=16)
    started_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    finished_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
    receipt_id: UUID | None = DbField(default=None, foreign_key="ontology_batch_sync_receipts.id")
    code: str | None = DbField(default=None, max_length=40)
    message: str | None = DbField(default=None, max_length=240)
    scheduled_job_id: UUID | None = DbField(default=None, foreign_key="ontology_batch_sync_schedule_jobs.id")
