"""PyMuPDF PDF → 文本提取转换器。

基于 PyMuPDF (fitz)，直接提取 PDF 全部文本。

依赖：pip install PyMuPDF
"""

from __future__ import annotations

import logging

import fitz

from app.etl.tasks.converters.base import BaseConverter, ConvertResult

logger = logging.getLogger(__name__)


class PyMuPDFConverter(BaseConverter):
    name = "pymupdf"

    def convert(self, pdf_bytes: bytes) -> ConvertResult:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            text = "\n".join(page.get_text() for page in doc)
            page_count = doc.page_count

            logger.debug(
                "PyMuPDFConverter: %d pages → %d chars",
                page_count,
                len(text),
            )
            return ConvertResult(
                content=text,
                page_count=page_count,
            )
        finally:
            doc.close()