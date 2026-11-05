"""Build canonical records from a current source row and explicit field aliases."""

from collections.abc import Mapping
from typing import Any

from app.records.field_names import FieldNameCollision, SYSTEM_ROOT_FIELDS, normalize_record_fields
from app.records.values import record_values_equal


def has_canonical_field(record: Mapping[str, Any], path: str) -> bool:
    """Test target presence, including typed CSV contacts, without comparing values."""
    current: Any = record
    for component in path.split("."):
        key, bracket, selector = component.partition("[")
        if not isinstance(current, Mapping) or key not in current:
            return False
        current = current[key]
        if bracket:
            if not selector.endswith("]") or not isinstance(current, list):
                return False
            matches = [item for item in current if isinstance(item, Mapping) and item.get("type") == selector[:-1]]
            if not matches:
                return False
            current = matches[0]
    return True


def canonical_record(
    raw: Mapping[str, Any], normalized: Mapping[str, Any], aliases: Mapping[str, str],
    *, nested_aliases: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Consume mapped native keys and retain every other source field.

    The parser supplies already-normalized targets. A source alias remains when
    its target is absent, because it is then the only copy. An explicit null
    target counts as present. Explicit nested paths consume only the mapped
    leaf and its emptied parents; unknown siblings remain. No values are
    compared or converted here.
    """
    remaining = {key: value for key, value in raw.items()
                 if key not in aliases or not has_canonical_field(normalized, aliases[key])}

    def consume(value: Any, path: list[str]) -> Any:
        if not isinstance(value, Mapping) or path[0] not in value:
            return value
        result = dict(value)
        key = path[0]
        if len(path) == 1:
            del result[key]
        else:
            child = consume(value[key], path[1:])
            if child is value[key]:
                return value
            if child:
                result[key] = child
            else:
                del result[key]
        return result

    for native, target in (nested_aliases or {}).items():
        path = native.split(".")
        if not all(path) or any(key in SYSTEM_ROOT_FIELDS for key in path):
            raise ValueError("Nested source aliases must reference business fields")
        if has_canonical_field(normalized, target):
            remaining = consume(remaining, path)
    for key in remaining.keys() & normalized.keys():
        if not record_values_equal(remaining[key], normalized[key]):
            raise FieldNameCollision(f"Unmapped source field would be overwritten: {key!r}")
    return normalize_record_fields({**remaining, **normalized})
