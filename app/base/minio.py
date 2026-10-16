"""兼容已发布适配器的采集资源存储入口。"""

from app.records.asset_store import AssetStore as MinioClient

__all__ = ["MinioClient"]
