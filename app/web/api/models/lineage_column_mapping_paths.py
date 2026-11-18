"""Bounded witnesses for declared column mapping references (no observed flow)."""

from typing import Literal

from pydantic import BaseModel, Field

from app.web.api.models.lineage_neighbors import LineageDirection


class ColumnMappingNode(BaseModel):
    schema: str
    table: str
    column: str


class ColumnMappingPath(BaseModel):
    nodes: list[ColumnMappingNode] = Field(min_length=2, max_length=4)


class ColumnMappingPaths(BaseModel):
    status: Literal["complete", "incomplete", "unavailable"]
    direction: LineageDirection
    paths: list[ColumnMappingPath] = Field(default_factory=list, max_length=19)
