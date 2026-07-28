"""MinIO 对象存储客户端 — 管理文件上传、下载和 Content-Type 检测。

支持：
- 延迟连接：首次操作时自动建立连接
- 流式上传：upload_bytes 接收内存数据直接上传，无需落盘
- 自动 Content-Type 推断：根据文件扩展名设置 MIME 类型
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from io import BytesIO
from pathlib import Path

import urllib3

from app.config.settings import settings
from app.logger import get_logger

logger = get_logger(__name__)


class MinioClient:
    def __init__(self, bucket: str | None = None, public_read: bool = False) -> None:
        self._client = None
        self._bucket = bucket or settings.minio_bucket
        self._public_read = public_read
        self._connection_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=settings.minio_max_workers,
            thread_name_prefix="minio",
        )

    async def _run_sync(self, func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            partial(func, *args, **kwargs),
        )

    async def _ensure_connection(self) -> None:
        if self._client is not None:
            return
        async with self._connection_lock:
            if self._client is not None:
                return
            await self._run_sync(self._connect)

    def _connect(self) -> None:
        try:
            from minio import Minio

            self._client = Minio(
                endpoint=settings.minio_endpoint,
                access_key=settings.minio_access_key,
                secret_key=settings.minio_secret_key,
                secure=settings.minio_secure,
                http_client=urllib3.PoolManager(
                    timeout=urllib3.Timeout(connect=300, read=300),
                    maxsize=settings.minio_max_workers,
                    block=True,
                    retries=urllib3.Retry(
                        total=5,
                        backoff_factor=0.2,
                        status_forcelist=[500, 502, 503, 504],
                    ),
                ),
            )
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                logger.info("Created MinIO bucket: %s", self._bucket)
            if self._public_read:
                self._client.set_bucket_policy(
                    self._bucket,
                    json.dumps(
                        {
                            "Version": "2012-10-17",
                            "Statement": [
                                {
                                    "Effect": "Allow",
                                    "Principal": {"AWS": ["*"]},
                                    "Action": ["s3:GetObject"],
                                    "Resource": [f"arn:aws:s3:::{self._bucket}/*"],
                                }
                            ],
                        }
                    ),
                )
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

    def build_object_url(self, object_key: str) -> str:
        if not object_key or object_key.startswith(("http://", "https://")):
            return object_key
        endpoint = settings.minio_endpoint.rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            scheme = "https" if settings.minio_secure else "http"
            endpoint = f"{scheme}://{endpoint}"
        return f"{endpoint}/{self._bucket}/{object_key.lstrip('/')}"

    def _build_file_url(self, object_key: str) -> str:
        return self.build_object_url(object_key)

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

        await self._run_sync(
            self._client.fput_object,
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
        await self._run_sync(
            self._client.put_object,
            bucket_name=self._bucket,
            object_name=object_key,
            data=data_stream,
            length=len(data),
            content_type=content_type,
        )

        logger.debug("Uploaded bytes to MinIO: %s (%d bytes)", object_key, len(data))
        return object_key

    async def upload_bytes_to_key(
        self,
        data: bytes,
        object_key: str,
        content_type: str | None = None,
    ) -> str:
        await self._ensure_connection()

        data_stream = BytesIO(data)
        await self._run_sync(
            self._client.put_object,
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

        def read_object() -> bytes:
            response = self._client.get_object(self._bucket, object_key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        non_retryable_codes = {
            "AccessDenied",
            "InvalidAccessKeyId",
            "NoSuchBucket",
            "NoSuchKey",
            "NoSuchObject",
            "SignatureDoesNotMatch",
        }
        attempts = 3
        for attempt in range(attempts):
            try:
                return await self._run_sync(read_object)
            except Exception as e:
                error_code = str(getattr(e, "code", "") or "")
                if error_code in non_retryable_codes or attempt == attempts - 1:
                    logger.warning("MinIO get_object failed for %s: %s", object_key, e)
                    return None
                await asyncio.sleep(0.2 * (attempt + 1))
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
            await self._run_sync(self._client.stat_object, self._bucket, object_key)
            return True
        except Exception:
            return False

    async def remove_object(self, object_key: str) -> None:
        await self._ensure_connection()
        await self._run_sync(self._client.remove_object, self._bucket, object_key)
        logger.info("Removed MinIO object: %s", object_key)

    async def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._client = None

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


_business_metadata_minio_client: MinioClient | None = None


def get_business_metadata_minio_client() -> MinioClient:
    global _business_metadata_minio_client
    if _business_metadata_minio_client is None:
        _business_metadata_minio_client = MinioClient(settings.business_metadata_minio_bucket, public_read=True)
    return _business_metadata_minio_client
