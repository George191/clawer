"""Safe read-only CDC health responses."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CdcModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ProbeState(CdcModel):
    state: Literal["unconfigured", "available", "unavailable", "not_ready"]
    reason: str | None = None


class ConnectorState(CdcModel):
    name: str
    state: str
    task_states: list[str]


class ConnectState(ProbeState):
    connectors: list[ConnectorState] = Field(default_factory=list)


class WalSlot(CdcModel):
    name: str
    active: bool
    retained_bytes: int | None = None
    unconfirmed_bytes: int | None = None


class PostgresState(ProbeState):
    wal_level: str | None = None
    expected_slot: str | None = None
    slots: list[WalSlot] = Field(default_factory=list)


class ConsumerLag(CdcModel):
    group: str
    lag: int
    partitions: int


class KafkaState(ProbeState):
    consumers: list[ConsumerLag] = Field(default_factory=list)


class CdcStatus(CdcModel):
    checked_at: str
    output_mode: Literal["debezium", "direct"]
    connect: ConnectState
    mongo: ProbeState
    postgres: PostgresState
    kafka: KafkaState
