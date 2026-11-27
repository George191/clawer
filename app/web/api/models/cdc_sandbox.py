"""Configuration and applied-batch evidence for the fixed local CDC fixture."""

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, UUID4, field_validator
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index
from sqlmodel import Field as DbField, SQLModel
from app.web.api.models.synthetic_governance import SyntheticMarked


class CdcSandboxReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedGeneration: None


class CdcSandboxApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: UUID4
    expectedGeneration: Literal[1]
    expectedAppliedThrough: str = Field(pattern=r"^(?:0|[1-9][0-9]{0,18})$")

    @field_validator("expectedAppliedThrough")
    @classmethod
    def check_int8(cls, value: str) -> str:
        if int(value) > 9223372036854775807:
            raise ValueError("Applied cursor exceeds PostgreSQL bigint")
        return value


class CdcSandboxStatus(BaseModel):
    available: bool
    state: Literal["unreviewed", "armed", "blocked"]
    generation: int | None
    appliedThrough: str
    sourceHighWater: str | None
    source: str
    target: str
    maxRows: int
    maxBytes: int


class CdcSandboxReceipt(SyntheticMarked):
    id: UUID
    requestId: UUID
    generation: int
    fromExclusive: str
    appliedThrough: str
    rowsAffected: int
    recordedAt: datetime


class CdcSandboxBindingRow(SQLModel, table=True):
    __tablename__ = "helio_cdc_sandbox_binding"
    __table_args__ = (
        CheckConstraint("singleton AND generation = 1 AND applied_through >= 0",
                        name="ck_ontology_cdc_sandbox_binding"),
    )

    singleton: bool = DbField(default=True, primary_key=True)
    generation: int = 1
    stream_marker: UUID
    physical_digest: str = DbField(max_length=64)
    applied_through: int = DbField(default=0, sa_column=Column(BigInteger, nullable=False))
    reviewed_by: UUID
    reviewed_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))


class CdcSandboxReceiptRow(SQLModel, table=True):
    __tablename__ = "helio_cdc_sandbox_receipts"
    __table_args__ = (
        CheckConstraint("binding_key AND generation = 1 AND from_exclusive >= 0 "
                        "AND applied_through > from_exclusive AND applied_through - from_exclusive <= 50 "
                        "AND rows_affected BETWEEN 1 AND applied_through - from_exclusive",
                        name="ck_ontology_cdc_sandbox_receipt"),
        Index("ix_ontology_cdc_sandbox_receipts_history", "recorded_at", "id"),
    )

    id: UUID = DbField(default_factory=uuid4, primary_key=True)
    request_id: UUID = DbField(unique=True)
    binding_key: bool = DbField(default=True, foreign_key="helio_cdc_sandbox_binding.singleton")
    generation: int = 1
    from_exclusive: int = DbField(sa_column=Column(BigInteger, nullable=False))
    applied_through: int = DbField(sa_column=Column(BigInteger, nullable=False))
    rows_affected: int
    applied_by: UUID
    recorded_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
