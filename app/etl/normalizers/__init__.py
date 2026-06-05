"""ODS 标准化器注册中心。

所有标准化器通过 Key = "{data_type}:{data_source}" 注册。
消费端调用 get_normalizer(data_type, data_source) 获取对应的标准化函数。

扩展新数据源：
    1. 在 normalizers/ 下新建模块（如 normalizers/uspto.py）
    2. 实现 normalize_xxx_patent(record) -> dict 函数
    3. 在模块末尾调用 register_normalizer("patent", "uspto", normalize_uspto_patent)
    4. 在 ts_ods.py 中 import 该模块以触发注册
"""

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
    if data_type in _NORMALIZER_REGISTRY:
        return _NORMALIZER_REGISTRY[data_type]
    return normalize_generic


from app.etl.normalizers import patent  # noqa: E402, F401 — 触发注册