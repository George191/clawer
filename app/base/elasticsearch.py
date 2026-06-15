"""Elasticsearch 基础组件 — 重新导出 ElasticsearchStorage。"""

from app.storage.elasticsearch_storage import ElasticsearchStorage as ElasticsearchClient

__all__ = ["ElasticsearchClient"]
