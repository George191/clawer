"""Resolve configured JSON download paths while preserving their array indexes."""
from typing import Any


def iter_download_values(value: Any, path: str):
    parts = path.replace("[]", ".*").replace("[", ".").replace("]", "").split(".")

    def visit(node, remaining, prefix):
        if not remaining:
            yield prefix, node
            return
        key, *rest = remaining
        if key == "*" and isinstance(node, list):
            for index, child in enumerate(node):
                yield from visit(child, rest, f"{prefix}.{index}" if prefix else str(index))
        elif isinstance(node, dict) and key in node:
            yield from visit(node[key], rest, f"{prefix}.{key}" if prefix else key)
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            yield from visit(node[int(key)], rest, f"{prefix}.{key}" if prefix else key)

    yield from visit(value, parts, "")


def iter_download_nodes(record: dict[str, Any], recursive: str | None, prefix: str = ""):
    yield prefix, record
    if recursive:
        for path, child in iter_download_values(record, recursive):
            if isinstance(child, dict):
                full_path = f"{prefix}.{path}" if prefix else path
                yield from iter_download_nodes(child, recursive, full_path)
