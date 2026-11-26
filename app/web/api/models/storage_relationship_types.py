"""Editable metadata for an existing storage-backed relationship type."""

from typing import Literal

from app.etl.schema.layout import PG_SCHEMA_PATTERN

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class StorageRelationshipMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr = Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$")
    description: StrictStr = Field(max_length=4000)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        if isinstance(value, str):
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError("Relationship names cannot contain control characters")
            return value.strip()
        return value


class StorageRelationshipTypeUpdate(StorageRelationshipMetadata):
    expectedRevision: StrictStr = Field(min_length=1, max_length=128)


class StorageRelationshipTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema: StrictStr = Field(max_length=63, pattern=f"^{PG_SCHEMA_PATTERN}$")
    table: StrictStr = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class StorageRelationshipTypeCreate(StorageRelationshipMetadata):
    description: StrictStr = Field(default="", max_length=4000)
    target: StorageRelationshipTarget
    direction: Literal["outgoing", "incoming"] = "outgoing"
    cardinality: Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"] = "many_to_many"
