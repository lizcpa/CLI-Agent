import os
import logging
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any, AsyncGenerator, Optional, Self

import aiomysql

logger = logging.getLogger(__name__)


class MySQLClient:
    _instance: Optional["MySQLClient"] = None

    def __init__(self) -> None:
        self._pool: Optional[aiomysql.Pool] = None

    @classmethod
    def get_client(cls) -> "MySQLClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def create_pool(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
    ) -> aiomysql.Pool:
        if self._pool is not None:
            return self._pool

        cfg = {
            "host": host or os.getenv("MYSQL_HOST", "localhost"),
            "port": port or int(os.getenv("MYSQL_PORT", "3306")),
            "user": user or os.getenv("MYSQL_USER", "dev_user"),
            "password": password or os.getenv("MYSQL_PASSWORD", "dev_pass_2024"),
            "db": database or os.getenv("MYSQL_DATABASE", "prodvideo"),
            "autocommit": True,
            "charset": "utf8mb4",
            "minsize": 2,
            "maxsize": 20,
            "pool_recycle": 3600,
        }
        self._pool = await aiomysql.create_pool(**cfg)
        logger.info("MySQL pool created host=%s port=%s db=%s", cfg["host"], cfg["port"], cfg["db"])
        return self._pool

    async def _ensure_pool(self) -> aiomysql.Pool:
        if self._pool is None:
            await self.create_pool()
        assert self._pool is not None
        return self._pool

    async def ping(self) -> bool:
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                    await cur.fetchone()
            return True
        except Exception:
            logger.exception("MySQL ping failed")
            return False

    async def execute(self, sql: str, params: Any = None) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                return cur.rowcount

    async def fetchone(self, sql: str, params: Any = None) -> dict | None:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                return await cur.fetchone()

    async def fetchall(self, sql: str, params: Any = None) -> list[dict]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                return await cur.fetchall()

    async def execute_many(self, sql: str, params_list: list[Any]) -> int:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(sql, params_list)
                return cur.rowcount

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[Self, None]:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.begin()
            try:
                yield self
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
            MySQLClient._instance = None
            logger.info("MySQL pool closed")


get_mysql_client = MySQLClient.get_client
