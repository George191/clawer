"""Immutable orbital versions in one TimescaleDB ODS hypertable."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from sqlalchemy import text

from app.etl.schema.catalogs import current_insert_sql, current_table_ddl
from app.etl.source_fences import snapshot_matches

ETL_VERSION = "orbital-v1"


def immutable_event_payload(record: dict) -> dict:
    """Strip Kafka delivery metadata while retaining all canonical event fields."""
    return {key: value for key, value in record.items() if key != "_kafka_meta"}


def event_payload_fingerprint(record: dict) -> str:
    content = json.dumps(immutable_event_payload(record), ensure_ascii=False, sort_keys=True,
                         default=str, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def event_lineage(meta: dict) -> dict:
    event_id = meta.get("event_id")
    revision = meta.get("event_sequence")
    observed = meta.get("event_observed_at")
    if not isinstance(event_id, str) or not re.fullmatch(r"[0-9a-f]{32}", event_id):
        raise ValueError("Orbital event_id must be UUID hex")
    if type(revision) is not int or not 0 < revision < 2 ** 63:
        raise ValueError("Orbital event_sequence must be a positive bigint")
    if isinstance(observed, str):
        observed = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    if not isinstance(observed, datetime) or observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("Orbital event_observed_at must be timezone aware")
    observed = observed.astimezone(timezone.utc)
    if observed.microsecond % 1000:
        raise ValueError("Orbital event_observed_at must use Mongo millisecond precision")
    if not isinstance(meta.get("record_id"), str) or not meta["record_id"].strip():
        raise ValueError("Orbital event requires fact record_id")
    return dict(source_event_id=event_id, source_revision=revision,
                source_observed_at=observed, etl_version=ETL_VERSION)


def history_ddl() -> list[str]:
    return [
        "CREATE SCHEMA IF NOT EXISTS ts_ods",
        current_table_ddl("ods", "orbital"),
    ]


async def ensure_history_schema(pg) -> None:
    await pg.init_schema(history_ddl())


async def write_orbital_output(session, payload: dict, columns: dict) -> dict:
    """Append each immutable event/version exactly once, including late revisions."""
    identity = {name: payload[name] for name in ("record_id", "data_source", "data_type")}
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 0))"),
                          {"name": "orbital:" + json.dumps(identity, sort_keys=True)})
    same_revision = (await session.execute(text(
        "SELECT source_event_id FROM ts_ods.ods_orbital WHERE record_id=:record_id "
        "AND data_source=:data_source AND data_type=:data_type AND source_revision=:source_revision LIMIT 1"
    ), payload)).mappings().first()
    if same_revision and same_revision["source_event_id"] != payload["source_event_id"]:
        raise ValueError("Orbital revision reused with a different event identity")
    await session.execute(text(current_insert_sql("ods", "orbital", columns)), payload)
    existing = (await session.execute(text(
        "SELECT * FROM ts_ods.ods_orbital WHERE source_observed_at=:source_observed_at "
        "AND source_event_id=:source_event_id AND etl_version=:etl_version"
    ), payload)).mappings().first()
    expected = {name: json.loads(value) if columns[name].split()[0].lower() == "jsonb" and isinstance(value, str)
                else value for name, value in payload.items() if name not in {"created_at", "updated_at"}}
    if existing is None or not snapshot_matches(expected, existing):
        raise ValueError("Orbital normalization changed without an ETL_VERSION change")
    return dict(existing)
