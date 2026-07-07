import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import asyncio
import json

from db_clients.mysql import get_mysql_client
from mq_clients.celery_app import create_task, celery_app
from common_sdk.business_metrics import crawl_jobs_total, crawl_products_found
from common_sdk.config import config_manager
from common_sdk.logger import get_logger
from platform_connectors.models import CrawlRequest, PlatformAdapterConfig

from .connectors import build_crawler_registry
from .persistence import persist_products

logger = get_logger(__name__)


def _get_redis():
    import redis as sync_redis

    return sync_redis.Redis(
        host=config_manager.get("REDIS_HOST", "localhost"),
        port=int(config_manager.get("REDIS_PORT", "6379")),
        password=config_manager.get("REDIS_PASSWORD", ""),
        db=int(config_manager.get("REDIS_DB", "0")),
        decode_responses=True,
    )


async def _load_platform_config(platform: str) -> dict:
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT config_key, config_value FROM platform_config WHERE platform = %s",
        (platform,),
    )
    return {r["config_key"]: r["config_value"] for r in rows} if rows else {}


@create_task("execute_crawl_job", queue="crawl_queue")
def execute_crawl_job(self, task_id, platform, keyword, max_count, sort_by, tenant_id="default"):
    r = _get_redis()
    try:
        r.hset(f"task:{task_id}", mapping={"status": "running", "progress": 10})

        async def _run():
            cfg_dict = await _load_platform_config(platform)
            adapter_cfg = PlatformAdapterConfig(
                platform_id=platform,
                connector_class=cfg_dict.get("crawler_type", "playwright"),
                config=cfg_dict,
            )
            reg = build_crawler_registry({platform: adapter_cfg})
            crawler = reg.get_crawler(platform)
            if crawler is None:
                raise RuntimeError(f"no crawler registered for platform {platform}")
            request = CrawlRequest(
                keyword=keyword,
                max_count=max_count,
                sort_by=sort_by,
                platform_config=cfg_dict,
            )
            r.hset(f"task:{task_id}", mapping={"status": "running", "progress": 30})
            result = await crawler.run_crawl(request)
            r.hset(
                f"task:{task_id}",
                mapping={
                    "status": "running",
                    "progress": 70,
                    "products_found": str(result.total_found),
                },
            )
            persisted, product_ids = await persist_products(result.products, tenant_id)
            return result, persisted, product_ids

        result, persisted, product_ids = asyncio.run(_run())
        r.hset(
            f"task:{task_id}",
            mapping={
                "status": "completed",
                "progress": 100,
                "result": json.dumps(
                    [p.model_dump() for p in result.products],
                    ensure_ascii=False,
                    default=str,
                ),
                "products_found": str(result.total_found),
                "persisted": str(persisted),
            },
        )
        r.expire(f"task:{task_id}", 86400)
        crawl_jobs_total.labels(platform=platform, status="success").inc()
        crawl_products_found.labels(platform=platform).observe(result.total_found)

        if product_ids:
            analyze_task_id = f"auto-analyze-{task_id}"
            celery_app.send_task(
                "product_analyzer.analyze_products",
                kwargs={
                    "task_id": analyze_task_id,
                    "product_ids": product_ids,
                    "threshold": 70.0,
                },
                queue="analyze_queue",
            )
            logger.info("crawl_to_analyze_chained", product_ids=product_ids, analyze_task_id=analyze_task_id)

        return {
            "products_found": result.total_found,
            "persisted": persisted,
            "platform": platform,
            "keyword": keyword,
            "analyzed_product_ids": product_ids,
        }
    except Exception as e:
        crawl_jobs_total.labels(platform=platform, status="failed").inc()
        try:
            r.hset(
                f"task:{task_id}",
                mapping={"status": "failed", "error": str(e)[:500]},
            )
        except Exception:
            pass
        raise
