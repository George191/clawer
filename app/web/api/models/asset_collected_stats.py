"""Recorded PostgreSQL catalog statistics for exact sandbox Evidence."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime
from sqlmodel import Field, SQLModel


class AssetStatsObservation(BaseModel):
    capturedAt: datetime
    policyGeneration: int
    planUpdatedAt: datetime
    estimatedRows: str | None = None
    lastAnalyzedAt: datetime | None = None


class AssetCollectedStats(BaseModel):
    available: bool
    state: Literal["unavailable", "not_configured", "not_collected", "no_stats", "current", "unknown"]
    reason: Literal["checking_disabled", "target_unavailable", "target_changed", "policy_changed", "policy_paused",
                    "stats_reset", "observation_stale", "probe_failed"] | None = None
    validUntil: datetime | None = None
    current: AssetStatsObservation | None = None
    historical: AssetStatsObservation | None = None


class AssetCollectedStatRow(SQLModel, table=True):
    __tablename__ = "ontology_asset_collected_stats"
    __table_args__ = (
        CheckConstraint("slot IN ('latest','historical') AND policy_generation > 0 AND target_oid > 0 AND target_filenode > 0 "
                        "AND state IN ('captured','no_stats','stats_reset')",
                        name="ck_asset_collected_stats_scope"),
        CheckConstraint("estimated_rows IS NULL OR estimated_rows ~ '^(0|[1-9][0-9]*)([.][0-9]+)?$'",
                        name="ck_asset_collected_stats_decimal"),
        CheckConstraint("(state = 'captured') = (estimated_rows IS NOT NULL OR analyzed_at IS NOT NULL)",
                        name="ck_asset_collected_stats_measure"),
    )

    target_table: str = Field(primary_key=True, max_length=63, foreign_key="ontology_asset_freshness_policies.target_table")
    slot: str = Field(primary_key=True, max_length=10)
    policy_generation: int
    plan_id: UUID
    plan_updated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    target_oid: int = Field(sa_column=Column(BigInteger, nullable=False))
    target_filenode: int = Field(sa_column=Column(BigInteger, nullable=False))
    captured_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    evaluation_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    state: str = Field(max_length=12)
    estimated_rows: str | None = Field(default=None, max_length=96)
    analyzed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    analyze_floor: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    reset_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
