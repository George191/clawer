"""MongoDB connection, literal collection access, and generic index operations."""

from __future__ import annotations

from typing import Any

from app.config.settings import settings
from app.logger import get_logger

logger = get_logger(__name__)


class MongoClient:
    def __init__(self) -> None:
        self._client = None
        self._db = None
        self._initialized_indexes: set[tuple[str, str, bool]] = set()

    async def _ensure_connection(self) -> None:
        if self._db is not None:
            return
        try:
            from motor.motor_asyncio import AsyncIOMotorClient

            self._client = AsyncIOMotorClient(settings.db_url)
            self._db = self._client[settings.db_name]
            await self._client.admin.command("ping")
            logger.info("Connected to MongoDB: %s/%s", settings.db_url, settings.db_name)
        except Exception as e:
            logger.error("Failed to connect to MongoDB: %s", e)
            raise


    async def _get_collection(self, collection_name: str):
        await self._ensure_connection()
        return self._db[collection_name]

    async def _ensure_indexes(self, collection: Any, indexes: list[tuple[str, bool]]) -> None:
        for field, unique in indexes:
            key = (collection.name, field, unique)
            if key not in self._initialized_indexes:
                await collection.create_index(field, unique=unique)
                self._initialized_indexes.add(key)


    async def close(self) -> None:
        if self._client:
            self._client.close()
