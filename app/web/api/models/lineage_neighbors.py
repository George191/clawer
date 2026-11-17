"""Bounded, declared Explorer table relationships (not movement evidence)."""

from typing import Literal

from pydantic import BaseModel, Field

LineageDirection = Literal["incoming", "outgoing"]
LineageNeighborKind = Literal["object_relation", "foreign_key"]


class LineageNeighbor(BaseModel):
    schema_name: str = Field(alias="schema")
    table: str
    kinds: list[LineageNeighborKind]


class LineageNeighbors(BaseModel):
    direction: LineageDirection
    neighbors: list[LineageNeighbor]
    truncated: bool
