"""Project-scoped directed link types and personal, dynamic object-set definitions."""

from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, JsonValue
from sqlalchemy import CheckConstraint, Column, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field as DBField
from sqlmodel import SQLModel

from app.web.api.models.ontology_registry import Identifier, Payload

Cardinality = Literal["one_to_one", "many_to_one", "one_to_many", "many_to_many"]


class LinkTypeInput(Payload):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,62}$")
    name: str = Field(min_length=1, max_length=120)
    sourceDefinitionId: UUID
    targetDefinitionId: UUID
    cardinality: Cardinality = "many_to_many"


class LinkInput(Payload):
    sourceRecordId: UUID
    targetRecordId: UUID


class SetFilter(Payload):
    field: Identifier
    op: Literal["eq", "gt", "lt", "contains"]
    value: JsonValue


class SetQuery(Payload):
    filters: list[SetFilter] = Field(default_factory=list, max_length=20)
    groupBy: Identifier | None = None
    sumField: Identifier | None = None


class SavedSetInput(Payload):
    name: str = Field(min_length=1, max_length=120)
    query: SetQuery


class OntologyLinkType(SQLModel, table=True):
    __tablename__ = "helio_link_types"
    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_ontology_link_type_key"),
        CheckConstraint(
            "cardinality IN ('one_to_one','one_to_many','many_to_one','many_to_many')",
            name="ck_ontology_link_cardinality",
        ),
    )
    id: UUID = DBField(default_factory=uuid4, primary_key=True)
    project_id: UUID = DBField(foreign_key="team.id", nullable=False)
    key: str = DBField(max_length=63)
    name: str = DBField(max_length=120)
    source_definition_id: UUID = DBField(foreign_key="helio_registry_definitions.id", index=True)
    target_definition_id: UUID = DBField(foreign_key="helio_registry_definitions.id", index=True)
    cardinality: str = DBField(max_length=16)


class OntologyLink(SQLModel, table=True):
    __tablename__ = "helio_links"
    __table_args__ = (
        UniqueConstraint(
            "link_type_id", "source_record_id", "target_record_id", name="uq_ontology_link_pair"
        ),
        Index("ix_ontology_link_reverse", "target_record_id", "link_type_id", "source_record_id"),
    )
    id: UUID = DBField(default_factory=uuid4, primary_key=True)
    link_type_id: UUID = DBField(foreign_key="helio_link_types.id")
    source_record_id: UUID = DBField(foreign_key="helio_runtime_records.id", index=True)
    target_record_id: UUID = DBField(foreign_key="helio_runtime_records.id")


class OntologyRemovedLink(SQLModel, table=True):
    """Identify the latest removal of a currently absent directed pair."""

    __tablename__ = "helio_removed_links"
    link_type_id: UUID = DBField(foreign_key="helio_link_types.id", primary_key=True)
    source_record_id: UUID = DBField(foreign_key="helio_runtime_records.id", primary_key=True)
    target_record_id: UUID = DBField(foreign_key="helio_runtime_records.id", primary_key=True)
    link_id: UUID = DBField(nullable=False)


class OntologySavedSet(SQLModel, table=True):
    __tablename__ = "helio_saved_sets"
    __table_args__ = (
        UniqueConstraint("definition_id", "owner_id", "name", name="uq_ontology_saved_set_name"),
    )
    id: UUID = DBField(default_factory=uuid4, primary_key=True)
    definition_id: UUID = DBField(foreign_key="helio_registry_definitions.id")
    owner_id: UUID = DBField(foreign_key="user.id")
    name: str = DBField(max_length=120)
    query: dict = DBField(sa_column=Column(JSONB, nullable=False))
