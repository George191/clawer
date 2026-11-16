from __future__ import annotations

from importlib import import_module
from typing import Any, Callable

from app.logger import get_logger

logger = get_logger(__name__)

_NORMALIZER_REGISTRY: dict[str, Callable[[dict[str, Any]], Any]] = {}
_NORMALIZER_MODULES: dict[str, str] = {
    "acticle": "acticle",
    "intelligence": "intelligence",
    "navwarn": "navwarn",
    "news": "news",
    "patent": "patent",
    "organization": "organization",
    "filing": "filing",
    "filing_document": "filing_document",
    "financial_fact": "financial_fact",
    "contract": "procurement",
    "tender": "procurement",
    "search_result": "search_result",
    "account": "social_account",
    "tweet": "social_content",
    "constellation": "space",
    "debrisevents": "space",
    "debris": "space",
    "launchvehicles": "space",
    "frequency": "space",
    "groundstations": "space",
    "launchinfo": "space",
    "satellite": "space",
    "launchsites": "space",
    "orbital": "space",
    "vulnerability": "vulnerability",
}


def register_normalizer(
    data_type: str,
    source: str,
    fn: Callable[[dict[str, Any]], Any],
) -> None:
    key = f"{data_type}:{source}"
    _NORMALIZER_REGISTRY[key] = fn
    logger.info("Normalizer registered: %s", key)


def get_normalizer(
    data_type: str,
    data_source: str,
) -> Callable[[dict[str, Any]], Any]:
    specific_key = f"{data_type}:{data_source}"
    if specific_key in _NORMALIZER_REGISTRY:
        return _NORMALIZER_REGISTRY[specific_key]
    module = _NORMALIZER_MODULES.get(data_type)
    if module:
        import_module(f".{module}", __name__)
    if specific_key in _NORMALIZER_REGISTRY:
        return _NORMALIZER_REGISTRY[specific_key]
    generic_key = f"{data_type}:*"
    if generic_key in _NORMALIZER_REGISTRY:
        return _NORMALIZER_REGISTRY[generic_key]
    logger.warning(
        "No source-specific normalizer registered for %s, fallback to generic",
        specific_key,
    )
