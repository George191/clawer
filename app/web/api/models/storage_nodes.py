"""Connection-scoped storage resources used by source selection and transfer drafts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.web.api.models.data_connections import Engine, SourceColumn


StorageNodeKind = Literal["schema", "database", "index", "collection", "bucket"]


class StorageNodeRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connectionId: UUID
    kind: StorageNodeKind
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("Control characters are not allowed in resource names")
        return value


class StorageNode(StorageNodeRef):
    id: str
    engine: Engine
    connectionName: str
    sourceUpdatedAt: datetime


class StorageNodeCatalog(BaseModel):
    nodes: list[StorageNode]
    hasMore: bool


class StorageNodeRelation(BaseModel):
    name: str
    kind: str


class StorageNodeRelations(BaseModel):
    relations: list[StorageNodeRelation]
    hasMore: bool


class StorageNodeColumns(BaseModel):
    columns: list[SourceColumn]
    hasMore: bool


class StorageNodePreview(BaseModel):
    columns: list[str]
    rows: list[dict]
    hasMore: bool
