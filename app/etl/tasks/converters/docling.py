"""Docling PDF → Markdown 转换器。

基于 IBM Docling（MIT 许可证），提供高质量的 PDF 转 Markdown 转换。

特性：
- 表格 → Markdown 表格语法（| col | col |）
- 标题层级 → # ## ###
- 粗体/斜体 → **bold** / *italic*
- 公式 → LaTeX（$$ ... $$）
- 图片提取与位置标记
- 多栏排版正确阅读顺序
- 页眉/页脚自动清理
- 直接读取 BytesIO 二进制流，无需临时文件

依赖：pip install docling
"""

from __future__ import annotations

import logging
from io import BytesIO

from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions, AcceleratorOptions, AcceleratorDevice
from app.etl.tasks.converters.base import BaseConverter, ConvertResult

logger = logging.getLogger(__name__)


pipeline_options = PdfPipelineOptions(
    # ── 内存大头：关闭耗内存的特性 ──
    do_ocr=False,                # 默认 False，关 OCR 大幅减少内存
    do_table_structure=True,    # 默认 False，TableFormer 模型 ~500MB

    # ── 超时保护 ──
    document_timeout=120.0,      # 超时返回 PARTIAL_SUCCESS，不会卡死

    # ── 硬件限制 ──
    accelerator_options=AcceleratorOptions(
        num_threads=1,           # 默认 1，设为 2~4 会成倍增加内存
        device=AcceleratorDevice.CPU,  # 强制 CPU，不用 GPU 显存
    ),
    do_code_enrichment=False,
    do_formula_enrichment=True,
    # 可选：不生成页图片
    generate_page_images=False,
    generate_picture_images=False,
)

class DoclingConverter(BaseConverter):
    name = "docling"

    def convert(self, pdf_bytes: bytes, filename: str="document.pdf") -> ConvertResult:
        buf = BytesIO(pdf_bytes)
        source = DocumentStream(stream=buf, name=filename)

        converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                }
        )
        result = converter.convert(source)

        markdown = result.document.export_to_markdown()
        page_count = len(result.document.pages) if result.document.pages else 0

        logger.debug(
            "DoclingConverter: %d pages → %d chars",
            page_count,
            len(markdown),
        )
        return ConvertResult(
            content=markdown,
            page_count=page_count,
        )