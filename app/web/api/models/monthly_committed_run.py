"""Safe metadata from a verified Monthly UTC committed run."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class MonthlyCommittedRun(BaseModel):
    kind: Literal["monthly_utc"] = "monthly_utc"
    planId: UUID
    jobId: UUID
    receiptId: UUID
    sourceSchema: Literal["ts_transfer_sandbox"] = "ts_transfer_sandbox"
    sourceTable: str
    targetSchema: Literal["ts_transfer_sandbox"] = "ts_transfer_sandbox"
    targetTable: str
    slot: datetime
    scheduleDayOfMonthUtc: int = Field(ge=1, le=28)
    recordedAt: datetime
    rowsAffected: int = Field(ge=0, le=100)
