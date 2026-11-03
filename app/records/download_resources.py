"""Pure download planning and resource identity shared by persistence and workers."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import unquote

from bson.json_util import default as bson_json_default

from app.downloader.adapters import DownloadItem, get_download_adapter, news
from app.utils.download_paths import iter_download_nodes, iter_download_values
from app.utils.path import get_nested_value


def resource_filename(url: str, suffix: str = "") -> str:
    """Produce the existing stable resource filename without network access."""
    name = unquote(url.split("?", 1)[0].rsplit("/", 1)[-1])
    stem, dot, ext = name.rpartition(".")
    if not stem or len(stem) > 60:
        stem = hashlib.md5(url.encode()).hexdigest()[:12]
    extension = f".{ext}" if dot and ext else ""
    return f"{stem}{suffix}{extension}"


def download_urls(value: Any, config: Any) -> list[tuple[str, str]]:
    """Resolve the URL representation supported by download configurations."""
    prefix = getattr(config, "url_prefix", None) or ""
    if isinstance(value, dict):
        fields = ("href", "src", "url", "link", "full", "thumbnail", "pdf")
        return [(field, prefix + str(value[field])) for field in fields if value.get(field)]
    return [("url", prefix + value)] if isinstance(value, str) and value else []


def template_download_items(record: dict[str, Any], configs: list[Any]) -> list[DownloadItem]:
    """Expand configured selectors, preserving array and recursive asset paths."""
    items = []
    for config in configs:
        if getattr(config.selector_type, "value", config.selector_type) != "json":
            continue
        collection = getattr(config, "collection", None)
        if collection and collection != (record.get("_meta") or {}).get("template"):
            continue
        recursive = getattr(config, "recursive", None)
        for prefix, node in iter_download_nodes(record, recursive):
            for path, value in iter_download_values(node, config.selector):
                values = value if isinstance(value, list) else [value]
                for index, entry in enumerate(values):
                    full_path = f"{prefix}.{path}" if prefix else path
                    if len(values) > 1:
                        full_path += f".{index}"
                    for field, url in download_urls(entry, config):
                        key = f"assets.{full_path}"
                        if isinstance(entry, dict):
                            key += f".{field}"
                        expanded = collection or recursive or "[]" in config.selector or "*" in config.selector
                        suffix = (
                            "_" + hashlib.md5(key.encode()).hexdigest()[:10] if expanded
                            else f"_{index:05d}" if len(values) > 1 else ""
                        )
                        version_selector = getattr(config, "version_selector", None)
                        version = get_nested_value(entry if isinstance(entry, dict) else node, version_selector) if version_selector else None
                        items.append(DownloadItem(
                            url=url, filename=resource_filename(url, suffix), asset_key=key, version=version,
                            attachment=getattr(config, "asset_type", None) == "attachment",
                        ))
    return items


def download_items(record: dict[str, Any], template: Any, template_name: str, data_type: str) -> list[DownloadItem]:
    """Resolve template and registered adapter items using the same rules as downloads."""
    items = template_download_items(record, template.download)
    adapter = get_download_adapter(record, template_name=template_name, data_type=data_type)
    if adapter is not None:
        additional = adapter(record, template, resource_filename)
        if adapter is news.download and items:
            declared = {(item.asset_key, source_fingerprint(item)) for item in items}
            additional = [item for item in additional if (item.asset_key, source_fingerprint(item)) not in declared]
        items.extend(additional)
    return items


def _json_plan_value(value: Any) -> Any:
    if isinstance(value, datetime):
        stamp = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return stamp.isoformat(timespec="milliseconds")
    if isinstance(value, date):
        return value.isoformat()
    return bson_json_default(value)


def encode_plan(items: list[DownloadItem]) -> list[dict[str, Any]]:
    """Freeze pure resource inputs in a form stable across BSON and JSON storage."""
    return json.loads(json.dumps([asdict(item) for item in items], default=_json_plan_value))


def decode_plan(value: list[dict[str, Any]]) -> list[DownloadItem]:
    """Restore the exact saved resource inputs independently of template changes."""
    return [DownloadItem(**entry) for entry in value]


def item_fingerprint(item: DownloadItem) -> str:
    """Identify every declared input that can change the stored resource."""
    return hashlib.sha256(json.dumps(encode_plan([item])[0], sort_keys=True).encode()).hexdigest()


def source_fingerprint(item: DownloadItem) -> str:
    """Identify downloads whose bytes and validation can be reused within a batch."""
    plan = encode_plan([item])[0]
    inputs = {key: plan[key] for key in ("url", "version", "attachment")}
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def resource_manifest(items: list[DownloadItem]) -> dict[str, str]:
    """Map each asset path to its inputs, rejecting conflicting destinations."""
    manifest = {}
    for item in items:
        fingerprint = item_fingerprint(item)
        if item.asset_key in manifest and manifest[item.asset_key] != fingerprint:
            raise ValueError(f"Conflicting download inputs for asset path {item.asset_key!r}")
        manifest[item.asset_key] = fingerprint
    return manifest


def encode_manifest(manifest: dict[str, str]) -> list[dict[str, str]]:
    """Persist paths as values so dotted asset paths never become BSON field names."""
    return [{"asset_key": key, "fingerprint": value} for key, value in sorted(manifest.items())]


def decode_manifest(value: Any) -> dict[str, str]:
    """Read a persisted resource manifest; legacy records have no verified inputs."""
    return {entry["asset_key"]: entry["fingerprint"] for entry in (value or [])}


def apply_record_updates(record: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Apply Mongo-style dotted field assignments to a detached record snapshot."""
    result = copy.deepcopy(record)
    for path, value in updates.items():
        parts = path.split(".")
        parent = result
        for part in parts[:-1]:
            if isinstance(parent, list):
                parent = parent[int(part)]
            else:
                parent = parent.setdefault(part, {})
        if isinstance(parent, list):
            parent[int(parts[-1])] = value
        else:
            parent[parts[-1]] = value
    return result


def preserve_resource_removals(record: dict[str, Any], incoming: dict[str, Any], configs: list[Any]) -> dict[str, Any]:
    """Honor explicit empty resource fields despite the general non-empty merge policy."""
    updates = {}

    def visit(node: Any, parts: list[str], prefix: str) -> None:
        if prefix and (node is None or node == "" or node == []):
            updates[prefix] = node
            return
        if not parts:
            return
        key, *rest = parts
        if key == "*" and isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, rest, f"{prefix}.{index}" if prefix else str(index))
        elif isinstance(node, dict) and key in node:
            visit(node[key], rest, f"{prefix}.{key}" if prefix else key)

    for config in configs:
        if getattr(config.selector_type, "value", config.selector_type) != "json":
            continue
        collection = getattr(config, "collection", None)
        if collection and collection != (record.get("_meta") or {}).get("template"):
            continue
        parts = config.selector.replace("[]", ".*").replace("[", ".").replace("]", "").split(".")
        for prefix, node in iter_download_nodes(incoming, getattr(config, "recursive", None)):
            visit(node, parts, prefix)
    return apply_record_updates(record, updates) if updates else record


def invalidate_assets(record: dict[str, Any], paths: set[str]) -> None:
    """Remove stale paths without shifting array indexes or modifying the old record."""
    if not paths or "assets" not in record:
        return
    record["assets"] = copy.deepcopy(record["assets"])
    for path in paths:
        parts = path.split(".")
        parent = get_nested_value(record, ".".join(parts[:-1]))
        if isinstance(parent, dict):
            parent.pop(parts[-1], None)
        elif isinstance(parent, list) and parts[-1].isdigit() and int(parts[-1]) < len(parent):
            parent[int(parts[-1])] = None


def prepare_download_state(
    existing: dict[str, Any] | None, record: dict[str, Any], template: Any,
    template_name: str, data_type: str,
) -> bool:
    """Requeue only changed resources, with one safe verification of legacy assets."""
    meta = record["_meta"]
    previous_meta = (existing or {}).get("_meta") or {}
    items = download_items(record, template, template_name, data_type)
    desired = resource_manifest(items)
    previous = decode_manifest(previous_meta.get("download_inputs"))
    verified = decode_manifest(previous_meta.get("downloaded_inputs"))
    if existing and "download_inputs" not in previous_meta:
        previous = resource_manifest(download_items(existing, template, template_name, data_type))
    changed = previous != desired
    legacy_verification = bool(existing and desired and "downloaded_inputs" not in previous_meta)
    plan_migration = bool(existing and desired and "download_plan" not in previous_meta)
    meta["download_inputs"] = encode_manifest(desired)
    meta["download_plan"] = encode_plan(items)
    if not existing or changed or legacy_verification or plan_migration:
        stale = {key for key in previous | verified if verified.get(key) != desired.get(key) or key not in desired}
        invalidate_assets(record, stale)
        meta["downloaded_inputs"] = encode_manifest({key: value for key, value in verified.items() if desired.get(key) == value})
        meta["download_status"] = "pending"
        meta.pop("download_claim_token", None)
        meta.pop("download_claimed_at", None)
        meta.pop("download_error", None)
        return True
    return False
