from __future__ import annotations

import json
import os
import logging
import uuid
from typing import Any, AsyncGenerator, Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class RedisClient:
    _instance: Optional["RedisClient"] = None

    def __init__(self) -> None:
        self._conn: Optional[aioredis.Redis] = None

    @classmethod
    def get_client(cls) -> "RedisClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def connect(
        self,
        host: str | None = None,
        port: int | None = None,
        password: str | None = None,
        db: int | None = None,
    ) -> "RedisClient":
        if self._conn is not None:
            return self

        password = password or os.getenv("REDIS_PASSWORD", "dev_redis_2024")
        connection_kwargs: dict[str, Any] = {
            "host": host or os.getenv("REDIS_HOST", "localhost"),
            "port": port or int(os.getenv("REDIS_PORT", "6379")),
            "db": db if db is not None else int(os.getenv("REDIS_DB", "0")),
            "decode_responses": True,
        }
        if password:
            connection_kwargs["password"] = password

        self._conn = aioredis.from_url(
            f"redis://{connection_kwargs['host']}:{connection_kwargs['port']}/{connection_kwargs['db']}",
            password=password or None,
            decode_responses=True,
            protocol=2,
        )
        await self._conn.ping()
        logger.info("Redis connected host=%s port=%s db=%s", connection_kwargs["host"], connection_kwargs["port"], connection_kwargs["db"])
        return self

    def _ensure_conn(self) -> aioredis.Redis:
        if self._conn is None:
            raise RuntimeError("Redis not connected, call connect() first")
        return self._conn

    async def ping(self) -> bool:
        try:
            if self._conn is None:
                return False
            return await self._conn.ping()
        except Exception:
            logger.exception("Redis ping failed")
            return False

    # --- Key operations ---
    async def get(self, key: str) -> str | None:
        return await self._ensure_conn().get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        return await self._ensure_conn().set(key, value, ex=ex)

    async def delete(self, *keys: str) -> int:
        return await self._ensure_conn().delete(*keys)

    async def exists(self, *keys: str) -> int:
        return await self._ensure_conn().exists(*keys)

    async def expire(self, key: str, seconds: int) -> bool:
        return await self._ensure_conn().expire(key, seconds)

    async def ttl(self, key: str) -> int:
        return await self._ensure_conn().ttl(key)

    # --- Hash operations ---
    async def hget(self, name: str, key: str) -> str | None:
        return await self._ensure_conn().hget(name, key)

    async def hset(self, name: str, key: str, value: str) -> int:
        return await self._ensure_conn().hset(name, key, value)

    async def hgetall(self, name: str) -> dict[str, str]:
        return await self._ensure_conn().hgetall(name)

    async def hdel(self, name: str, *keys: str) -> int:
        return await self._ensure_conn().hdel(name, *keys)

    async def hincrby(self, name: str, key: str, amount: int = 1) -> int:
        return await self._ensure_conn().hincrby(name, key, amount)

    # --- List operations ---
    async def lpush(self, name: str, *values: str) -> int:
        return await self._ensure_conn().lpush(name, *values)

    async def rpush(self, name: str, *values: str) -> int:
        return await self._ensure_conn().rpush(name, *values)

    async def lpop(self, name: str) -> str | None:
        return await self._ensure_conn().lpop(name)

    async def rpop(self, name: str) -> str | None:
        return await self._ensure_conn().rpop(name)

    async def lrange(self, name: str, start: int, end: int) -> list[str]:
        return await self._ensure_conn().lrange(name, start, end)

    async def llen(self, name: str) -> int:
        return await self._ensure_conn().llen(name)

    # --- Set operations ---
    async def sadd(self, name: str, *values: str) -> int:
        return await self._ensure_conn().sadd(name, *values)

    async def srem(self, name: str, *values: str) -> int:
        return await self._ensure_conn().srem(name, *values)

    async def smembers(self, name: str) -> set[str]:
        return await self._ensure_conn().smembers(name)

    async def sismember(self, name: str, value: str) -> bool:
        return await self._ensure_conn().sismember(name, value)

    # --- Sorted set operations ---
    async def zadd(self, name: str, mapping: dict[str, float]) -> int:
        return await self._ensure_conn().zadd(name, mapping)

    async def zrange(self, name: str, start: int, end: int, desc: bool = False, withscores: bool = False) -> list:
        return await self._ensure_conn().zrange(name, start, end, desc=desc, withscores=withscores)

    async def zrem(self, name: str, *values: str) -> int:
        return await self._ensure_conn().zrem(name, *values)

    async def zcard(self, name: str) -> int:
        return await self._ensure_conn().zcard(name)

    async def zscore(self, name: str, value: str) -> float | None:
        return await self._ensure_conn().zscore(name, value)

    # --- Pub/Sub ---
    async def publish(self, channel: str, message: str) -> int:
        return await self._ensure_conn().publish(channel, message)

    async def subscribe(self, channel: str) -> AsyncGenerator[str, None]:
        conn = self._ensure_conn()
        pubsub = conn.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for msg in pubsub.listen():
                if msg["type"] == "message":
                    yield msg["data"]
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    # --- Lock ---
    async def acquire_lock(self, key: str, timeout_seconds: int) -> str | None:
        token = uuid.uuid4().hex
        acquired = await self._ensure_conn().set(
            f"lock:{key}", token, nx=True, ex=timeout_seconds
        )
        return token if acquired else None

    async def release_lock(self, key: str, token: str) -> bool:
        script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        else
            return 0
        end
        """
        result = await self._ensure_conn().eval(script, 1, f"lock:{key}", token)
        return bool(result)

    # --- Task progress ---
    async def set_task_progress(self, task_id: str, status: str, progress: int, result: Any = None) -> None:
        key = f"task:{task_id}"
        data = json.dumps({"status": status, "progress": progress, "result": result})
        await self._ensure_conn().set(key, data, ex=86400)

    async def get_task_progress(self, task_id: str) -> dict | None:
        key = f"task:{task_id}"
        raw = await self._ensure_conn().get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            RedisClient._instance = None
            logger.info("Redis connection closed")


get_redis_client = RedisClient.get_client
