import os
import logging
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)


class MongoDBClient:
    _instance: Optional["MongoDBClient"] = None

    def __init__(self) -> None:
        self._client: Optional[AsyncIOMotorClient] = None
        self._db: Optional[AsyncIOMotorDatabase] = None

    @classmethod
    def get_client(cls) -> "MongoDBClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def connect(
        self,
        host: str | None = None,
        port: int | None = None,
        database: str | None = None,
    ) -> "MongoDBClient":
        if self._client is not None:
            return self

        host = host or os.getenv("MONGODB_HOST", "localhost")
        port = port or int(os.getenv("MONGODB_PORT", "27017"))
        database = database or os.getenv("MONGODB_DATABASE", "prodvideo")

        self._client = AsyncIOMotorClient(host, port)
        self._db = self._client[database]
        await self._client.admin.command("ping")
        logger.info("MongoDB connected host=%s port=%s db=%s", host, port, database)
        return self

    def _ensure_db(self) -> AsyncIOMotorDatabase:
        if self._db is None:
            raise RuntimeError("MongoDB not connected, call connect() first")
        return self._db

    def get_collection(self, name: str) -> AsyncIOMotorCollection:
        return self._ensure_db()[name]

    async def ping(self) -> bool:
        try:
            if self._client is None:
                return False
            await self._client.admin.command("ping")
            return True
        except Exception:
            logger.exception("MongoDB ping failed")
            return False

    async def insert_one(self, collection: str, document: dict) -> str:
        col = self.get_collection(collection)
        result = await col.insert_one(document)
        return str(result.inserted_id)

    async def find_one(self, collection: str, query: dict, projection: dict | None = None) -> dict | None:
        col = self.get_collection(collection)
        return await col.find_one(query, projection)

    async def find_many(
        self,
        collection: str,
        query: dict,
        projection: dict | None = None,
        sort: list[tuple[str, int]] | None = None,
        skip: int = 0,
        limit: int = 0,
    ) -> list[dict]:
        col = self.get_collection(collection)
        cursor = col.find(query, projection)
        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        if limit:
            cursor = cursor.limit(limit)
        return await cursor.to_list(length=None)

    async def update_one(
        self, collection: str, query: dict, update: dict, upsert: bool = False
    ) -> int:
        col = self.get_collection(collection)
        result = await col.update_one(query, update, upsert=upsert)
        return result.modified_count

    async def delete_one(self, collection: str, query: dict) -> int:
        col = self.get_collection(collection)
        result = await col.delete_one(query)
        return result.deleted_count

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._db = None
            MongoDBClient._instance = None
            logger.info("MongoDB connection closed")


get_mongodb_client = MongoDBClient.get_client
