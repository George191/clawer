"""Deployment-owned physical allowlist for exact ordinary ID samples."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

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
