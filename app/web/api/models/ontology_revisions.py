"""Immutable published snapshots and independently reviewed ontology changes."""
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field
from sqlalchemy import Column, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field as DBField, SQLModel

from app.web.api.models.ontology_registry import Definition, Payload, Revision

Status = Literal["pending", "approved", "rejected", "merged"]


class ProposalCreate(Revision):
    summary: str = Field(min_length=1, max_length=500)
    definition: Definition


class RestoreProposal(Revision):
    summary: str = Field(min_length=1, max_length=500)


class ReviewDecision(Payload):
    approve: bool = Field(strict=True)
    note: str = Field(default="", max_length=500)


class RevisionRoleUpdate(Payload):
    canEdit: bool = Field(strict=True)
    canReview: bool = Field(strict=True)


class OntologyRevisionGrant(SQLModel, table=True):
    __tablename__ = "ontology_revision_grants"

    membership_id: UUID = DBField(
        foreign_key="team_user_join.id", ondelete="CASCADE", primary_key=True,
    )
    can_edit: bool
    can_review: bool


class OntologyRevisionProposal(SQLModel, table=True):
    __tablename__ = "ontology_revision_proposals"

    id: UUID = DBField(default_factory=uuid4, primary_key=True)
    definition_id: UUID = DBField(foreign_key="ontology_registry_definitions.id", nullable=False)
    base_revision: int
    restore_revision: int | None = None
    author_id: UUID
    summary: str = DBField(max_length=500)
    proposed_definition: dict = DBField(sa_column=Column(JSONB, nullable=False))
    status: str = DBField(default="pending", max_length=12)
    reviewer_id: UUID | None = None
    review_note: str = DBField(default="", max_length=500)
    reviewed_at: datetime | None = DBField(default=None, sa_type=DateTime(timezone=True))
    merged_revision: int | None = None
    created_at: datetime | None = DBField(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class OntologyRevisionSnapshot(SQLModel, table=True):
    __tablename__ = "ontology_revision_snapshots"

    definition_id: UUID = DBField(foreign_key="ontology_registry_definitions.id", primary_key=True)
    revision: int = DBField(primary_key=True)
    snapshot: dict = DBField(sa_column=Column(JSONB, nullable=False))
    actor_id: UUID
    proposal_id: UUID | None = DBField(default=None, foreign_key="ontology_revision_proposals.id")
    created_at: datetime = DBField(sa_type=DateTime(timezone=True))
