"""基础组件层 — 统一对外暴露 MongoDB、MinIO、Kafka、HTTP 客户端。

各组件内部封装连接管理、错误重试等逻辑，供 crawler/downloader/syncer 各服务共用。
"""

from app.storage.mongo_storage import MongoStorage as MongoClient

__all__ = ["MongoClient"]