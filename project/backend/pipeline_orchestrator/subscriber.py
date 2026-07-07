from __future__ import annotations

import asyncio
import json
import uuid

from common_sdk.config import config_manager
from common_sdk.logger import get_logger

from .config import REDIS_HOT_SCORE_CHANNEL, SCORE_THRESHOLD

logger = get_logger(__name__)


class HotScoreSubscriber:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._listen())
        logger.info("hot_score_subscriber_started", channel=REDIS_HOT_SCORE_CHANNEL)

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("hot_score_subscriber_stopped")

    async def _listen(self):
        import redis.asyncio as aioredis
        r = aioredis.Redis(
            host=config_manager.get("REDIS_HOST", "localhost"),
            port=int(config_manager.get("REDIS_PORT", "6379")),
            password=config_manager.get("REDIS_PASSWORD", ""),
            db=int(config_manager.get("REDIS_DB", "0")),
            decode_responses=True,
        )
        pubsub = r.pubsub()
        await pubsub.subscribe(REDIS_HOT_SCORE_CHANNEL)
        try:
            while self._running:
                try:
                    msg = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=5.0,
                    )
                    if msg and msg["type"] == "message":
                        await self._handle_message(msg["data"])
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning("subscriber_error", error=str(e))
                    await asyncio.sleep(1)
        finally:
            await pubsub.unsubscribe(REDIS_HOT_SCORE_CHANNEL)
            try:
                await r.aclose()
            except AttributeError:
                await r.close()

    async def _handle_message(self, raw: str) -> None:
        try:
            event = json.loads(raw)
            product_id = event.get("product_id")
            score = event.get("score", 0)
            tenant_id = event.get("tenant_id", "default")

            if score < SCORE_THRESHOLD:
                logger.info(
                    "score_below_threshold",
                    product_id=product_id, score=score, threshold=SCORE_THRESHOLD,
                )
                return

            logger.info("hot_product_detected", product_id=product_id, score=score)

            from mq_clients.celery_app import get_celery_app
            task_id = f"pipe_{uuid.uuid4().hex[:12]}"
            app = get_celery_app()
            app.send_task(
                "pipeline_orchestrator.tasks.run_pipeline_task",
                args=[task_id, product_id, tenant_id],
                queue="orchestrator_queue",
            )
            logger.info("pipeline_triggered", task_id=task_id, product_id=product_id)

        except Exception as e:
            logger.error("handle_message_failed", error=str(e), raw=raw[:200])


hot_score_subscriber = HotScoreSubscriber()
