from __future__ import annotations

import logging
from typing import Any, Callable

from app.etl.normalizers.base import normalize_generic

logger = logging.getLogger(__name__)

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


from app.etl.normalizers import intelligence  # noqa: E402, F401
from app.etl.normalizers import navwarn  # noqa: E402, F401
from app.etl.normalizers import news  # noqa: E402, F401
from app.etl.normalizers import patent  # noqa: E402, F401
