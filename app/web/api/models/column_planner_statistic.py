"""Metadata-only PostgreSQL planner estimate for one selected column."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ColumnPlannerStatistic(BaseModel):
    status: Literal["observed", "unavailable"]
    nullFraction: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    observedAt: datetime | None = None
