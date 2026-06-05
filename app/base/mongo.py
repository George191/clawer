"""MongoDB 基础组件 — 重新导出 MongoStorage 为 MongoClient。"""

from app.storage.mongo_storage import MongoStorage as MongoClient

__all__ = ["MongoClient"]