"""Write-only credentials and validated external source configuration."""

from __future__ import annotations

import ipaddress
import re
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


Engine = Literal["postgresql", "mysql", "oracle", "dameng", "elasticsearch", "milvus"]


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535, strict=True)
    database: str = Field(default="", max_length=128)
    username: str = Field(default="", max_length=200)
    auth: Literal["password", "api_key", "token"] = "password"
    tls: bool = Field(default=True, strict=True)

    @field_validator("host")
    @classmethod
    def hostname(cls, value: str) -> str:
        try:
            ipaddress.ip_address(value)
        except ValueError:
            if not re.fullmatch(r"[a-zA-Z0-9_](?:[a-zA-Z0-9_.-]*[a-zA-Z0-9_])?", value):
                raise ValueError("Enter a hostname or IP address without a URL, path or credentials")
        return value

    @field_validator("database", "username")
    @classmethod
    def no_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 for character in value):
            raise ValueError("Control characters are not allowed")
        return value


class SourceCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr | None = None
    apiKey: SecretStr | None = None
    token: SecretStr | None = None


class DataConnectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    engine: Engine
    config: SourceConfig
    credentials: SourceCredentials = Field(default_factory=SourceCredentials)

    @model_validator(mode="after")
    def engine_configuration(self):
        config = self.config
        allowed = {"password", "api_key"} if self.engine == "elasticsearch" else {"password", "token"} if self.engine == "milvus" else {"password"}
        if config.auth not in allowed:
            raise ValueError("This authentication method is not supported by the selected connector")
        if config.auth == "password" and not config.username:
            raise ValueError("Username is required for password authentication")
        if config.auth != "password" and config.username:
            raise ValueError("Username must be empty for token or API key authentication")
        if self.engine in {"postgresql", "mysql", "oracle", "milvus"} and not config.database:
            raise ValueError("Database or service name is required")
        if self.engine in {"elasticsearch", "dameng"} and config.database:
            raise ValueError("This connector does not accept a database scope")
        if self.engine == "dameng" and config.tls:
            raise ValueError("Dameng certificate transport requires a vendor-configured DPI client; this connector currently supports its default transport only")
        expected = {"password": "password", "api_key": "apiKey", "token": "token"}[config.auth]
        for key, value in self.credentials:
            if value is not None and key != expected:
                raise ValueError("Credentials must match the selected authentication method")
            if value is not None and not 1 <= len(value.get_secret_value()) <= 8192:
                raise ValueError("Credentials must contain between 1 and 8192 characters")
        return self


class DataConnectionPauseChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paused: bool = Field(strict=True)
    expectedUpdatedAt: AwareDatetime


SavedSourceCapabilityReason = Literal[
    "source_paused", "driver_missing", "unsupported_engine", "explicit_local_required",
    "mode_disabled", "numeric_loopback_required", "source_identity_not_configured",
    "source_identity_unverified", "source_identity_stale", "source_identity_policy_disabled",
    "source_identity_registry_unavailable",
]


class SavedSourceCapability(BaseModel):
    status: Literal["available_to_try", "unavailable"]
    reason: SavedSourceCapabilityReason | None = None


class SavedSourceCapabilities(BaseModel):
    connectionTest: SavedSourceCapability
    catalog: SavedSourceCapability
    snapshot: SavedSourceCapability
    committedBatch: SavedSourceCapability
    reviewedRange: SavedSourceCapability


class SourceRelation(BaseModel):
    name: str
    kind: Literal["table", "partitioned_table", "view", "materialized_view", "foreign_table"]


class SourceCatalog(BaseModel):
    schemas: list[str]
    schemaHasMore: bool
    schema_name: str | None = Field(alias="schema")
    relations: list[SourceRelation]
    hasMore: bool


class SourceColumn(BaseModel):
    name: str
    type: str
    nullable: bool
    primaryKey: bool
    position: int


class SourceColumns(BaseModel):
    schema_name: str = Field(alias="schema")
    table: str
    columns: list[SourceColumn]
    hasMore: bool
