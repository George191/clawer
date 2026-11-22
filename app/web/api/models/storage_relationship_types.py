"""Editable metadata for an existing storage-backed relationship type."""

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class StorageRelationshipTypeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr = Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$")
    description: StrictStr = Field(max_length=4000)
    expectedRevision: StrictStr = Field(min_length=1, max_length=128)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        if isinstance(value, str):
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError("Relationship names cannot contain control characters")
            return value.strip()
        return value
