"""Reviewed append-only application of bounded exact synthetic inbox ordinals."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field as ApiField, UUID4, field_validator
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index, text
from sqlmodel import Field, SQLModel

PUSH_APPLY_SCHEMA = "ts_push_inbox_sandbox"
PUSH_APPLY_TARGET = "applied_fixture"
MAX_PUSH_APPLY_EVENTS = 50
MAX_PUSH_EMPTY_CHECKS = 200

Ordinal = Annotated[str, ApiField(pattern=r"^(0|[1-9][0-9]{0,3})$")]


class PushApplyReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedGeneration: None


class PushApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID4
    expectedGeneration: Annotated[int, ApiField(strict=True, ge=1, le=1)]
    expectedAppliedThrough: Ordinal
    expectedAcceptedThrough: Ordinal

    @field_validator("expectedAppliedThrough", "expectedAcceptedThrough")
    @classmethod
    def within_inbox(cls, value: str) -> str:
        if int(value) > 1000:
            raise ValueError("Ordinal is outside the synthetic inbox")
        return value


class PushApplyResult(BaseModel):
    id: UUID
    status: Literal["applied", "no_new_data"]
    fromExclusive: str
    throughInclusive: str
    reviewedAcceptedThrough: str
    eventsApplied: int
    checkedAt: datetime
    appliedAt: datetime | None
    hasMoreReviewed: bool


class PushApplyStatus(BaseModel):
    target: str = f"{PUSH_APPLY_SCHEMA}.{PUSH_APPLY_TARGET}"
    state: Literal["disabled", "unconfigured", "unreviewed", "ready", "blocked"]
    available: bool
    canReplay: bool = False
    generation: Literal[1] | None = None
    acceptedThrough: str | None = None
    appliedThrough: str | None = None
    reviewedAt: datetime | None = None
    maxEvents: int = MAX_PUSH_APPLY_EVENTS
    recent: list[PushApplyResult] = ApiField(default_factory=list)


class PushApplyBindingRow(SQLModel, table=True):
    __tablename__ = "helio_push_inbox_apply_binding"
    __table_args__ = (
        CheckConstraint("""singleton IS TRUE AND generation=1 AND source_oid>0 AND source_filenode>0
                        AND target_oid>0 AND target_filenode>0 AND applied_through BETWEEN 0 AND 1000
                        AND empty_checks BETWEEN 0 AND 200""", name="ck_push_apply_binding"),
    )

    singleton: bool = Field(default=True, primary_key=True)
    generation: int = 1
    database_marker: UUID
    source_oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    source_filenode: int = Field(sa_column=Column(BigInteger, nullable=False))
    target_oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    target_filenode: int = Field(sa_column=Column(BigInteger, nullable=False))
    applied_through: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    empty_checks: int = 0
    reviewed_by: UUID
    reviewed_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class PushApplyResultRow(SQLModel, table=True):
    __tablename__ = "helio_push_inbox_apply_receipts"
    __table_args__ = (
        CheckConstraint("""generation=1 AND from_exclusive BETWEEN 0 AND 1000
                        AND reviewed_accepted_through BETWEEN from_exclusive AND 1000
                        AND ((outcome='no_new_data' AND from_exclusive=through_inclusive
                              AND through_inclusive=reviewed_accepted_through AND events_applied=0)
                             OR (outcome='applied' AND through_inclusive=least(from_exclusive+50,reviewed_accepted_through)
                                 AND events_applied=through_inclusive-from_exclusive AND events_applied BETWEEN 1 AND 50))""",
                        name="ck_push_apply_result"),
        Index("ix_push_apply_recent", "recorded_at", "id"),
        Index("uq_push_apply_ordinal", "through_inclusive", unique=True, postgresql_where=text("outcome='applied'")),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    request_id: UUID = Field(unique=True)
    database_marker: UUID
    generation: int
    outcome: str = Field(max_length=12)
    from_exclusive: int = Field(sa_column=Column(BigInteger, nullable=False))
    through_inclusive: int = Field(sa_column=Column(BigInteger, nullable=False))
    reviewed_accepted_through: int = Field(sa_column=Column(BigInteger, nullable=False))
    events_applied: int
    recorded_by: UUID
    recorded_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
