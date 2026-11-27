"""Reviewed field-time observation for one synthetic transfer target."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import BigInteger, Column, DateTime
from sqlmodel import Field as DbField, SQLModel

from app.web.api.models.asset_freshness import AssetQualityIncident


class AssetFieldFreshnessReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedGeneration: Annotated[int, Field(strict=True, ge=0, le=2147483647)]
    policyGeneration: Annotated[int, Field(strict=True, ge=1, le=2147483647)]
    maxLagSeconds: Annotated[int, Field(strict=True, ge=60, le=604800)]


class AssetFieldFreshnessStatus(BaseModel):
    enabled: bool
    generation: int = 0
    policyGeneration: int | None = None
    maxLagSeconds: int | None = None
    state: Literal["unconfigured", "unknown", "fresh", "stale", "superseded"] = "unconfigured"
    reason: str | None = None
    fieldRecordedAt: datetime | None = None
    receiptRecordedAt: datetime | None = None
    checkedAt: datetime | None = None
    validUntil: datetime | None = None
    lagSeconds: float | None = None
    incident: AssetQualityIncident | None = None


class AssetFieldFreshnessRow(SQLModel, table=True):
    __tablename__ = "helio_asset_field_freshness"

    target_table: str = DbField(primary_key=True, foreign_key="helio_asset_freshness_policies.target_table")
    generation: int
    policy_generation: int
    max_lag_seconds: int
    target_oid: int = DbField(sa_column=Column(BigInteger, nullable=False))
    target_filenode: int = DbField(sa_column=Column(BigInteger, nullable=False))
    index_oid: int = DbField(sa_column=Column(BigInteger, nullable=False))
    index_filenode: int = DbField(sa_column=Column(BigInteger, nullable=False))
    reviewed_at: datetime = DbField(sa_column=Column(DateTime(timezone=True), nullable=False))
    field_recorded_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
    receipt_recorded_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
    checked_at: datetime | None = DbField(default=None, sa_column=Column(DateTime(timezone=True)))
    receipt_id: UUID | None = None
