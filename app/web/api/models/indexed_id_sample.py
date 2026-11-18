"""Strict contracts for the ordinary Explorer ID-only sample."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_BigInt = Annotated[str, Field(strict=True, pattern=r"^(?:0|-?[1-9][0-9]{0,18})$")]
SampleReason = Literal["index_path", "scope_unavailable", "unsupported_plan", "query_unavailable"]


class IndexedSampleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idEquals: _BigInt | None = None
    idFrom: _BigInt | None = None
    idTo: _BigInt | None = None

    @field_validator("idEquals", "idFrom", "idTo")
    @classmethod
    def in_bigint(cls, value: str | None) -> str | None:
        if value is not None and not -(2**63) <= int(value) < 2**63:
            raise ValueError("ID must fit a PostgreSQL bigint")
        return value

    @model_validator(mode="after")
    def exact_or_range(self) -> Self:
        if self.idEquals is not None and self.idFrom is None and self.idTo is None:
            return self
        if self.idEquals is None and self.idFrom is not None and self.idTo is not None and int(self.idFrom) <= int(self.idTo):
            return self
        raise ValueError("Use an exact ID or both ordered inclusive ID bounds")


class IndexedCompoundSampleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenantId: _BigInt
    groupId: _BigInt
    idFrom: _BigInt
    idTo: _BigInt

    @field_validator("tenantId", "groupId", "idFrom", "idTo")
    @classmethod
    def in_bigint(cls, value: str) -> str:
        if not -(2**63) <= int(value) < 2**63:
            raise ValueError("ID must fit a PostgreSQL bigint")
        return value

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if int(self.idFrom) > int(self.idTo):
            raise ValueError("Use ordered inclusive ID bounds")
        return self


class IndexedSampleCapability(BaseModel):
    available: bool
    keyColumn: str | None = Field(default=None, exclude_if=lambda value: value is None)
    limit: Literal[20] = 20


class IndexedSampleResult(BaseModel):
    state: Literal["sampled", "blocked"]
    reason: SampleReason
    ids: list[str] = Field(default_factory=list)
    hasMore: bool = False

class IndexedProjectionRequest(IndexedSampleRequest):
    expectedProjectionRevision: UUID


class IndexedProjectionField(BaseModel):
    name: str
    type: Literal["bigint", "boolean", "varchar(32)"]


class IndexedProjectionCapability(BaseModel):
    available: bool
    limit: Literal[20] = 20
    fields: list[IndexedProjectionField] | None = Field(default=None, exclude_if=lambda value: value is None)
    projectionRevision: UUID | None = Field(default=None, exclude_if=lambda value: value is None)


class IndexedProjectionResult(BaseModel):
    state: Literal["sampled", "blocked"]
    reason: SampleReason
    rows: list[list[str | bool | None]] = Field(default_factory=list)
    hasMore: bool = False
    fields: list[IndexedProjectionField] | None = Field(default=None, exclude_if=lambda value: value is None)
    projectionRevision: UUID | None = Field(default=None, exclude_if=lambda value: value is None)
