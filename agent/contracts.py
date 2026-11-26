"""Validated public contracts and bounded execution policy."""

from __future__ import annotations

import ipaddress
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.web.utils.validation import validate_target_url

MAX_ATTEMPTS = 3
LEASE_SECONDS = 45
HEARTBEAT_SECONDS = 10
MAX_CHECKPOINT_BYTES = 524288
STAGES = ("capture", "template", "adapter", "validate")


def public_url(value: str) -> str:
    validate_target_url(value)
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        raise ValueError("Collection requires a public host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Collection requires a public address")
    if parsed.port == 0:
        raise ValueError("Invalid port")
    return value


class CollectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str = Field(min_length=1, max_length=4096)
    goal: str = Field(min_length=1, max_length=4000)
    max_pages: int = Field(default=20, ge=1, le=100)
    max_fields: int = Field(default=50, ge=1, le=100)
    agent_id: UUID | None = None

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str) -> str:
        return public_url(value)


class AgentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    model: str = Field(default="gpt-6-sol", min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$")
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    timeout_seconds: int = Field(default=120, ge=10, le=300)
    max_output_tokens: int = Field(default=8192, ge=1024, le=16384)

    @field_validator("base_url")
    @classmethod
    def check_endpoint(cls, value: str) -> str:
        parsed = urlsplit(public_url(value))
        if parsed.scheme != "https" or parsed.query or parsed.fragment:
            raise ValueError("Model endpoint requires HTTPS without query or fragment")
        return value.rstrip("/")


class SettingsUpdate(AgentSettings):
    api_key: SecretStr | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    expected_revision: int = Field(ge=0)


class SettingsView(AgentSettings):
    connection_verified: bool = False
    api_key_configured: bool = False
    revision: int = 0


class RunConflict(Exception):
    """A conflicting request, execution lease or settings revision."""


class LeaseLost(Exception):
    """A cancelled/reclaimed worker must stop without committing more output."""
