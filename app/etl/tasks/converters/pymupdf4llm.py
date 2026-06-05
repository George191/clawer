"""PyMuPDF4LLM PDF → Markdown 转换器。

基于 PyMuPDF4LLM（AGPL-3.0 许可证），提供高效的 PDF 转 Markdown 转换。

特性：
- 表格 → Markdown 表格语法（| col | col |）
- 标题层级 → # ## ###（基于字体大小分析）
- 粗体/斜体 → **bold** / *italic*
- 多栏排版正确阅读顺序
- 页眉/页脚检测与清理
- 可选 OCR（扫描件）
- 图片写入或 base64 内嵌
- 直接读取 fitz.Document 内存流，无需临时文件

依赖：pip install pymupdf4llm
"""

from __future__ import annotations

import logging

import fitz
import pymupdf4llm

from app.etl.tasks.converters.base import BaseConverter, ConvertResult

logger = logging.getLogger(__name__)


class PyMuPDF4LLMConverter(BaseConverter):
    name = "pymupdf4llm"

    def convert(self, pdf_bytes: bytes) -> ConvertResult:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            markdown = pymupdf4llm.to_markdown(doc)
            page_count = doc.page_count

            logger.debug(
                "PyMuPDF4LLMConverter: %d pages → %d chars",
                page_count,
                len(markdown),
            )
            return ConvertResult(
                content=markdown,
                page_count=page_count,
            )
        finally:
            doc.close()