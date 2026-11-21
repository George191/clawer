"""Request contracts for the Flow workspace."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator


PREVIEW_ROWS_MAX_LIMIT = 100
PREVIEW_ROWS_MAX_OFFSET = 2000


class RowPreviewPolicy(BaseModel):
    mode: Literal["classic", "indexed_id_required", "indexed_projection_required"]


class ExplorerRowKey(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    key: dict[str, str | int | bool | float] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def bounded(self):
        if any(len(k) > 63 or len(str(v)) > 4096 for k, v in self.key.items()):
            raise ValueError("Primary key is too large")
        return self


class ExplorerFileQuery(ExplorerRowKey):
    field: str = Field(min_length=1, max_length=63)
    download: bool = True
    path: list[StrictStr | StrictInt] = Field(default_factory=list, max_length=12)


class ExplorerRowLinksQuery(ExplorerRowKey):
    relation: str | None = Field(default=None, min_length=1, max_length=64)
    offset: int = Field(default=0, ge=0, le=500)
    limit: int = Field(default=10, ge=1, le=25)


class DataQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=1_000_000)
    partition: str | None = None
    forEditing: bool = False
    withIdentity: bool = False
    search: str = Field(default="", max_length=1000)
    filters: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    sorts: list[dict[str, str]] = Field(default_factory=list, max_length=20)


class PreviewRowsQuery(DataQuery):
    limit: int = Field(default=50, ge=1, le=PREVIEW_ROWS_MAX_LIMIT)
    offset: int = Field(default=0, ge=0, le=PREVIEW_ROWS_MAX_OFFSET)
    savedListId: UUID | None = None


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


class PreviewCompositeIndexCoverageRequest(BaseModel):
    """Two or three equality terms, with at most one term using a range."""

    model_config = ConfigDict(extra="forbid")

    terms: list[PreviewIndexCoverageRequest] = Field(strict=True, min_length=2, max_length=3)

    @model_validator(mode="after")
    def distinct_filter_columns(self):
        if len({term.field for term in self.terms}) != len(self.terms):
            raise ValueError("Use distinct filter fields")
        if sum(term.operator != "eq" for term in self.terms) > 1:
            raise ValueError("Use at most one range filter")
        return self


class PreviewIndexSort(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(strict=True, pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    direction: Literal["asc", "desc"]


class PreviewSortIndexCoverageRequest(BaseModel):
    """An ordered key shape, without filter values or executable SQL."""

    model_config = ConfigDict(extra="forbid")

    sorts: list[PreviewIndexSort] = Field(strict=True, min_length=1, max_length=8)

    @model_validator(mode="after")
    def distinct_sort_columns(self):
        if len({sort.field for sort in self.sorts}) != len(self.sorts):
            raise ValueError("Use distinct sort fields")
        return self


PreviewIndexCoverageInput = PreviewIndexCoverageRequest | PreviewCompositeIndexCoverageRequest | PreviewSortIndexCoverageRequest


class PreviewIndexCoverage(BaseModel):
    state: Literal["candidate", "no_candidate", "unsupported", "unavailable"]
