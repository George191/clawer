"""Exact threshold-only evidence for a bounded local asset sample."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Column, DateTime
from sqlmodel import Field as DbField, SQLModel

from app.web.api.models.asset_freshness import AssetQualityIncident


class AssetMinRowsReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedGeneration: Annotated[int, Field(strict=True, ge=0, le=2147483647)]
    policyGeneration: Annotated[int, Field(strict=True, ge=1, le=2147483647)]
    minimumRows: Annotated[int, Field(strict=True, ge=1, le=100)]


class AssetMinRowsStatus(BaseModel):
    enabled: bool
    generation: int = 0
    policyGeneration: int | None = None
    minimumRows: int | None = None
    state: Literal["unconfigured", "unknown", "healthy", "below_minimum", "superseded"] = "unconfigured"
    reason: str | None = None
    receiptRecordedAt: datetime | None = None
    checkedAt: datetime | None = None
    validUntil: datetime | None = None
    incident: AssetQualityIncident | None = None


class AssetMinRowsRow(SQLModel, table=True):
    __tablename__ = "helio_asset_min_rows"

    target_table: str = DbField(primary_key=True, foreign_key="helio_asset_freshness_policies.target_table")
    generation: int
    policy_generation: int
    minimum_rows: int
    target_oid: int = DbField(sa_column=Column(BigInteger, nullable=False))
    target_filenode: int = DbField(sa_column=Column(BigInteger, nullable=False))
    index_oid: int = DbField(sa_column=Column(BigInteger, nullable=False))
    index_filenode: int = DbField(sa_column=Column(BigInteger, nullable=False))
    reviewed_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    at_least_minimum: bool | None = None
    receipt_id: UUID | None = None
    receipt_recorded_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
    checked_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
