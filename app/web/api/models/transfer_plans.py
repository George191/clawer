"""Configuration-only transfer drafts for internal warehouse tables."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index, UniqueConstraint, text
from sqlmodel import Field as DbField, SQLModel

WarehouseSchema = Annotated[str, Field(pattern=r"^ts_[a-z0-9_]+$", max_length=63)]
TableName = Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_]{0,62}$")]
DailyUtcTime = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]


class TransferPlanPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    sourceSchema: WarehouseSchema
    sourceTable: TableName
    targetSchema: WarehouseSchema
    targetTable: TableName
    writeMode: Literal["append", "upsert"]
    trigger: Literal["manual", "scheduled", "weekly", "upstream"]
    scheduleTime: DailyUtcTime | None = None
    scheduleWeekdayUtc: int | None = Field(default=None, strict=True, ge=1, le=7, exclude_if=lambda value: value is None)
    incrementalCursor: TableName | None = None

    @model_validator(mode="after")
    def validate_plan(self):
        if (self.sourceSchema, self.sourceTable) == (self.targetSchema, self.targetTable):
            raise ValueError("Source and target tables must be different")
        if (self.trigger in {"scheduled", "weekly"}) != (self.scheduleTime is not None):
            raise ValueError("A UTC time is required only for a daily or weekly trigger")
        if (self.trigger == "weekly") != (self.scheduleWeekdayUtc is not None):
            raise ValueError("An ISO UTC weekday is required only for a weekly trigger")
        if self.trigger == "weekly" and (
            self.writeMode != "upsert" or self.sourceSchema != "ts_transfer_sandbox" or self.targetSchema != "ts_transfer_sandbox"
        ):
            raise ValueError("A weekly trigger requires a sandbox Upsert plan")
        if self.trigger == "upstream" and (
            self.writeMode != "upsert" or self.sourceSchema != "ts_transfer_sandbox" or self.targetSchema != "ts_transfer_sandbox"
        ):
            raise ValueError("An upstream trigger requires a sandbox Upsert plan")
        if self.incrementalCursor is not None and (
            self.trigger != "manual" or self.writeMode != "append"
            or self.sourceSchema != "ts_transfer_sandbox" or self.targetSchema != "ts_transfer_sandbox"
        ):
            raise ValueError("An incremental cursor is supported only for manual sandbox Append plans")
        return self


class TransferPlan(TransferPlanPayload):
    id: UUID
    status: Literal["draft"] = "draft"
    updatedAt: datetime


class TransferPreflightIssue(BaseModel):
    code: str
    message: str
    column: str | None = None


class TransferPreflight(BaseModel):
    status: Literal["compatible", "blocked"]
    issues: list[TransferPreflightIssue]
    checkedColumns: int


class TransferRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requestId: UUID
    expectedUpdatedAt: AwareDatetime


class TransferRunCapabilities(BaseModel):
    enabled: bool
    sourceSchema: str
    targetSchema: str
    maxRows: int
    incrementalAppend: bool
    scheduledUpsert: bool
    weeklyUpsert: Literal[True] | None = Field(default=None, exclude_if=lambda value: value is None)
    upstreamUpsert: bool


class TransferRun(BaseModel):
    id: UUID
    planId: UUID
    status: Literal["committed"] = "committed"
    rowsAffected: int
    sourceSchema: str
    sourceTable: str
    targetSchema: str
    targetTable: str
    recordedAt: datetime
    writeMode: Literal["append", "upsert"]
    cursorThrough: str | None = None
    hasMore: bool | None = None


class TransferAttempt(BaseModel):
    id: UUID
    planId: UUID
    status: Literal["started", "committed", "replayed", "rejected", "outcome_unknown"]
    startedAt: datetime
    finishedAt: datetime | None = None
    receiptId: UUID | None = None
    message: str | None = None
    upstreamJobId: UUID | None = None


DAILY_OR_LEGACY_PLAN_CHECK = (
    "(trigger = 'manual' AND schedule_time IS NULL) OR "
    "(trigger = 'scheduled' AND schedule_time IS NOT NULL AND "
    "schedule_time ~ '^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$') OR "
    "(trigger = 'upstream' AND schedule_time IS NULL AND write_mode = 'upsert' "
    "AND source_schema = 'ts_transfer_sandbox' AND target_schema = 'ts_transfer_sandbox')"
)
WEEKLY_PLAN_CHECK = (
    f"((({DAILY_OR_LEGACY_PLAN_CHECK}) AND schedule_weekday_utc IS NULL) OR "
    "(trigger = 'weekly' AND schedule_time IS NOT NULL "
    "AND schedule_time ~ '^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$' "
    "AND schedule_weekday_utc BETWEEN 1 AND 7 "
    "AND write_mode = 'upsert' AND source_schema = 'ts_transfer_sandbox' AND target_schema = 'ts_transfer_sandbox')) IS TRUE"
)


class TransferPlanRow(SQLModel, table=True):
    __tablename__ = "ontology_transfer_plans"
    __table_args__ = (
        CheckConstraint("write_mode IN ('append', 'upsert')", name="ck_ontology_transfer_plans_mode"),
        CheckConstraint(WEEKLY_PLAN_CHECK, name="ck_ontology_transfer_plans_schedule"),
        CheckConstraint("source_schema <> target_schema OR source_table <> target_table",
                        name="ck_ontology_transfer_plans_endpoints"),
        CheckConstraint("incremental_cursor IS NULL OR (write_mode = 'append' AND trigger = 'manual' "
                        "AND source_schema = 'ts_transfer_sandbox' AND target_schema = 'ts_transfer_sandbox')",
                        name="ck_ontology_transfer_plans_incremental"),
        Index("ix_ontology_transfer_plans_source", "source_schema"),
        Index("ix_ontology_transfer_plans_target", "target_schema"),
    )

    id: UUID = DbField(default_factory=uuid4, primary_key=True)
    name: str = DbField(max_length=120)
    source_schema: str = DbField(max_length=63)
    source_table: str = DbField(max_length=63)
    target_schema: str = DbField(max_length=63)
    target_table: str = DbField(max_length=63)
    write_mode: str = DbField(max_length=6)
    trigger: str = DbField(max_length=9)
    schedule_time: str | None = DbField(default=None, max_length=5)
    schedule_weekday_utc: int | None = None
    incremental_cursor: str | None = DbField(default=None, max_length=63)
    updated_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))


class TransferRunRow(SQLModel, table=True):
    __tablename__ = "ontology_transfer_runs"
    __table_args__ = (
        UniqueConstraint("plan_id", "request_id", name="uq_ontology_transfer_runs_request"),
        CheckConstraint("rows_affected BETWEEN 0 AND 100", name="ck_ontology_transfer_runs_size"),
        CheckConstraint("write_mode IN ('append', 'upsert')", name="ck_ontology_transfer_runs_mode"),
        CheckConstraint("source_schema = 'ts_transfer_sandbox' AND target_schema = 'ts_transfer_sandbox' "
                        "AND source_table <> target_table", name="ck_ontology_transfer_runs_sandbox"),
        CheckConstraint("(target_oid IS NULL) = (target_filenode IS NULL)",
                        name="ck_ontology_transfer_runs_physical_target"),
        Index("ix_ontology_transfer_runs_freshness", "plan_id", "plan_updated_at", "target_oid",
              "target_filenode", "recorded_at", "id", postgresql_where=text(
                  "rows_affected > 0 AND target_oid IS NOT NULL AND target_filenode IS NOT NULL")),
        CheckConstraint("""((scheduled_source_oid IS NULL AND scheduled_source_filenode IS NULL) OR
                        (scheduled_source_oid BETWEEN 1 AND 4294967295
                        AND scheduled_source_filenode BETWEEN 1 AND 4294967295
                        AND target_oid > 0 AND target_filenode > 0 AND rows_affected > 0
                        AND write_mode = 'upsert' AND cursor_through IS NULL AND has_more IS NULL
                        AND initiated_by = '827a40a4-ea50-5e8f-a4f8-191ebfadd1a6'::uuid)) IS TRUE""",
                        name="ck_ontology_transfer_run_scheduled_source"),
        Index("ix_ontology_transfer_run_scheduled_source", "source_schema", "source_table", "recorded_at", "id",
              postgresql_where=text("scheduled_source_oid IS NOT NULL")),
        Index("ix_ontology_transfer_runs_plan", "plan_id", "recorded_at"),
        Index("ix_ontology_transfer_runs_source", "source_schema", "source_table", "recorded_at"),
        Index("ix_ontology_transfer_runs_target", "target_schema", "target_table", "recorded_at"),
        Index("ix_ontology_transfer_timeline_receipts", "target_schema", "target_table", "recorded_at", "id"),
        Index("ix_ontology_transfer_timeline_current", "target_schema", "target_table", "target_oid",
              "target_filenode", "recorded_at", "id", postgresql_where=text("rows_affected > 0")),
        Index("uq_ontology_transfer_runs_append_route", "source_schema", "source_table", "target_schema",
              "target_table", unique=True, postgresql_where=text("write_mode = 'append' AND has_more IS NULL")),
    )

    id: UUID = DbField(default_factory=uuid4, primary_key=True)
    plan_id: UUID = DbField(foreign_key="ontology_transfer_plans.id")
    request_id: UUID
    initiated_by: UUID
    plan_updated_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    source_schema: str = DbField(max_length=63)
    source_table: str = DbField(max_length=63)
    target_schema: str = DbField(max_length=63)
    target_table: str = DbField(max_length=63)
    target_oid: int | None = DbField(default=None, sa_column=Column(BigInteger))
    target_filenode: int | None = DbField(default=None, sa_column=Column(BigInteger))
    scheduled_source_oid: int | None = DbField(default=None, sa_column=Column(BigInteger))
    scheduled_source_filenode: int | None = DbField(default=None, sa_column=Column(BigInteger))
    write_mode: str = DbField(max_length=6)
    rows_affected: int
    recorded_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    cursor_through: int | None = DbField(default=None, sa_column=Column(BigInteger))
    has_more: bool | None = None


class TransferCheckpointRow(SQLModel, table=True):
    __tablename__ = "ontology_transfer_checkpoints"
    __table_args__ = (
        UniqueConstraint("source_table", "target_table", name="uq_ontology_transfer_checkpoints_route"),
    )

    plan_id: UUID = DbField(primary_key=True, foreign_key="ontology_transfer_plans.id")
    source_table: str = DbField(max_length=63)
    target_table: str = DbField(max_length=63)
    cursor_column: str = DbField(max_length=63)
    source_oid: int = DbField(sa_column=Column(BigInteger, nullable=False))
    source_node: int = DbField(sa_column=Column(BigInteger, nullable=False))
    target_oid: int = DbField(sa_column=Column(BigInteger, nullable=False))
    target_node: int = DbField(sa_column=Column(BigInteger, nullable=False))
    cursor_through: int | None = DbField(default=None, sa_column=Column(BigInteger))


class TransferAttemptRow(SQLModel, table=True):
    __tablename__ = "ontology_transfer_attempts"
    __table_args__ = (
        CheckConstraint("status IN ('started', 'committed', 'replayed', 'rejected', 'outcome_unknown')",
                        name="ck_ontology_transfer_attempts_status"),
        CheckConstraint("(target_table IS NULL) = (plan_updated_at IS NULL) "
                        "AND (target_table IS NULL) = (source_table IS NULL) "
                        "AND (target_table IS NULL) = (scope_trigger IS NULL) "
                        "AND (target_oid IS NULL) = (target_filenode IS NULL) "
                        "AND (target_table IS NOT NULL OR target_oid IS NULL) "
                        "AND (scope_trigger IS NULL OR scope_trigger IN ('manual','daily_utc','upstream','week_utc')) "
                        "AND (scope_trigger IS NULL OR scope_trigger NOT IN ('daily_utc','week_utc') OR schedule_job_id IS NOT NULL)",
                        name="ck_ontology_transfer_attempt_timeline_scope"),
        Index("ix_ontology_transfer_attempts_plan", "plan_id", "started_at"),
        Index("ix_ontology_transfer_attempts_request", "plan_id", "request_id"),
        Index("ix_ontology_transfer_attempt_timeline", "target_table", "started_at", "id",
              postgresql_where=text("target_table IS NOT NULL")),
        Index("ix_ontology_transfer_attempt_clock_receipt", "receipt_id",
              postgresql_where=text("scope_trigger IN ('daily_utc','week_utc') AND status = 'committed' "
                                    "AND receipt_id IS NOT NULL")),
        Index("ix_ontology_transfer_attempt_manual_receipt", "receipt_id",
              postgresql_where=text("scope_trigger = 'manual' AND status = 'committed' AND receipt_id IS NOT NULL")),
    )

    id: UUID = DbField(default_factory=uuid4, primary_key=True)
    upstream_job_id: UUID | None = DbField(default=None, foreign_key="ontology_transfer_upstream_jobs.id")
    schedule_job_id: UUID | None = DbField(default=None, foreign_key="ontology_transfer_schedule_jobs.id")
    plan_updated_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
    source_table: str | None = DbField(default=None, max_length=63)
    target_table: str | None = DbField(default=None, max_length=63)
    target_oid: int | None = DbField(default=None, sa_column=Column(BigInteger))
    target_filenode: int | None = DbField(default=None, sa_column=Column(BigInteger))
    scope_trigger: str | None = DbField(default=None, max_length=9)
    plan_id: UUID = DbField(foreign_key="ontology_transfer_plans.id")
    request_id: UUID
    status: str = DbField(default="started", max_length=16)
    started_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    finished_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
    receipt_id: UUID | None = DbField(default=None, foreign_key="ontology_transfer_runs.id")
    message: str | None = DbField(default=None, max_length=240)
