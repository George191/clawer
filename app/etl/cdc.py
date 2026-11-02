"""Convert committed PostgreSQL CDC rows to the existing ETL message contract."""

from __future__ import annotations

import base64
import json
import struct
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.config.settings import settings
from app.etl.schema.catalogs import validate_table_name

CDC_LAYERS = ("rds", "ods", "task", "dwd", "dws", "dim", "ads")
UNAVAILABLE_VALUE = "__spider_cdc_unavailable__"


def cdc_schemas() -> tuple[str, ...]:
    """Return current-state schemas, excluding history and application metadata."""
    return tuple(f"ts_{layer}" for layer in CDC_LAYERS)


def layer_topic(layer: str) -> str:
    """Resolve infrastructure routes from the existing application configuration."""
    if layer not in CDC_LAYERS:
        raise ValueError("Unsupported CDC layer")
    return getattr(settings, f"etl_{layer}_topic")


def _geometry(value: dict[str, Any]) -> str:
    """Encode a Debezium WKB/SRID struct as PostgreSQL EWKB hexadecimal text."""
    binary = base64.b64decode(value["wkb"], validate=True)
    if len(binary) < 5 or binary[0] not in (0, 1):
        raise ValueError("Invalid CDC geometry")
    endian = "<" if binary[0] else ">"
    geometry_type = struct.unpack(f"{endian}I", binary[1:5])[0]
    if value.get("srid") is not None and not geometry_type & 0x20000000:
        binary = (binary[:1] + struct.pack(f"{endian}I", geometry_type | 0x20000000)
                  + struct.pack(f"{endian}I", int(value["srid"])) + binary[5:])
    return binary.hex().upper()


def decode_value(value: Any, schema: dict[str, Any]) -> Any:
    """Decode Connect logical values without consulting the mutable source table."""
    if value == UNAVAILABLE_VALUE:
        raise ValueError("CDC contains unavailable TOAST data; verify REPLICA IDENTITY FULL")
    if value is None:
        return None
    name = schema.get("name", "")
    if name == "io.debezium.data.Json":
        return json.loads(value) if isinstance(value, str) else value
    if name in {"io.debezium.data.geometry.Geometry", "io.debezium.data.geometry.Geography"}:
        return _geometry(value)
    if name in {"org.apache.kafka.connect.data.Date", "io.debezium.time.Date"}:
        return (date(1970, 1, 1) + timedelta(days=value)).isoformat()
    if name == "org.apache.kafka.connect.data.Timestamp":
        return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=value)).isoformat()
    if name == "org.apache.kafka.connect.data.Time":
        return (datetime(1970, 1, 1) + timedelta(milliseconds=value)).time().isoformat()
    if name in {"io.debezium.time.Timestamp", "io.debezium.time.MicroTimestamp"}:
        delta = timedelta(microseconds=value) if name.endswith("MicroTimestamp") else timedelta(milliseconds=value)
        return (datetime(1970, 1, 1) + delta).isoformat()
    if name == "io.debezium.time.MicroTime":
        if value == 86400000000:
            return "24:00:00"
        return (datetime(1970, 1, 1) + timedelta(microseconds=value)).time().isoformat()
    if schema.get("type") == "struct":
        fields = {field["field"]: field for field in schema.get("fields", [])}
        return {key: decode_value(item, fields.get(key, {})) for key, item in value.items()}
    if schema.get("type") == "array":
        return [decode_value(item, schema.get("items", {})) for item in value]
    if schema.get("type") == "map":
        return {key: decode_value(item, schema.get("values", {})) for key, item in value.items()}
    return value


def committed_row(envelope: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    """Validate a schema-bearing Debezium event and return its exact committed row."""
    if not isinstance(envelope, dict):
        raise ValueError("Invalid PostgreSQL CDC envelope")
    payload = envelope.get("payload")
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError("Invalid PostgreSQL CDC payload")
    if payload.get("op") in {"d", "t"}:
        raise ValueError("Physical deletion has no ETL contract; use business soft deletion")
    if payload.get("op") not in {"c", "u", "r"}:
        return None
    source = payload.get("source") or {}
    schema_name, table = source.get("schema", ""), source.get("table", "")
    if schema_name not in cdc_schemas():
        raise ValueError("CDC source is outside current ETL schemas")
    layer = schema_name.removeprefix("ts_")
    if not table.startswith(f"{layer}_"):
        raise ValueError("CDC source table does not match its ETL layer")
    after_schema = next((field for field in envelope.get("schema", {}).get("fields", [])
                         if field.get("field") == "after"), None)
    if after_schema is None or not isinstance(payload.get("after"), dict):
        raise ValueError("CDC requires schema-bearing full after images")
    row = decode_value(payload["after"], after_schema)
    if not all(row.get(field) for field in ("record_id", "data_source", "data_type")):
        raise ValueError("CDC row is missing business identity")
    validate_table_name(row["data_type"])
    if table != f"{layer}_{row['data_type']}":
        raise ValueError("CDC requires parent-table publication and matching business type")
    if not isinstance(source.get("lsn"), int):
        raise ValueError("CDC source LSN is required for downstream version checks")
    row["_cdc_meta"] = {"lsn": source["lsn"], "schema": schema_name, "table": table}
    return layer, table, row


def row_outputs(layer: str, row: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Route committed state to business topics; graph output keeps its existing gate."""
    outputs = [(f"{layer_topic(layer)}-{row['data_type']}" if layer == "rds" else layer_topic(layer), row)]
    if layer == "dwd":
        from app.etl.ts_dwd import graph_record_complete

        if graph_record_complete(row):
            outputs.append((settings.etl_graph_topic, row))
    return outputs
