"""Shared ODS normalizer primitives."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any

from lxml import html as lxml_html


def safe_str(val: Any) -> str | None:
    if val is None:
        return None
    return str(val).strip() or None


def safe_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    text = str(val).strip()
    if not text:
        return None
    formats = [
        "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
        "%d-%m-%Y", "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.date()
        except ValueError:
            continue
    return None


def safe_datetime(val: Any) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, date):
        return datetime.combine(val, datetime.min.time(), tzinfo=timezone.utc)

    text = str(val).strip()
    if not text:
        return None

    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%d/%m/%Y, %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M:%S %z",
        "%m/%d/%Y %H:%M %z",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %I:%M:%S %p %z",
        "%m/%d/%Y %I:%M %p %z",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    if text.endswith("Z"):
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _pick_first(*vals: Any) -> Any:
    for v in vals:
        if v is not None:
            return v
    return None


def json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def html_to_text(html: Any) -> str | None:
    if html is None:
        return None

    text = str(html).strip()
    if not text:
        return None

    try:
        wrapper = lxml_html.fragment_fromstring(text, create_parent="div")
        content = " ".join(part.strip() for part in wrapper.text_content().split())
    except Exception:
        content = " ".join(text.split())

    return content or None


def _iter_asset_path_values(node: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    if isinstance(node, str):
        value = safe_str(node)
        return [(path, value)] if value else []
    if isinstance(node, dict):
        values: list[tuple[tuple[str, ...], str]] = []
        for key, value in node.items():
            values.extend(_iter_asset_path_values(value, (*path, str(key))))
        return values
    if isinstance(node, list):
        values = []
        for index, value in enumerate(node):
            values.extend(_iter_asset_path_values(value, (*path, str(index))))
        return values
    return []


def _ensure_path_child(parent: Any, key: str, next_key: str) -> Any:
    default_value: Any = [] if next_key.isdigit() else {}
    if isinstance(parent, dict):
        child = parent.get(key)
        if child is None or not isinstance(child, (dict, list)):
            child = default_value
            parent[key] = child
        return child
    if isinstance(parent, list) and key.isdigit():
        index = int(key)
        while len(parent) <= index:
            parent.append(None)
        child = parent[index]
        if child is None or not isinstance(child, (dict, list)):
            child = default_value
            parent[index] = child
        return child
    return None


def _set_path_value(target: dict[str, Any], path: tuple[str, ...], value: str) -> None:
    if not path:
        return

    current: Any = target
    for index, key in enumerate(path[:-1]):
        next_key = path[index + 1]
        current = _ensure_path_child(current, key, next_key)
        if current is None:
            return

    leaf = path[-1]
    if isinstance(current, dict):
        current[leaf] = value
    elif isinstance(current, list) and leaf.isdigit():
        item_index = int(leaf)
        while len(current) <= item_index:
            current.append(None)
        current[item_index] = value


def apply_asset_path_overrides(record: dict[str, Any]) -> tuple[dict[str, Any], set[tuple[str, ...]]]:
    assets = record.get("assets") or {}
    asset_paths: set[tuple[str, ...]] = set()
    if not isinstance(assets, (dict, list)):
        return record, asset_paths

    merged = deepcopy(record)
    for path, value in _iter_asset_path_values(assets):
        _set_path_value(merged, path, value)
        asset_paths.add(path)
    return merged, asset_paths


def normalize_generic(record: dict[str, Any]) -> dict[str, Any]:
    from app.etl.base import extract_meta

    meta = extract_meta(record)
    return {
        "data_source": meta.get("data_source", ""),
        "data_type": meta.get("data_type", "unknown"),
        "record_id": meta.get("record_id", ""),
        "title": safe_str(record.get("title")),
        "description": safe_str(record.get("description") or record.get("abstract") or record.get("summary")),
        "quality_score": 1.0,
        "quality_flags": "[]",
    }
