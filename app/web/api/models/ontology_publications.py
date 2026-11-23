"""Explicit, read-only API grants for published semantic object sets."""
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator
from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field as DBField, SQLModel

from app.web.api.models.ontology_explorer import ExplorerQuery
from app.web.api.models.ontology_registry import Payload


class PublicationInput(Payload):
    modelRevision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    fields: list[str] = Field(min_length=1, max_length=200)
    query: ExplorerQuery = Field(default_factory=ExplorerQuery)
    validFrom: datetime
    validUntil: datetime
    version: UUID | None = None

    @field_validator("validFrom", "validUntil")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("Include a timezone in publication validity dates")
        return value

    @model_validator(mode="after")
    def check_scope(self):
        if self.validUntil <= self.validFrom:
            raise ValueError("Expiry must be later than the start time")
        if len(set(self.fields)) != len(self.fields):
            raise ValueError("Publication fields must be distinct")
        if self.query.groupBy or self.query.sumField:
            raise ValueError("Publish an object range without grouping or sums")
        return self


class OntologyPublication(SQLModel, table=True):
    __tablename__ = "ontology_publications"

    id: UUID = DBField(default_factory=uuid4, primary_key=True)
    definition_id: UUID = DBField(foreign_key="ontology_registry_definitions.id", ondelete="CASCADE", index=True)
    owner_id: UUID = DBField(foreign_key="user.id", ondelete="CASCADE")
    name: str = DBField(max_length=120)
    revision: int
    fields: list = DBField(sa_column=Column(JSONB, nullable=False))
    query: dict = DBField(sa_column=Column(JSONB, nullable=False))
    valid_from: datetime = DBField(sa_type=DateTime(timezone=True))
    valid_until: datetime = DBField(sa_type=DateTime(timezone=True))
    key_digest: str | None = DBField(default=None, max_length=64)
    key_status: str = DBField(default="unissued", max_length=12)
    version: UUID = DBField(default_factory=uuid4)
