"""Reusable project clearances and their immutable grant history."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from sqlalchemy import Column, DateTime, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field as DBField
from sqlmodel import SQLModel

from app.web.api.models.ontology_registry import Payload
from app.web.services.ontology_registry_policy import Principal


class MarkingReaders(Payload):
    readers: list[Principal] = Field(max_length=100)

    @model_validator(mode="after")
    def distinct(self):
        if len(self.readers) != len(set(self.readers)):
            raise ValueError("Marking readers must be distinct")
        return self


class NewMarking(MarkingReaders):
    name: str = Field(min_length=1, max_length=80)


class ChangeMarking(MarkingReaders):
    revision: int = Field(ge=1, strict=True)


class OntologyMarking(SQLModel, table=True):
    __tablename__ = "helio_markings"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_ontology_marking_name"),)
    id: UUID = DBField(primary_key=True, default_factory=uuid4)
    project_id: UUID = DBField(foreign_key="team.id")
    name: str = DBField(max_length=80)
    revision: int = DBField(default=1)
    readers: list[dict] = DBField(sa_column=Column(JSONB, nullable=False))


class OntologyMarkingChange(SQLModel, table=True):
    __tablename__ = "helio_marking_changes"
    marking_id: UUID = DBField(foreign_key="helio_markings.id", primary_key=True)
    revision: int = DBField(primary_key=True)
    actor_id: UUID = DBField(foreign_key="user.id")
    readers: list[dict] = DBField(sa_column=Column(JSONB, nullable=False))
    created_at: datetime | None = DBField(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
