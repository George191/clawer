from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class WorkspaceTaskRequest(BaseModel):
    name: str = Field(min_length=1)
    template_name: str = Field(min_length=1)
    template_version: str = "v1.0"
    schedule: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    policies: dict[str, Any] = Field(default_factory=dict)
    owner: str = "AI Collect"

    @field_validator("policies")
    @classmethod
    def normalize_policies(cls, value: dict[str, Any]) -> dict[str, Any]:
        # Time fields belong to the template; runtime watermarks belong to Redis.
        return {key: item for key, item in value.items() if key not in {"watermark", "incremental_field"}}


class TaskAction(BaseModel):
    action: Literal[
        "start", "restart", "pause", "resume", "cancel",
        "start_download", "pause_download", "start_sync", "pause_sync", "cancel_sync",
    ]
