"""Persistent semantic definitions; separate from the physical table catalog."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import BigInteger, Column, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field as DBField
from sqlmodel import SQLModel

from app.web.services.ontology_registry_policy import ActionDefinition, ObjectSecurity

Identifier = Annotated[str, Field(max_length=63)]
Label = Annotated[str, Field(max_length=120)]
PropertyType = Literal["string", "integer", "number", "boolean", "date", "datetime", "json"]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectCreate(Payload):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[\w][\w .-]*$")
    teamId: UUID


class OntologyProjectProfile(SQLModel, table=True):
    """A named ontology project uses its existing team as the access boundary."""

    __tablename__ = "ontology_project_profiles"

    team_id: UUID = DBField(foreign_key="team.id", primary_key=True)
    name: str = DBField(max_length=64, nullable=False)
    owner_id: UUID = DBField(foreign_key="user.id", nullable=False)


class Source(Payload):
    mode: Literal["existing", "none"]
    schema_name: Identifier | None = Field(default=None, alias="schema")
    table: Identifier | None = None
    upstreamConnectionId: UUID | None = None


class Property(Payload):
    id: Identifier
    name: Label
    type: PropertyType
    sourceColumn: Identifier | None = None
    required: bool = False


FunctionKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")]


class Parameter(Payload):
    parameter: FunctionKey


class Constant(BaseModel):
    # Spaces in literal strings are business data, including separators used by concat.
    model_config = ConfigDict(extra="forbid")
    value: JsonValue


class Assignment(Payload):
    field: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,62}$")]
    operation: Literal["set", "add", "multiply", "concat"]
    operands: list[Parameter | Constant] = Field(min_length=1, max_length=2)


class BusinessFunction(Payload):
    key: FunctionKey
    name: str = Field(min_length=1, max_length=100)
    assignments: list[Assignment] = Field(min_length=1, max_length=24)


class Definition(Payload):
    id: Identifier
    name: Label
    pluralName: Label = ""
    description: str = Field(default="", max_length=4000)
    icon: Identifier = ""
    color: Annotated[str, Field(max_length=20)] = "#ffffff"
    iconColor: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")] = "#000000"
    groups: list[Label] = Field(default_factory=list, max_length=20)
    projectId: UUID
    source: Source
    properties: list[Property] = Field(default_factory=list, max_length=200)
    primaryKey: list[Identifier] = Field(default_factory=list, max_length=16)
    titleKey: Identifier = ""
    actions: list[ActionDefinition] = Field(default_factory=list, max_length=3)
    functions: list[BusinessFunction] = Field(default_factory=list, max_length=24)
    security: ObjectSecurity


class Revision(Payload):
    revision: int = Field(ge=1, strict=True)


class UpdateDraft(Revision):
    definition: Definition


class DefinitionIssue(Payload):
    path: str
    message: str


class ValidationResult(Payload):
    errors: list[DefinitionIssue] = Field(default_factory=list)
    warnings: list[DefinitionIssue] = Field(default_factory=list)


class OntologyDefinitionRow(SQLModel, table=True):
    __tablename__ = "ontology_registry_definitions"

    id: UUID = DBField(default_factory=uuid4, primary_key=True)
    project_id: UUID = DBField(foreign_key="team.id", index=True, nullable=False)
    owner_id: UUID = DBField(foreign_key="user.id", index=True, nullable=False)
    semantic_id: str = DBField(max_length=63, nullable=False)
    revision: int = DBField(default=1, nullable=False)
    draft_definition: dict = DBField(sa_column=Column(JSONB, nullable=False))
    published_definition: dict | None = DBField(default=None, sa_column=Column(JSONB(none_as_null=True), nullable=True))
    source_oid: int | None = DBField(default=None, sa_type=BigInteger)
    source_signature: dict | None = DBField(default=None, sa_column=Column(JSONB(none_as_null=True), nullable=True))
    published_at: datetime | None = DBField(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime | None = DBField(
        default=None, sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
    updated_at: datetime | None = DBField(
        default=None, sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
