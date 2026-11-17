"""Receipt-backed immutable synthetic Builder output versions."""

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field as ApiField, UUID4
from sqlalchemy import ARRAY, CheckConstraint, Column, DateTime, Index, SmallInteger, Text
from sqlmodel import Field, SQLModel

from app.web.api.models.pipeline_sandbox import BuilderColumn
from app.web.api.models.synthetic_governance import SyntheticMarked


class PipelineSandboxCommit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requestId: UUID4
    expectedGeneration: int = ApiField(ge=1, le=2147483647, strict=True)
    expectedConfigHash: str = ApiField(pattern=r"^[0-9a-f]{64}$")


class PipelineSandboxOutput(SyntheticMarked):
    id: UUID
    requestId: UUID
    reviewedGeneration: int
    configHash: str
    resultHash: str
    versionId: UUID
    columns: list[BuilderColumn]
    inputRows: int
    outputRows: int
    recordedAt: datetime


class PipelineSandboxOutputDetail(PipelineSandboxOutput):
    rows: list[list[str]]


class PipelineSandboxOutputsPage(BaseModel):
    available: bool
    outputs: list[PipelineSandboxOutput]


class PipelineSandboxOutputRow(SQLModel, table=True):
    __tablename__ = "ontology_pipeline_sandbox_outputs"
    __table_args__ = (
        CheckConstraint("reviewed_generation > 0 AND config_hash ~ '^[0-9a-f]{64}$' "
                        "AND result_hash ~ '^[0-9a-f]{64}$' AND input_rows BETWEEN 1 AND 100 "
                        "AND output_rows BETWEEN 0 AND input_rows AND output_bytes BETWEEN 0 AND 65536 "
                        "AND (output_rows > 0 OR output_bytes = 0)",
                        name="ck_ontology_pipeline_sandbox_output_bounds"),
        CheckConstraint("array_ndims(columns) = 1 AND array_lower(columns, 1) = 1 "
                        "AND cardinality(columns) BETWEEN 1 AND 2 AND array_position(columns, NULL) IS NULL "
                        "AND columns <@ ARRAY['id','value']::text[] "
                        "AND (cardinality(columns) = 1 OR columns[1] <> columns[2])",
                        name="ck_ontology_pipeline_sandbox_output_projection"),
        CheckConstraint("marking_level IN (1, 2)", name="ck_ontology_builder_output_marking"),
        Index("ix_ontology_pipeline_sandbox_outputs_scope", "stream_marker", "physical_digest", "recorded_at", "id"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    request_id: UUID = Field(unique=True)
    reviewed_generation: int = Field(foreign_key="ontology_pipeline_sandbox_revisions.generation")
    config_hash: str = Field(max_length=64)
    result_hash: str = Field(max_length=64)
    marking_level: int | None = Field(default=None, sa_column=Column(SmallInteger))
    input_version_id: UUID = Field(foreign_key="ontology_cdc_sandbox_versions.receipt_id")
    stream_marker: UUID
    physical_digest: str = Field(max_length=64)
    columns: list[str] = Field(sa_column=Column(ARRAY(Text), nullable=False))
    input_rows: int
    output_rows: int
    output_bytes: int
    recorded_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    committed_by: UUID


class PipelineSandboxOutputRecordRow(SQLModel, table=True):
    __tablename__ = "ontology_pipeline_sandbox_output_rows"
    __table_args__ = (
        CheckConstraint("position BETWEEN 1 AND 100 AND array_ndims(row_values) = 1 AND array_lower(row_values, 1) = 1 "
                        "AND cardinality(row_values) BETWEEN 1 AND 2 AND array_position(row_values, NULL) IS NULL "
                        "AND pg_catalog.octet_length(pg_catalog.convert_to(row_values[1], 'UTF8')) "
                        "+ coalesce(pg_catalog.octet_length(pg_catalog.convert_to(row_values[2], 'UTF8')), 0) <= 65536",
                        name="ck_ontology_pipeline_sandbox_output_row"),
    )

    output_id: UUID = Field(primary_key=True, foreign_key="ontology_pipeline_sandbox_outputs.id")
    position: int = Field(primary_key=True)
    row_values: list[str] = Field(sa_column=Column(ARRAY(Text), nullable=False))
