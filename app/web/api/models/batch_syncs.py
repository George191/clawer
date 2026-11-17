"""Saved sandbox batch sync plans and durable target transaction receipts."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKeyConstraint, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field as DbField, SQLModel

TARGET_SCHEMA = "ts_batch_sync_sandbox"
INCREMENTAL_TARGET = "committed_batch_fixture"
INCREMENTAL_SOURCE_SCHEMA = "batch_sync_stream"
INCREMENTAL_SOURCE_TABLE = "batches"
BatchSyncMode = Literal["snapshot", "incremental"]
BatchSyncOutcome = Literal["committed", "no_new_data"]
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
    mode: BatchSyncMode = Field(default="snapshot", exclude_if=lambda value: value == "snapshot")
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

    @model_validator(mode="after")
    def validate_managed_scope(self) -> Self:
        if self.mode == "incremental":
            if not managed_batch_scope(self):
                raise ValueError("Incremental mode requires the fixed synthetic stream and ID/value target mapping")
        elif self.targetTable == INCREMENTAL_TARGET or self.sourceSchema == INCREMENTAL_SOURCE_SCHEMA:
            raise ValueError("The committed stream and incremental target are reserved for managed incremental mode")
        return self


def managed_batch_scope(plan: BatchSyncDefinition) -> bool:
    return (plan.sourceSchema == INCREMENTAL_SOURCE_SCHEMA and plan.sourceTable == INCREMENTAL_SOURCE_TABLE
            and plan.sourceKeyColumn == "id" and plan.targetTable == INCREMENTAL_TARGET
            and {(item.sourceColumn, item.targetColumn) for item in plan.mapping} == {("id", "id"), ("value", "value")})


class BatchSyncPlan(BatchSyncDefinition):
    id: UUID
    appliedThrough: str | None = Field(default=None, exclude_if=lambda value: value is None)
    sourceIdentityReviewed: Literal[True] | None = Field(default=None, exclude_if=lambda v: v is None)
    sourceUpdatedAt: datetime
    updatedAt: datetime


class BatchSyncRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID
    expectedUpdatedAt: AwareDatetime


class BatchSyncReceipt(BaseModel):
    id: UUID
    sourceIdentityVerified: Literal[True] | None = Field(default=None, exclude_if=lambda v: v is None)
    planId: UUID
    status: BatchSyncOutcome = "committed"
    mode: Literal["incremental"] | None = Field(default=None, exclude_if=lambda value: value is None)
    fromExclusive: str | None = Field(default=None, exclude_if=lambda value: value is None)
    throughInclusive: str | None = Field(default=None, exclude_if=lambda value: value is None)
    sourceHighWater: str | None = Field(default=None, exclude_if=lambda value: value is None)
    eventsRead: int | None = Field(default=None, exclude_if=lambda value: value is None)
    manifestHash: str | None = Field(default=None, exclude_if=lambda value: value is None)
    rowsAffected: int
    recordedAt: datetime
    sourceUpdatedAt: datetime
    targetTable: str


class BatchSyncAttempt(BaseModel):
    id: UUID
    planId: UUID
    requestId: UUID
    planUpdatedAt: datetime
    status: Literal["started", "committed", "no_new_data", "replayed", "rejected", "retryable", "outcome_unknown"]
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
    incrementalLineage: Literal[True] | None = Field(default=None, exclude_if=lambda value: value is None)
    incrementalBatchSync: Literal[True] | None = Field(default=None, exclude_if=lambda value: value is None)
    incrementalMaxRows: int | None = Field(default=None, exclude_if=lambda value: value is None)
    incrementalMaxBytes: int | None = Field(default=None, exclude_if=lambda value: value is None)
    sourceIdentityRequired: Literal[True] | None = Field(default=None, exclude_if=lambda v: v is None)


class BatchSyncPlanRow(SQLModel, table=True):
    __tablename__ = "ontology_batch_sync_plans"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(mapping) = 'array' AND jsonb_array_length(mapping) BETWEEN 1 AND 32",
                        name="ck_ontology_batch_sync_mapping"),
        CheckConstraint(
            """((mode = 'snapshot' AND source_stream_digest IS NULL AND
                 ((source_identity_fingerprint IS NULL AND source_oid IS NULL AND source_file_node IS NULL) OR
                  (source_identity_fingerprint ~ '^[0-9a-f]{64}$' AND source_oid > 0 AND source_file_node > 0)))
                OR (mode = 'incremental' AND source_oid IS NULL AND source_file_node IS NULL
                    AND source_identity_fingerprint ~ '^[0-9a-f]{64}$' AND source_stream_digest ~ '^[0-9a-f]{64}$'
                    AND source_schema = 'batch_sync_stream' AND source_table = 'batches'
                    AND source_key_column = 'id' AND target_table = 'committed_batch_fixture')) IS TRUE""",
            name="ck_ontology_batch_sync_mode_pin",
        ),
        Index("ix_ontology_batch_sync_plans_connection", "connection_id", "updated_at"),
    )

    id: UUID = DbField(default_factory=uuid4, primary_key=True)
    name: str = DbField(max_length=120)
    connection_id: UUID
    source_schema: str = DbField(max_length=128)
    source_table: str = DbField(max_length=128)
    source_key_column: str = DbField(max_length=128)
    target_table: str = DbField(max_length=63)
    mode: str = DbField(default="snapshot", max_length=16)
    source_stream_digest: str | None = DbField(default=None, max_length=64)
    source_identity_fingerprint: str | None = DbField(default=None, max_length=64)
    source_oid: int | None = DbField(default=None, sa_column=Column(BigInteger))
    source_file_node: int | None = DbField(default=None, sa_column=Column(BigInteger))
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
        CheckConstraint(
            """((from_exclusive IS NULL AND through_inclusive IS NULL AND source_high_water IS NULL
                 AND events_read IS NULL AND manifest_hash IS NULL AND outcome = 'committed')
               OR (from_exclusive >= 0 AND through_inclusive >= from_exclusive
                   AND source_high_water >= through_inclusive AND source_identity_verified IS TRUE
                   AND target_table = 'committed_batch_fixture'
                   AND ((outcome = 'no_new_data' AND rows_affected = 0 AND events_read = 0 AND manifest_hash IS NULL
                         AND through_inclusive = from_exclusive AND source_high_water = from_exclusive)
                        OR (outcome = 'committed' AND events_read = rows_affected AND rows_affected BETWEEN 1 AND 50
                            AND through_inclusive - from_exclusive = 1 AND manifest_hash ~ '^[0-9a-f]{64}$')))) IS TRUE""",
            name="ck_ontology_batch_sync_receipt_window",
        ),
        Index("ix_ontology_batch_sync_receipts_plan", "plan_id", "recorded_at"),
        Index("ix_batch_receipt_incremental_timeline", "connection_id", "recorded_at", "id",
              postgresql_where=text("from_exclusive IS NOT NULL AND source_identity_verified IS TRUE "
                                    "AND source_schema = 'batch_sync_stream' AND source_table = 'batches' "
                                    "AND target_table = 'committed_batch_fixture'")),
        Index("ix_batch_receipt_incremental_positive", "connection_id", "recorded_at", "id",
              postgresql_where=text("from_exclusive IS NOT NULL AND source_identity_verified IS TRUE "
                                    "AND source_schema = 'batch_sync_stream' AND source_table = 'batches' "
                                    "AND target_table = 'committed_batch_fixture' "
                                    "AND outcome = 'committed' AND rows_affected > 0")),
        Index("uq_ontology_batch_sync_incremental_ordinal", "through_inclusive", unique=True,
              postgresql_where=text("outcome = 'committed' AND through_inclusive IS NOT NULL")),
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
    source_identity_verified: bool | None = None
    outcome: str = DbField(default="committed", max_length=16)
    from_exclusive: int | None = DbField(default=None, sa_column=Column(BigInteger))
    through_inclusive: int | None = DbField(default=None, sa_column=Column(BigInteger))
    source_high_water: int | None = DbField(default=None, sa_column=Column(BigInteger))
    events_read: int | None = None
    manifest_hash: str | None = DbField(default=None, max_length=64)
    recorded_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))


class BatchSyncWatermarkRow(SQLModel, table=True):
    __tablename__ = "ontology_batch_sync_incremental_watermark"
    __table_args__ = (
        CheckConstraint("singleton IS TRUE AND applied_through >= 0 AND target_oid > 0 AND target_file_node > 0 "
                        "AND source_identity_fingerprint ~ '^[0-9a-f]{64}$' AND source_stream_digest ~ '^[0-9a-f]{64}$'",
                        name="ck_ontology_batch_sync_watermark"),
    )

    singleton: bool = DbField(default=True, primary_key=True)
    connection_id: UUID
    source_identity_fingerprint: str = DbField(max_length=64)
    source_stream_digest: str = DbField(max_length=64)
    target_oid: int = DbField(sa_column=Column(BigInteger, nullable=False))
    target_file_node: int = DbField(sa_column=Column(BigInteger, nullable=False))
    applied_through: int = DbField(default=0, sa_column=Column(BigInteger, nullable=False))


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
            " OR (status IN ('committed','no_new_data','replayed') AND finished_at IS NOT NULL AND receipt_id IS NOT NULL"
            " AND code IS NULL AND message IS NULL)"
            " OR (status IN ('rejected','retryable','outcome_unknown') AND finished_at IS NOT NULL"
            " AND receipt_id IS NULL AND code IS NOT NULL AND message IS NOT NULL)",
            name="ck_ontology_batch_sync_attempt_status",
        ),
        CheckConstraint("incremental_scope ~ '^[0-9a-f]{64}$'", name="ck_batch_attempt_incremental_scope"),
        Index("ix_batch_attempt_incremental_timeline", "incremental_scope", "started_at", "id",
              postgresql_where=text("incremental_scope IS NOT NULL")),
        Index("ix_batch_attempt_legacy_timeline", "started_at", "id",
              postgresql_where=text("incremental_scope IS NULL AND receipt_id IS NOT NULL")),
        Index("ix_ontology_batch_sync_attempts_plan", "plan_id", "started_at"),
    )

    id: UUID = DbField(default_factory=uuid4, primary_key=True)
    plan_id: UUID
    request_id: UUID
    initiated_by: UUID
    status: str = DbField(default="started", max_length=16)
    incremental_scope: str | None = DbField(default=None, max_length=64)
    started_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    finished_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
    receipt_id: UUID | None = DbField(default=None, foreign_key="ontology_batch_sync_receipts.id")
    code: str | None = DbField(default=None, max_length=40)
    message: str | None = DbField(default=None, max_length=240)
    scheduled_job_id: UUID | None = DbField(default=None, foreign_key="ontology_batch_sync_schedule_jobs.id")
