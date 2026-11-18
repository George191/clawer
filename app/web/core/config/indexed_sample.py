"""Deployment-owned physical allowlist for exact ordinary ID samples."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

_Canonical = Annotated[str, Field(strict=True, pattern=r"^[a-z_][a-z0-9_]{0,62}$")]
_ObjectId = Annotated[int, Field(strict=True, ge=1, le=4294967295)]

class IndexedCompoundSampleScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    database: _Canonical
    schema_name: Annotated[str, Field(alias="schema", strict=True, pattern=r"^ts_[a-z0-9_]{1,60}$")]
    table: _Canonical
    heapOid: _ObjectId
    heapFilenode: _ObjectId
    indexOid: _ObjectId
    indexFilenode: _ObjectId


class IndexedSampleScope(IndexedCompoundSampleScope):
    keyColumn: _Canonical


class RequiredIndexedPreviewTable(BaseModel):
    """Canonical asset identity, independent of sampler flags and physical pins."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: Annotated[str, Field(alias="schema", strict=True, pattern=r"^ts_[a-z0-9_]{1,60}$")]
    table: _Canonical

    mode: Literal["indexed_id_required", "indexed_projection_required"] = "indexed_id_required"


class IndexedProjectionInclude(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: _Canonical
    type: Literal["boolean", "varchar(32)"]


class IndexedProjectionScope(IndexedSampleScope):
    marker: UUID
    include: Annotated[tuple[IndexedProjectionInclude, ...], Field(min_length=1, max_length=2)]

    @model_validator(mode="after")
    def exact_projection(self):
        if not self.marker.int or len({self.keyColumn, *(field.name for field in self.include)}) != len(self.include) + 1:
            raise ValueError("Review one marker and distinct covering fields")
        return self
