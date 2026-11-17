"""Strict typed builder configuration and immutable saved draft revisions."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field as ApiField, field_validator, model_validator
from sqlalchemy import CheckConstraint, Column, DateTime, Index, SmallInteger
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.web.api.models.synthetic_governance import SandboxMarking, SyntheticMarked

BuilderColumn = Literal["id", "value"]


class PipelineIdFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal["id"]
    operator: Literal["eq", "gte", "lte"]
    value: str = ApiField(pattern=r"^(?:0|-?[1-9][0-9]{0,18})$")

    @field_validator("value")
    @classmethod
    def check_int64(cls, value: str) -> str:
        if not -(2**63) <= int(value) < 2**63:
            raise ValueError("ID value exceeds PostgreSQL bigint")
        return value


class PipelineValueFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: Literal["value"]
    operator: Literal["eq", "prefix"]
    value: str = ApiField(max_length=128)

    @field_validator("value")
    @classmethod
    def check_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Filter text cannot contain a zero byte")
        # Invalid Unicode would otherwise fail when storing JSONB.
        value.encode("utf-8")
        return value


PipelineFilter = Annotated[PipelineIdFilter | PipelineValueFilter, ApiField(discriminator="field")]


class PipelineSandboxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    versionId: UUID
    matchVersionId: UUID | None = ApiField(default=None, exclude_if=lambda value: value is None)
    filters: list[PipelineFilter] = ApiField(max_length=3)
    projection: list[BuilderColumn] = ApiField(min_length=1, max_length=2)
    additionalMarking: Literal["sandbox-restricted"] | None = ApiField(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def canonical_shape(self):
        if self.matchVersionId == self.versionId:
            raise ValueError("Match by ID requires distinct left and right versions")
        if len(set(self.projection)) != len(self.projection):
            raise ValueError("Projection columns must be distinct")
        tags = {(clause.field, clause.operator, clause.value) for clause in self.filters}
        if len(tags) != len(self.filters):
            raise ValueError("AND filter clauses must be distinct")
        # AND clauses commute. Projection order remains part of the result.
        self.filters.sort(key=lambda item: (item.field, item.operator, item.value))
        return self


class PipelineSandboxSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expectedGeneration: int | None = ApiField(ge=1, le=2147483647, strict=True)
    config: PipelineSandboxConfig


class PipelineSandboxStatus(BaseModel):
    available: bool
    matchEnabled: Literal[True] | None = ApiField(default=None, exclude_if=lambda value: value is None)
    latestGeneration: int | None
    governanceEnabled: Literal[True] | None = ApiField(default=None, exclude_if=lambda value: value is None)
    allowedMarkings: list[SandboxMarking] | None = ApiField(default=None, exclude_if=lambda value: value is None)
    canSave: bool | None = ApiField(default=None, exclude_if=lambda value: value is None)


class PipelineSandboxPreview(SyntheticMarked):
    versionId: UUID
    matchVersionId: UUID | None = ApiField(default=None, exclude_if=lambda value: value is None)
    configHash: str
    inputRows: int
    rightInputRows: int | None = ApiField(default=None, exclude_if=lambda value: value is None)
    matchedRows: int
    columns: list[BuilderColumn]
    rows: list[list[str]]


class PipelineSandboxRevision(SyntheticMarked):
    generation: int
    configHash: str
    config: PipelineSandboxConfig
    recordedAt: datetime


class PipelineSandboxRevisionRow(SQLModel, table=True):
    __tablename__ = "ontology_pipeline_sandbox_revisions"
    __table_args__ = (
        CheckConstraint("generation > 0 AND config_hash ~ '^[0-9a-f]{64}$' "
                        "AND jsonb_typeof(config) = 'object' AND config ? 'versionId' "
                        "AND config->>'versionId' = input_version_id::text",
                        name="ck_ontology_pipeline_sandbox_revision"),
        CheckConstraint("(marking_level = 1 AND NOT config ? 'additionalMarking') OR "
                        "(marking_level = 2 AND (match_version_id IS NOT NULL OR "
                        "(config->>'additionalMarking' = 'sandbox-restricted') IS TRUE))",
                        name="ck_ontology_builder_revision_marking"),
        CheckConstraint("(match_version_id IS NULL AND NOT config ? 'matchVersionId') OR "
                        "(match_version_id IS NOT NULL AND match_version_id <> input_version_id AND "
                        "(marking_level IN (1, 2)) IS TRUE AND "
                        "(config->>'matchVersionId' = match_version_id::text) IS TRUE)",
                        name="ck_ontology_builder_revision_match"),
        Index("ix_ontology_pipeline_sandbox_history", "stream_marker", "physical_digest", "generation"),
    )

    generation: int = Field(primary_key=True)
    stream_marker: UUID
    physical_digest: str = Field(max_length=64)
    input_version_id: UUID = Field(foreign_key="ontology_cdc_sandbox_versions.receipt_id")
    match_version_id: UUID | None = Field(default=None, foreign_key="ontology_cdc_sandbox_versions.receipt_id")
    config_hash: str = Field(max_length=64)
    marking_level: int | None = Field(default=None, sa_column=Column(SmallInteger))
    config: dict = Field(sa_column=Column(JSONB, nullable=False))
    recorded_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    saved_by: UUID
