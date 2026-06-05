"""PDF → Markdown 转换任务。

职责：
- 校验 message 中的 PDF 路径
- 下载 PDF（MinIO 路径）
- PDF → Markdown 转换（基于 Docling）
- 上传 Markdown 到 MinIO
- 返回 MinIO 相对路径

转换器：app.etl.tasks.converters.docling.DoclingConverter
    通过 get_converter() 获取，支持注册其他后端（如 marker、pymupdf）

对外接口：
    task.execute(message=message)
    → TaskResult(data="patent/google_patent/xxx.md", data_type="minio_path")
"""

from __future__ import annotations

import logging
from typing import Any

from app.etl.tasks import register_task
from app.etl.tasks.base import BaseTask, TaskResult
from app.etl.tasks.converters import get_converter
from app.storage.minio_client import MinioClient, get_minio_client

logger = logging.getLogger(__name__)


class PdfToMarkdownTask(BaseTask):
    name = "pdf_to_markdown"

    async def execute(self, *, message: dict[str, Any]) -> TaskResult:
        pdf_url = message.get("original_file")
        if not pdf_url:
            logger.info("PdfToMarkdownTask: no original_file — skipped")
            return TaskResult(data="", data_type="skipped")

        publication_number = message.get("publication_number", "")
        data_source = message.get("data_source", "")
        data_type = message.get("data_type", "patent")

        minio = get_minio_client()
        await minio._ensure_connection()

        pdf_bytes = await self._download(pdf_url, minio)
        if not pdf_bytes:
            logger.warning("PdfToMarkdownTask: download/empty for %s — %s", publication_number, pdf_url)
            return TaskResult(data="", data_type="error")

        logger.info("PdfToMarkdownTask: downloaded %d bytes for %s", len(pdf_bytes), publication_number)

        markdown = self._convert(pdf_bytes)
        if not markdown:
            logger.warning("PdfToMarkdownTask: empty markdown for %s", publication_number)
            return TaskResult(data="", data_type="empty")

        logger.info("PdfToMarkdownTask: extracted %d chars for %s", len(markdown), publication_number)

        try:
            md_bytes = markdown.encode("utf-8")
            md_key = pdf_url.rsplit(".", 1)[0] + ".md"
            minio_key = await minio.upload_bytes_to_key(
                data=md_bytes,
                object_key=md_key,
                content_type="text/markdown",
            )
            if minio_key:
                logger.info("PdfToMarkdownTask: uploaded → %s (%d chars)", minio_key, len(markdown))
                return TaskResult(data=minio_key, data_type="minio_path")
        except Exception:
            logger.exception("PdfToMarkdownTask: MinIO upload failed, fallback to text")

        return TaskResult(data=markdown, data_type="text")

    @staticmethod
    async def _download(pdf_url: str, minio: MinioClient) -> bytes | None:
        return await minio.get_object_bytes(pdf_url)

    @staticmethod
    def _convert(pdf_bytes: bytes) -> str:
        converter = get_converter()
        result = converter.convert(pdf_bytes)
        return result.content 


register_task(PdfToMarkdownTask)