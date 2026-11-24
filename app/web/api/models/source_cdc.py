"""Configuration for one continuous capture task per saved source."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class MilvusCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kafkaBrokers: str = Field(min_length=1, max_length=2000, pattern=r"^[A-Za-z0-9_.:\[\],-]+$")
    etcdEndpoints: list[str] = Field(min_length=1, max_length=10)
    etcdRoot: str = Field(default="by-dev", min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_/-]+$")
    pchannels: list[str] = Field(min_length=1, max_length=16)

    @field_validator("etcdEndpoints")
    @classmethod
    def endpoints(cls, values):
        from urllib.parse import urlsplit
        for value in values:
            url = urlsplit(value)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
                raise ValueError("Use etcd HTTP(S) endpoints without credentials, paths or query strings")
        return values

    @field_validator("pchannels")
    @classmethod
    def channels(cls, values):
        import re
        if len(set(values)) != len(values) or any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,248}", value) for value in values):
            raise ValueError("Use unique explicit physical channel names")
        return values


class SourceCdcCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedUpdatedAt: AwareDatetime
    tables: list[str] = Field(min_length=1, max_length=100)
    snapshot: Literal["initial", "no_data"] = "initial"
    publication: str | None = Field(default=None, pattern=r"^[a-z_][a-z0-9_]{0,62}$")
    serverId: int | None = Field(default=None, ge=1, le=4294967295)
    oracleDatabase: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
    oraclePdb: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
    milvus: MilvusCapture | None = None

    @field_validator("tables")
    @classmethod
    def qualified_names(cls, values: list[str]) -> list[str]:
        import re

        if len(set(values)) != len(values) or any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*\.[A-Za-z_][A-Za-z0-9_$]*", name)
            for name in values
        ):
            raise ValueError("Use unique explicit schema.table or database.collection names")
        return values


class SourceCdcAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedUpdatedAt: AwareDatetime
    action: Literal["pause", "resume", "restart", "remove"]


class ObjectEventsCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedUpdatedAt: AwareDatetime
    expectedConfigurationHash: str = Field(pattern=r"^[a-f0-9]{64}$")
    targetArn: str = Field(pattern=r"^arn:minio:sqs::[A-Za-z0-9_-]+:kafka$")
    prefix: str = Field(default="", max_length=1024)
    suffix: str = Field(default="", max_length=1024)
    events: list[Literal["s3:ObjectCreated:*", "s3:ObjectRemoved:*"]] = Field(min_length=1, max_length=2)

    @field_validator("events")
    @classmethod
    def distinct_events(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Select each event type once")
        return values


class ObjectEventsRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedUpdatedAt: AwareDatetime
    expectedConfigurationHash: str = Field(pattern=r"^[a-f0-9]{64}$")


class DamengCdcBind(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedUpdatedAt: AwareDatetime
    topic: str = Field(min_length=1, max_length=249, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
    tables: list[str] = Field(min_length=1, max_length=100)

    _tables = field_validator("tables")(SourceCdcCreate.qualified_names.__func__)


class DamengCdcRemove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedUpdatedAt: AwareDatetime
