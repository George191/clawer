"""Bounded, declared Explorer table relationships (not movement evidence)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

LineageDirection = Literal["incoming", "outgoing"]
LineageNeighborKind = Literal["object_relation", "foreign_key"]


class LineageEndpoint(BaseModel):
    schema_name: str = Field(alias="schema")
    table: str


class LineageNeighbor(LineageEndpoint):
    kinds: list[LineageNeighborKind]


class LineageNeighbors(BaseModel):
    direction: LineageDirection
    neighbors: list[LineageNeighbor]
    truncated: bool


class LineagePathEdge(BaseModel):
    from_node: LineageEndpoint = Field(alias="from")
    to: LineageEndpoint
    kinds: list[LineageNeighborKind]


class LineagePath(BaseModel):
    state: Literal["found", "not_found_within_limit", "incomplete"]
    searchTruncated: bool
    maxHops: Literal[4] = 4
    edges: list[LineagePathEdge] = Field(default_factory=list)


class LineagePathTargets(BaseModel):
    items: list[LineageEndpoint]
    truncated: bool


class LineageCommonUpstream(BaseModel):
    state: Literal["found", "none_within_limit", "incomplete"]
    searchTruncated: bool
    ancestor: LineageEndpoint | None = None
    leftEdges: list[LineagePathEdge] = Field(default_factory=list)
    rightEdges: list[LineagePathEdge] = Field(default_factory=list)


class ObservedLineageNeighbor(LineageEndpoint):
    receiptId: UUID
    receiptRecordedAt: datetime
    rowsAffected: int


class ObservedLineage(BaseModel):
    available: bool
    direction: LineageDirection
    neighbors: list[ObservedLineageNeighbor]
    truncated: bool
