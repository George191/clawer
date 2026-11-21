"""Explorer queries and saved work scoped to a Published semantic definition."""

from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field as DBField
from sqlmodel import SQLModel

from app.web.api.models.ontology_graph import SetQuery
from app.web.api.models.ontology_registry import Identifier, Payload

Identity = dict[Annotated[str, Field(max_length=63)], Annotated[str, StringConstraints(max_length=4096, strip_whitespace=False)]]


class ExplorerSort(Payload):
    field: Identifier
    direction: Literal["asc", "desc"]


class WorkbenchFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=160)
    field: str = Field(max_length=63)
    operator: str = Field(min_length=1, max_length=64)
    value: str = Field(max_length=4096)
    active: bool = Field(strict=True)
    join: Literal["and", "or"]


class ExplorerQuery(SetQuery):
    # None is a dynamic query; an empty list is deliberately an empty object set.
    keys: list[Identity] | None = Field(default=None, max_length=100)
    sorts: list[ExplorerSort] = Field(default_factory=list, max_length=20)
    workbenchFilters: list[WorkbenchFilter] = Field(default_factory=list, max_length=100)
    search: Annotated[str, StringConstraints(max_length=1000, strip_whitespace=False)] = ""

    @model_validator(mode="after")
    def distinct_sorts(self):
        if len({sort.field for sort in self.sorts}) != len(self.sorts):
            raise ValueError("Each sort property may appear only once")
        return self


class ExplorerContent(Payload):
    query: ExplorerQuery = Field(default_factory=ExplorerQuery)
    view: Literal["explore", "results"] = "results"
    chart: Identifier | None = None
    keys: list[Identity] | None = Field(default=None, max_length=100)


class SaveExplorerItem(Payload):
    kind: Literal["exploration", "list"]
    name: str = Field(min_length=1, max_length=100)
    content: ExplorerContent


class ShareExplorerItem(Payload):
    version: UUID
    shared: bool


class OntologyExplorerSavedItem(SQLModel, table=True):
    __tablename__ = "ontology_explorer_saved_items"

    id: UUID = DBField(default_factory=uuid4, primary_key=True)
    definition_id: UUID = DBField(foreign_key="ontology_registry_definitions.id", ondelete="CASCADE")
    owner_id: UUID = DBField(foreign_key="user.id", ondelete="CASCADE")
    kind: str = DBField(max_length=12)
    name: str = DBField(max_length=100)
    shared: bool = False
    version: UUID = DBField(default_factory=uuid4)
    content: dict = DBField(sa_column=Column(JSONB(), nullable=False))


class OntologyExplorerResultLayout(SQLModel, table=True):
    __tablename__ = "ontology_explorer_result_layouts"
    __table_args__ = (
        UniqueConstraint("definition_id", "owner_id", name="uq_ontology_explorer_layout_owner", postgresql_nulls_not_distinct=True),
    )
    id: UUID = DBField(default_factory=uuid4, primary_key=True)
    definition_id: UUID = DBField(foreign_key="ontology_registry_definitions.id", ondelete="CASCADE")
    owner_id: UUID | None = DBField(default=None, foreign_key="user.id", ondelete="CASCADE")
    layout: dict | None = DBField(sa_column=Column(JSONB(none_as_null=True), nullable=True))
    version: UUID = DBField(default_factory=uuid4)
