from __future__ import annotations

from typing import Any, Callable

from app.etl.normalizers.base import normalize_generic
from app.logger import get_logger

logger = get_logger(__name__)

_NORMALIZER_REGISTRY: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def register_normalizer(
    data_type: str,
    source: str | None,
    fn: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    key = f"{data_type}:{source}" if source else data_type
    _NORMALIZER_REGISTRY[key] = fn
    logger.info("Normalizer registered: %s", key)


def get_normalizer(
    data_type: str,
    data_source: str,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    specific_key = f"{data_type}:{data_source}"
    if specific_key in _NORMALIZER_REGISTRY:
        return _NORMALIZER_REGISTRY[specific_key]
    logger.warning(
        "No source-specific normalizer registered for %s, fallback to generic",
        specific_key,
    )
    return normalize_generic


from app.etl.normalizers import (
    intelligence,  # noqa: E402, F401
    navwarn,  # noqa: E402, F401
    news,  # noqa: E402, F401
    patent,  # noqa: E402, F401
)
