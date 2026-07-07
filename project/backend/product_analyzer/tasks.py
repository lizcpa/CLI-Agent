from __future__ import annotations

import asyncio
import json
import os

from utils.mq_clients.celery_app import create_task

from .config import (
    CELERY_ANALYZE_QUEUE,
    DEFAULT_SCORE_THRESHOLD,
    REDIS_HOT_SCORE_CHANNEL,
    REDIS_HOT_SORTED_SET,
    SERVICE_NAME,
)
from .scoring import calculate_product_score, determine_tier, get_score_breakdown

PRODUCT_ANALYZER_JWT = os.getenv("PRODUCT_ANALYZER_JWT", "")


def _sync_mysql_fetchall(sql: str, params: tuple | None = None) -> list[dict]:
    import aiomysql

    async def _run() -> list[dict]:
        pool = await aiomysql.create_pool(
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "dev_user"),
            password=os.getenv("MYSQL_PASSWORD", "dev_pass_2024"),
            db=os.getenv("MYSQL_DATABASE", "prodvideo"),
            autocommit=True,
            charset="utf8mb4",
            minsize=1,
            maxsize=5,
        )
        try:
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(sql, params)
                    rows = await cur.fetchall()
                    return list(rows) if rows else []
        finally:
            pool.close()
            await pool.wait_closed()

    return asyncio.run(_run())


def _sync_mysql_execute(sql: str, params: tuple | None = None) -> int:
    import aiomysql

    async def _run() -> int:
        pool = await aiomysql.create_pool(
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "dev_user"),
            password=os.getenv("MYSQL_PASSWORD", "dev_pass_2024"),
            db=os.getenv("MYSQL_DATABASE", "prodvideo"),
            autocommit=True,
            charset="utf8mb4",
            minsize=1,
            maxsize=5,
        )
        try:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, params)
                    return cur.rowcount
        finally:
            pool.close()
            await pool.wait_closed()

    return asyncio.run(_run())


def _sync_redis_publish(channel: str, message: str) -> int:
    import redis as sync_redis

    r = sync_redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", "dev_redis_2024"),
        decode_responses=True,
    )
    try:
        return r.publish(channel, message)
    finally:
        r.close()


def _sync_redis_zadd(key: str, mapping: dict[str, float]) -> int:
    import redis as sync_redis

    r = sync_redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", "dev_redis_2024"),
        decode_responses=True,
    )
    try:
        return r.zadd(key, mapping)
    finally:
        r.close()


def _sync_redis_set_progress(task_id: str, status: str, progress: int, result: dict | None = None) -> None:
    import redis as sync_redis

    r = sync_redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", "dev_redis_2024"),
        decode_responses=True,
    )
    try:
        key = f"task:{task_id}"
        data = json.dumps({"status": status, "progress": progress, "result": result})
        r.set(key, data, ex=86400)
    finally:
        r.close()


@create_task(name="product_analyzer.analyze_products", queue=CELERY_ANALYZE_QUEUE)
def analyze_products_task(
    self,
    task_id: str,
    product_ids: list[int] | None = None,
    platform: str | None = None,
    limit: int = 100,
    threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> dict:
    _sync_redis_set_progress(task_id, "running", 0)

    if product_ids and len(product_ids) > 0:
        placeholders = ",".join(["%s"] * len(product_ids))
        sql = f"SELECT * FROM products WHERE id IN ({placeholders}) AND status='active'"
        params = tuple(product_ids)
    elif platform:
        sql = "SELECT * FROM products WHERE platform=%s AND status='active' LIMIT %s"
        params = (platform, limit)
    else:
        sql = "SELECT * FROM products WHERE status='active' LIMIT %s"
        params = (limit,)

    products = _sync_mysql_fetchall(sql, params)
    total = len(products)
    if total == 0:
        _sync_redis_set_progress(task_id, "completed", 100, {"analyzed_count": 0, "hot_count": 0})
        return {"analyzed_count": 0, "hot_count": 0}

    hot_count = 0
    for idx, product in enumerate(products):
        score = calculate_product_score(product, threshold)
        tier = determine_tier(score, threshold)

        _sync_mysql_execute(
            "UPDATE products SET score=%s, tier=%s, updated_at=NOW() WHERE id=%s",
            (score, tier, product["id"]),
        )

        breakdown = get_score_breakdown(product)
        event = json.dumps({
            "product_id": product["id"],
            "title": product.get("title", ""),
            "platform": product.get("platform", ""),
            "score": score,
            "tier": tier,
            "dimensions": breakdown,
            "threshold": threshold,
        })

        if tier == "hot":
            hot_count += 1
            _sync_redis_publish(REDIS_HOT_SCORE_CHANNEL, event)
            _sync_redis_zadd(REDIS_HOT_SORTED_SET, {str(product["id"]): score})

        progress = int((idx + 1) / total * 100)
        if progress % 10 == 0:
            _sync_redis_set_progress(task_id, "running", progress)

    _sync_redis_set_progress(
        task_id,
        "completed",
        100,
        {"analyzed_count": total, "hot_count": hot_count},
    )
    return {"analyzed_count": total, "hot_count": hot_count}
