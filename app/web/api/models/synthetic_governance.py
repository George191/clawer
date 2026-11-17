"""Fixed marking vocabulary for the local synthetic pilot only."""

from typing import Literal

from pydantic import BaseModel, Field

SandboxMarking = Literal["sandbox-synthetic", "sandbox-restricted"]


class SyntheticMarked(BaseModel):
    markings: list[SandboxMarking] | None = Field(default=None, exclude_if=lambda value: value is None)
