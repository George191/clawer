"""采集资源存储 — 按数据类型和模板组织对象路径。"""

from __future__ import annotations

from pathlib import Path

from app.storage.minio_client import MinioClient


class AssetStore(MinioClient):
    def _build_object_key(
        self,
        template_name: str,
        data_type: str,
        filename: str,
    ) -> str:
        return f"{data_type}/{template_name}/{filename}"

    async def upload_file(
        self,
        file_path: Path,
        template_name: str,
        data_type: str,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> str:
        filename = file_path.name if filename is None else filename
        return await self.upload_file_to_key(
            file_path,
            self._build_object_key(template_name, data_type, filename),
            content_type or self._guess_content_type(filename),
        )

    async def upload_bytes(
        self,
        data: bytes,
        template_name: str,
        data_type: str,
        filename: str,
        content_type: str | None = None,
    ) -> str:
        return await self.upload_bytes_to_key(
            data,
            self._build_object_key(template_name, data_type, filename),
            content_type or self._guess_content_type(filename),
        )

    async def file_exists(
        self,
        template_name: str,
        data_type: str,
        filename: str,
    ) -> bool:
        return await self.object_exists(self._build_object_key(template_name, data_type, filename))


_asset_store: AssetStore | None = None


def get_asset_store() -> AssetStore:
    global _asset_store
    if _asset_store is None:
        _asset_store = AssetStore()
    return _asset_store
