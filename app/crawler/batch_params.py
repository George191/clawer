"""Shared parsing for single-field and multi-field batch inputs."""

from __future__ import annotations

import csv
from collections.abc import Callable


BatchParamNames = str | list[str]


def normalize_batch_param_names(value: BatchParamNames) -> list[str]:
    if isinstance(value, str):
        names = [item.strip() for item in value.split(",") if item.strip()]
    else:
        names = [str(item).strip() for item in value if str(item).strip()]
    if not names:
        raise ValueError("Batch parameter name is required")
    if len(names) != len(set(names)):
        raise ValueError("Batch parameter names must be unique")
    return names


def batch_param_identity(value: BatchParamNames) -> str:
    return ",".join(normalize_batch_param_names(value))


def parse_batch_param_line(
    line: str,
    param_names: BatchParamNames,
    *,
    delimiter: str = ",",
) -> dict[str, str]:
    names = normalize_batch_param_names(param_names)
    if len(names) == 1:
        value = line.strip()
        if not value:
            raise ValueError("Batch parameter value cannot be empty")
        return {names[0]: value}
    if len(delimiter) != 1:
        raise ValueError("Batch delimiter must be one character")

    values = next(csv.reader([line], delimiter=delimiter, skipinitialspace=True))
    values = [value.strip() for value in values]
    if len(values) != len(names):
        raise ValueError(
            f"Expected {len(names)} batch values ({', '.join(names)}), "
            f"got {len(values)}: {line!r}"
        )
    if any(not value for value in values):
        raise ValueError(f"Batch parameter values cannot be empty: {line!r}")
    return dict(zip(names, values, strict=True))


def build_batch_params(
    batch_data: list[str],
    param_names: BatchParamNames,
    builder: Callable[[list[str], str], str],
    *,
    delimiter: str = ",",
) -> dict[str, str]:
    names = normalize_batch_param_names(param_names)
    if len(names) == 1:
        return {names[0]: builder(batch_data, names[0])}
    if len(batch_data) != 1:
        raise ValueError("Multi-field batch_params requires batch_size=1")
    return parse_batch_param_line(
        batch_data[0],
        names,
        delimiter=delimiter,
    )
