"""Durable local synthetic Push acceptance records and admin-only status."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field as ApiField
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime
from sqlmodel import Field, SQLModel

TOTAL_LIMIT = 1000
WINDOW_LIMIT = 20
WINDOW_SECONDS = 60
RECENT_LIMIT = 10
MAX_INTAKE_GENERATION = 2_147_483_647


class PushInboxAcceptance(BaseModel):
    id: UUID
    ordinal: str
    acceptedAt: datetime


class PushInboxStatus(BaseModel):
    available: bool
    state: Literal["disabled", "unconfigured", "ready", "full", "paused"]
    acceptedCount: int = 0
    totalLimit: int = TOTAL_LIMIT
    windowSeconds: int = WINDOW_SECONDS
    windowLimit: int = WINDOW_LIMIT
    recent: list[PushInboxAcceptance] = ApiField(default_factory=list)

    intakeControlAvailable: Literal[True] | None = None
    intakeGeneration: int | None = None
    pausedAt: datetime | None = None
    secondaryProducerAvailable: Literal[True] | None = None


class PushInboxIntakeChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedGeneration: int = ApiField(strict=True, ge=0, le=MAX_INTAKE_GENERATION)
    paused: bool = ApiField(strict=True)


class PushInboxStateRow(SQLModel, table=True):
    __tablename__ = "ontology_push_inbox_state"
    __table_args__ = (
        CheckConstraint("singleton IS TRUE AND accepted_count BETWEEN 0 AND 1000 AND window_accepted BETWEEN 0 AND 20 "
                        "AND (window_epoch IS NOT NULL OR window_accepted = 0)",
                        name="ck_push_inbox_synthetic_singleton"),
    )

    singleton: bool = Field(default=True, primary_key=True)
    database_marker: UUID | None = None
    accepted_count: int = 0
    window_epoch: int | None = Field(default=None, sa_column=Column(BigInteger))
    window_accepted: int = 0


class PushInboxReceiptRow(SQLModel, table=True):
    __tablename__ = "ontology_push_inbox_receipts"
    __table_args__ = (
        CheckConstraint("ordinal BETWEEN 1 AND 1000 AND schema_version = 1 AND entity_id > 0 "
                        "AND body_digest ~ '^[0-9a-f]{64}$' AND fixture_value ~ '^fixture-[a-z0-9-]{1,48}$'",
                        name="ck_push_inbox_synthetic_receipt"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    request_id: UUID = Field(unique=True)
    ordinal: int = Field(sa_column=Column(BigInteger, nullable=False, unique=True))
    body_digest: str = Field(max_length=64)
    schema_version: int
    entity_id: int = Field(sa_column=Column(BigInteger, nullable=False))
    fixture_value: str = Field(max_length=56)
    accepted_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
