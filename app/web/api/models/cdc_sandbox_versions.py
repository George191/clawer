"""Immutable snapshots captured at real synthetic CDC commits."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index, SmallInteger, Text
from sqlmodel import Field, SQLModel

from app.web.api.models.synthetic_governance import SyntheticMarked


class CdcSandboxVersion(SyntheticMarked):
    id: UUID
    receiptId: UUID
    generation: int
    appliedThrough: str
    rowsCaptured: int
    bytesCaptured: int
    recordedAt: datetime


class CdcSandboxVersionRecord(BaseModel):
    id: str
    value: str


class CdcSandboxVersionDetail(CdcSandboxVersion):
    rows: list[CdcSandboxVersionRecord]


class CdcSandboxVersionsPage(BaseModel):
    available: bool
    versions: list[CdcSandboxVersion]


class CdcSandboxVersionChange(BaseModel):
    id: str
    kind: Literal["inserted", "updated", "deleted"]
    before: str | None
    after: str | None


class CdcSandboxComparison(SyntheticMarked):
    fromVersionId: UUID
    toVersionId: UUID
    changes: list[CdcSandboxVersionChange]


class CdcSandboxVersionRow(SQLModel, table=True):
    __tablename__ = "ontology_cdc_sandbox_versions"
    __table_args__ = (
        CheckConstraint("generation = 1 AND applied_through > 0 AND rows_captured BETWEEN 1 AND 100 "
                        "AND bytes_captured BETWEEN rows_captured * 8 AND 65536",
                        name="ck_ontology_cdc_sandbox_version_bounds"),
        CheckConstraint("marking_level = 1", name="ck_ontology_cdc_version_marking"),
        Index("ix_ontology_cdc_sandbox_versions_scope", "stream_marker", "physical_digest", "recorded_at", "receipt_id"),
    )

    receipt_id: UUID = Field(primary_key=True, foreign_key="ontology_cdc_sandbox_receipts.id")
    stream_marker: UUID
    physical_digest: str = Field(max_length=64)
    generation: int
    applied_through: int = Field(sa_column=Column(BigInteger, nullable=False))
    rows_captured: int
    marking_level: int | None = Field(default=None, sa_column=Column(SmallInteger))
    bytes_captured: int
    recorded_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class CdcSandboxVersionRecordRow(SQLModel, table=True):
    __tablename__ = "ontology_cdc_sandbox_version_rows"
    __table_args__ = (
        CheckConstraint("pg_catalog.octet_length(value) <= 65528", name="ck_ontology_cdc_version_row_bytes"),
    )

    version_id: UUID = Field(primary_key=True, foreign_key="ontology_cdc_sandbox_versions.receipt_id")
    entity_id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    value: str = Field(sa_column=Column(Text, nullable=False))
