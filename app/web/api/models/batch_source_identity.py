"""Proof metadata for the fixed local saved PostgreSQL source identity check."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import CheckConstraint, Column, DateTime
from sqlmodel import Field, SQLModel


class BatchSourceIdentityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedSourceUpdatedAt: AwareDatetime


class BatchSourceIdentityStatus(BaseModel):
    state: Literal["disabled", "unverified", "verified", "stale", "blocked"]
    sourceUpdatedAt: datetime | None = None
    verifiedAt: datetime | None = None


class BatchSourceIdentityRow(SQLModel, table=True):
    __tablename__ = "ontology_batch_source_identities"
    __table_args__ = (CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="ck_batch_source_identity_fingerprint"),)

    connection_id: UUID = Field(primary_key=True)
    source_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    fingerprint: str = Field(max_length=64)
    is_current: bool = True
    verified_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
