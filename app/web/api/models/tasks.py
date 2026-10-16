from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class WorkspaceTaskRequest(BaseModel):
    name: str = Field(min_length=1)
    template_name: str = Field(min_length=1)
    template_version: str = "v1.0"
    schedule: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    policies: dict[str, Any] = Field(default_factory=dict)
    owner: str = "AI Collect"


class TaskAction(BaseModel):
    action: Literal[
        "start", "restart", "pause", "resume", "cancel",
        "start_download", "pause_download", "start_sync", "pause_sync", "cancel_sync",
    ]


class AutomationTaskRequest(WorkspaceTaskRequest):
    product_domain: str = Field(default="ai-collect", min_length=1)
    task_type: Literal["template", "celery"] = "template"
    task_path: str | None = Field(default=None, min_length=1)
    template_name: str | None = Field(default=None, min_length=1)
    template_version: str | None = "v1.0"

    @model_validator(mode="after")
    def validate_executor(self):
        if self.task_type == "template" and (not self.template_name or not self.template_version):
            raise ValueError("template_name and template_version are required for template tasks")
        if self.task_type == "celery" and not self.task_path:
            raise ValueError("task_path is required for celery tasks")
        return self
