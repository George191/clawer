"""Persist business records and action events, isolated from source warehouse tables."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, Index, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class OntologyRuntimeRecord(SQLModel, table=True):
    """A single editable no-source business record with an optimistic revision."""

    __tablename__ = "ontology_runtime_records"
    __table_args__ = (
        UniqueConstraint("definition_id", "key_digest", name="uq_ontology_runtime_record_key"),
        CheckConstraint("revision > 0", name="ck_ontology_runtime_record_revision"),
        Index("ix_ontology_runtime_records_list", "definition_id", "deleted", "id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    definition_id: UUID = Field(foreign_key="ontology_registry_definitions.id", nullable=False)
    key_digest: str = Field(max_length=64)
    values: dict[str, Any] = Field(sa_column=Column(JSONB, nullable=False))
    revision: int = Field(default=1)
    deleted: bool = Field(default=False)


class OntologyActionEvent(SQLModel, table=True):
    """Audit metadata without a copy of restricted record values."""

    __tablename__ = "ontology_action_events"
    __table_args__ = (
        UniqueConstraint("record_id", "revision", name="uq_ontology_action_event_revision"),
        CheckConstraint(
            "kind IN ('create', 'modify', 'delete')", name="ck_ontology_action_event_kind"
        ),
        CheckConstraint("revision > 0", name="ck_ontology_action_event_revision"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    record_id: UUID = Field(foreign_key="ontology_runtime_records.id", nullable=False)
    actor_id: UUID
    kind: str = Field(max_length=6)
    revision: int
    occurred_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
