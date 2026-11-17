"""Request contracts for the Flow workspace."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


PREVIEW_ROWS_MAX_LIMIT = 100
PREVIEW_ROWS_MAX_OFFSET = 2000


class DataQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    partition: str | None = None
    search: str = Field(default="", max_length=1000)
    filters: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    sorts: list[dict[str, str]] = Field(default_factory=list, max_length=20)


class PreviewRowsQuery(DataQuery):
    limit: int = Field(default=50, ge=1, le=PREVIEW_ROWS_MAX_LIMIT)
    offset: int = Field(default=0, ge=0, le=PREVIEW_ROWS_MAX_OFFSET)


class OffsetUpdate(BaseModel):
    partition: int = Field(ge=0, strict=True)
    offset: int = Field(ge=0, le=9_007_199_254_740_991, strict=True)
    confirmation: Literal["SET OFFSET"]
    topic: str | None = None


class CodePayload(BaseModel):
    code: str = Field(max_length=250_000)


class SqlQuery(BaseModel):
    sql: str = Field(min_length=1, max_length=50_000)


class KeywordVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=36, max_length=36)


class ObjectVisibility(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visibility: Literal["normal", "prominent", "hidden"]


class FavoriteMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FavoriteImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(max_length=200)


class PreviewIndexCoverageRequest(BaseModel):
    """One catalog-only atomic filter shape: never accept values or SQL."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(strict=True, pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    operator: Literal["eq", "lt", "lte", "gt", "gte", "between"]


class PreviewIndexCoverage(BaseModel):
    state: Literal["candidate", "no_candidate", "unsupported", "unavailable"]
