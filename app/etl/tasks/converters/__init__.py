"""PDF 转换器工具包。

提供统一的 PDF → Markdown 转换接口，支持多种后端（Docling、PyMuPDF 等）。

架构：
    _convert(pdf_bytes) → get_converter(name) → converter.convert(pdf_bytes) → ConvertResult

扩展新转换器：
    1. 在 converters/ 下新建模块（如 converters/marker.py）
    2. 继承 BaseConverter 实现 convert(pdf_bytes: bytes) -> ConvertResult
    3. 在模块末尾调用 _register(MarkerConverter)
    4. 使用 get_converter("marker") 获取实例
"""

from __future__ import annotations

import logging
from typing import Type

from app.etl.tasks.converters.base import BaseConverter

logger = logging.getLogger(__name__)

_CONVERTER_REGISTRY: dict[str, BaseConverter] = {}
_DEFAULT = "pymupdf"


def _register(converter_cls: Type[BaseConverter]) -> None:
    instance = converter_cls()
    _CONVERTER_REGISTRY[instance.name] = instance
    logger.info("Converter registered: %s", instance.name)


def get_converter(name: str | None = None) -> BaseConverter:
    name = name or _DEFAULT
    converter = _CONVERTER_REGISTRY.get(name)
    if converter is None:
        available = list(_CONVERTER_REGISTRY)
        raise KeyError(f"Converter '{name}' not found. Available: {available}")
    return converter


from app.etl.tasks.converters import docling  # noqa: E402, F401
from app.etl.tasks.converters import pymupdf4llm  # noqa: E402, F401
from app.etl.tasks.converters import pymupdf  # noqa: E402, F401

_register(docling.DoclingConverter)
_register(pymupdf4llm.PyMuPDF4LLMConverter)
_register(pymupdf.PyMuPDFConverter)
