"""Strict, server-compiled filter contract for the synthetic Explorer fixture."""
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Mode = Literal["explain", "preview"]
Scan = Literal["index", "index_only"]
Reason = Literal["index_path", "table_unavailable", "primary_index_required", "unsupported_plan", "query_unavailable"]
BigInt = Annotated[str, Field(pattern=r"^(?:0|-?[1-9][0-9]{0,18})$")]


class IndexSandboxQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode
    idEquals: BigInt | None = None
    idFrom: BigInt | None = None
    idTo: BigInt | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 25
    cursor: str | None = Field(default=None, max_length=512)

    @field_validator("idEquals", "idFrom", "idTo")
    @classmethod
    def validate_bigint(cls, value: str | None) -> str | None:
        if value is not None and not -(2**63) <= int(value) < 2**63:
            raise ValueError("ID must fit a PostgreSQL bigint")
        return value

    @model_validator(mode="after")
    def validate_filter(self) -> Self:
        ranged = self.idFrom is not None or self.idTo is not None
        if ranged == (self.idEquals is not None):
            raise ValueError("Use either exact ID or at least one inclusive ID range bound")
        if self.cursor is not None and (not ranged or self.mode != "preview"):
            raise ValueError("A continuation cursor is only valid for a range preview")
        if self.idFrom is not None and self.idTo is not None and int(self.idFrom) > int(self.idTo):
            raise ValueError("The lower ID must not exceed the upper ID")
        return self


class IndexSandboxCapability(BaseModel):
    available: bool
    rangePagination: Literal[True] | None = Field(default=None, exclude_if=lambda value: value is None)


class IndexSandboxPlan(BaseModel):
    scan: Scan
    key: Literal["id"] = "id"
    order: Literal["ascending"] = "ascending"


class IndexSandboxRow(BaseModel):
    id: str
    label: str


class IndexSandboxPreview(BaseModel):
    status: Literal["allowed", "blocked"]
    reason: Reason
    guidance: str
    mode: Mode
    plan: IndexSandboxPlan | None = None
    rows: list[IndexSandboxRow] | None = None
    hasMore: bool | None = None
    page: int | None = Field(default=None, exclude_if=lambda value: value is None)
    nextCursor: str | None = Field(default=None, exclude_if=lambda value: value is None)
    paginationStatus: Literal["more", "end", "capped"] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
