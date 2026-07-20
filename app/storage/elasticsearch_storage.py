"""Elasticsearch 存储后端 — 文档持久化与批量写入。

功能
----
- 文档 CRUD：单条/批量写入（upsert），按 doc_id 查询
- 索引管理：自动创建索引，按模板名分索引
- 批量同步：bulk API 批量写入，支持幂等 upsert
- 统计查询：各索引文档数量 / 存储大小
"""

from __future__ import annotations

from typing import Any

from app.logger import get_logger

logger = get_logger(__name__)


class ElasticsearchStorage:
    def __init__(
        self,
        hosts: str | list[str] = "http://localhost:9200",
        username: str = "",
        password: str = "",
        index_prefix: str = "spider_",
        batch_size: int = 200,
    ) -> None:
        self._hosts = hosts
        self._username = username
        self._password = password
        self._index_prefix = index_prefix
        self._batch_size = batch_size
        self._client = None
        self._bulk_actions: list[dict[str, Any]] = []

    async def connect(self) -> None:
        if self._client is not None:
            return
        try:
            from elasticsearch import Elasticsearch
        except ImportError as e:
            raise ImportError(
                "elasticsearch package is not installed. "
                "Run: pip install elasticsearch"
            ) from e

        kwargs: dict[str, Any] = {
            "hosts": self._hosts,
            "request_timeout": 30,
        }
        if self._username and self._password:
            kwargs["http_auth"] = (self._username, self._password)

        self._client = Elasticsearch(**kwargs)
        logger.info("Connected to Elasticsearch: %s", self._hosts)

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            logger.info("Elasticsearch connection closed")

    def _get_index_name(self, template_name: str) -> str:
        safe_name = "".join(
            c if c.isalnum() or c in "-_" else "_"
            for c in template_name
        ).lower()
        return f"{self._index_prefix}{safe_name}"

    async def ensure_index(self, template_name: str) -> str:
        await self.connect()
        index = self._get_index_name(template_name)
        if not self._client.indices.exists(index=index):
            body = {
                "settings": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    "refresh_interval": "30s",
                },
                "mappings": {
                    "dynamic": "true",
                    "date_detection": True,
                    "numeric_detection": True,
                },
            }
            self._client.indices.create(index=index, body=body)
            logger.info("Created Elasticsearch index: %s", index)
        return index

    async def upsert_doc(
        self,
        index: str,
        doc_id: str,
        doc: dict[str, Any],
    ) -> None:
        await self.connect()
        self._client.update(
            index=index,
            id=doc_id,
            body={
                "doc": doc,
                "doc_as_upsert": True,
            },
        )

    async def add_to_bulk(
        self,
        index: str,
        doc_id: str,
        doc: dict[str, Any],
    ) -> None:
        self._bulk_actions.append(
            {
                "_op_type": "update",
                "_index": index,
                "_id": doc_id,
                "doc": doc,
                "doc_as_upsert": True,
            }
        )
        if len(self._bulk_actions) >= self._batch_size:
            await self.flush_bulk()

    async def flush_bulk(self) -> int:
        if not self._bulk_actions:
            return 0
        await self.connect()

        try:
            from elasticsearch import helpers
        except ImportError as e:
            raise ImportError(
                "elasticsearch helpers not available"
            ) from e

        success, errors = helpers.bulk(
            self._client,
            self._bulk_actions,
            raise_on_error=False,
            stats_only=False,
        )
        count = len(self._bulk_actions)
        self._bulk_actions.clear()

        if errors:
            logger.warning(
                "Elasticsearch bulk: %d success, %d errors (first 3: %s)",
                success, len(errors), errors[:3],
            )
        else:
            logger.info("Elasticsearch bulk: %d docs flushed", count)

        return success

    async def get_stats(self) -> list[dict[str, Any]]:
        await self.connect()
        result = self._client.cat.indices(
            index=f"{self._index_prefix}*",
            format="json",
            h="index,docs.count,store.size",
        )
        stats = []
        for row in result:
            stats.append({
                "name": row.get("index", ""),
                "total": int(row.get("docs.count", "0") or 0),
                "size": row.get("store.size", ""),
            })
        return stats
