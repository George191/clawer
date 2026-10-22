"""Canonical business-schema field names, without changing data identifiers."""

from collections.abc import Mapping
from functools import lru_cache
import re
from typing import Any


SYSTEM_ROOT_FIELDS = frozenset({"_id", "_meta", "_kafka_meta", "_meta_search_params"})
FIELD_NAME_OVERRIDES = {
    "power_dBm": "power_dbm", "gt_dB_K": "gt_db_k", "Award$": "award_amount_raw",
}
# These objects are dictionaries indexed by taxonomy, concept, or unit values.
# Their entry values still contain ordinary schema and are traversed below.
DYNAMIC_KEY_PATHS = (
    ("facts",), ("facts", "*"), ("facts", "*", "*", "units"),
    ("featured_media", "media_details", "sizes"),
)


class FieldNameCollision(ValueError):
    """Different source field names would occupy one canonical field."""


@lru_cache(maxsize=8192)
def canonical_field_name(key: str) -> str:
    if not isinstance(key, str):
        raise ValueError("Business schema field names must be strings")
    if key in FIELD_NAME_OVERRIDES:
        return FIELD_NAME_OVERRIDES[key]
    # Preserve acronym groups while splitting their following PascalCase word.
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^\w]+", "_", value, flags=re.UNICODE).strip("_").lower()
    value = re.sub(r"_+", "_", value)
    if not value:
        raise ValueError("Business schema field name has no letters or digits")
    return value


def _dynamic_path(path: tuple[str, ...]) -> bool:
    return any(len(path) == len(pattern) and all(expected == "*" or expected == actual
        for actual, expected in zip(path, pattern)) for pattern in DYNAMIC_KEY_PATHS)


def _walk_record_fields(record: Mapping[str, Any], *, normalize: bool) -> Any:
    def visit(value: Any, path: tuple[str, ...]) -> Any:
        if isinstance(value, Mapping):
            output: dict[str, Any] = {}
            originals: dict[str, str] = {}
            dynamic = _dynamic_path(path)
            pairs = []
            for key, child in value.items():
                if key in SYSTEM_ROOT_FIELDS:
                    originals[key] = key
                    pairs.append((key, key, child, True))
                    continue
                target = key if dynamic else canonical_field_name(key)
                if target in originals and originals[target] != key:
                    location = ".".join(path) or "<root>"
                    raise FieldNameCollision(f"Field-name collision at {location}: {originals[target]!r}, {key!r} -> {target!r}")
                if not path and target in SYSTEM_ROOT_FIELDS:
                    raise FieldNameCollision(f"Business field {key!r} would overwrite system field {target!r}")
                originals[target] = key
                pairs.append((key, target, child, False))
            for key, target, child, system in pairs:
                if not normalize and key != target:
                    location = ".".join(path) or "<root>"
                    raise ValueError(f"Noncanonical field at {location}: {key!r}; expected {target!r}")
                result = child if system else visit(child, (*path, str(target)))
                if normalize:
                    output[target] = result
            return output if normalize else value
        if isinstance(value, list):
            if normalize:
                return [visit(child, path) for child in value]
            for child in value:
                visit(child, path)
            return value
        if isinstance(value, tuple):
            if normalize:
                return tuple(visit(child, path) for child in value)
            for child in value:
                visit(child, path)
            return value
        return value

    return visit(record, ())


def normalize_record_fields(record: Mapping[str, Any], *, data_source: str | None = None) -> dict[str, Any]:
    """Return canonical business fields; consume source aliases before calling.

    System values, including embedded record metadata, and known data-map keys remain exact. Source names
    document the caller and never determine whether identifiers are preserved.
    """
    return _walk_record_fields(record, normalize=True)


def validate_record_field_names(record: Mapping[str, Any], *, data_source: str | None = None) -> None:
    """Reject noncanonical schema or collisions without converting the payload."""
    _walk_record_fields(record, normalize=False)
