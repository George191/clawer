"""Write-only credentials and validated external source configuration."""

from __future__ import annotations

import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


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
