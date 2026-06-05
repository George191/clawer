"""MinIO 对象存储客户端 — 管理文件上传、下载和 Content-Type 检测。

支持：
- 延迟连接：首次操作时自动建立连接
- 流式上传：upload_bytes 接收内存数据直接上传，无需落盘
- 自动 Content-Type 推断：根据文件扩展名设置 MIME 类型
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)


class MinioClient:
    def __init__(self) -> None:
        self._client = None
        self._bucket = settings.minio_bucket

    async def _ensure_connection(self) -> None:
        if self._client is not None:
            return
        try:
            from minio import Minio

            self._client = Minio(
                endpoint=settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
            )
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                logger.info("Created MinIO bucket: %s", self._bucket)
            logger.info("Connected to MinIO: %s", settings.minio_endpoint)
        except Exception as e:
            logger.error("Failed to connect to MinIO: %s", e)
            raise

    def _build_object_key(
        self,
        template_name: str,
        data_type: str,
        filename: str,
    ) -> str:
        return f"{data_type}/{template_name}/{filename}"

    def _build_file_url(self, object_key: str) -> str:
        scheme = "https" if settings.minio_secure else "http"
        return f"{scheme}://{settings.minio_endpoint}/{self._bucket}/{object_key}"

    async def upload_file(
        self,
        file_path: Path,
        template_name: str,
        data_type: str,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        await self._ensure_connection()

        if filename is None:
            filename = file_path.name

        object_key = self._build_object_key(template_name, data_type, filename)

        if not content_type:
            content_type = self._guess_content_type(filename)

        file_size = file_path.stat().st_size

        self._client.fput_object(
            bucket_name=self._bucket,
            object_name=object_key,
            file_path=str(file_path),
            content_type=content_type,
        )

        logger.info(
            "Uploaded file to MinIO: %s (%d bytes, %s)",
            object_key,
            file_size,
            content_type,
        )
        return object_key

    async def upload_bytes(
        self,
        data: bytes,
        template_name: str,
        data_type: str,
        filename: str,
        content_type: str | None = None,
    ) -> str:
        await self._ensure_connection()

        object_key = self._build_object_key(template_name, data_type, filename)

        if not content_type:
            content_type = self._guess_content_type(filename)

        data_stream = BytesIO(data)
        self._client.put_object(
            bucket_name=self._bucket,
            object_name=object_key,
            data=data_stream,
            length=len(data),
            content_type=content_type,
        )

        logger.info("Uploaded bytes to MinIO: %s (%d bytes)", object_key, len(data))
        return object_key

    async def upload_bytes_to_key(
        self,
        data: bytes,
        object_key: str,
        content_type: str | None = None,
    ) -> str:
        await self._ensure_connection()

        data_stream = BytesIO(data)
        self._client.put_object(
            bucket_name=self._bucket,
            object_name=object_key,
            data=data_stream,
            length=len(data),
            content_type=content_type or "application/octet-stream",
        )

        logger.info("Uploaded bytes to MinIO: %s (%d bytes)", object_key, len(data))
        return object_key

    async def get_object_bytes(self, object_key: str) -> bytes | None:
        await self._ensure_connection()
        try:
            response = self._client.get_object(self._bucket, object_key)
            data = response.read()
            response.close()
            response.release_conn()
            return data
        except Exception as e:
            logger.warning("MinIO get_object failed for %s: %s", object_key, e)
            return None

    async def file_exists(
        self,
        template_name: str,
        data_type: str,
        filename: str,
    ) -> bool:
        await self._ensure_connection()
        object_key = self._build_object_key(template_name, data_type, filename)
        try:
            self._client.stat_object(self._bucket, object_key)
            return True
        except Exception:
            return False

    async def close(self) -> None:
        pass

    @staticmethod
    def _guess_content_type(filename: str) -> str:
        ext = Path(filename).suffix.lower()
        content_types = {
            ".pdf": "application/pdf",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xls": "application/vnd.ms-excel",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".txt": "text/plain",
            ".html": "text/html",
            ".htm": "text/html",
            ".xml": "application/xml",
            ".json": "application/json",
            ".zip": "application/zip",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".tiff": "image/tiff",
            ".tif": "image/tiff",
        }
        return content_types.get(ext, "application/octet-stream")


_minio_client: MinioClient | None = None


def get_minio_client() -> MinioClient:
    global _minio_client
    if _minio_client is None:
        _minio_client = MinioClient()
    return _minio_client
