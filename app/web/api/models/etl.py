"""Request contracts for the Flow workspace."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DataQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    partition: str | None = None
    search: str = Field(default="", max_length=1000)
    filters: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    sorts: list[dict[str, str]] = Field(default_factory=list, max_length=20)


class OffsetUpdate(BaseModel):
    partition: int = Field(ge=0, strict=True)
    offset: int = Field(ge=0, le=9_007_199_254_740_991, strict=True)
    confirmation: Literal["SET OFFSET"]
    topic: str | None = None


class CodePayload(BaseModel):
    code: str = Field(max_length=250_000)


class SqlQuery(BaseModel):
    sql: str = Field(min_length=1, max_length=50_000)
