from __future__ import annotations

import os
import time
import uuid
import logging
from datetime import datetime, timedelta
from typing import Any, Callable

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import redis
import redis.connection
import redis.utils
if redis.utils.DEFAULT_RESP_VERSION == 3:
    redis.utils.DEFAULT_RESP_VERSION = 2
    redis.connection.DEFAULT_RESP_VERSION = 2

from celery import Celery, Task, chain, group, chord
from celery.schedules import crontab, schedule
from kombu import Queue


logger = logging.getLogger(__name__)

DEFAULT_BROKER = "amqp://guest:guest@localhost:5672//"
DEFAULT_BACKEND = "redis://localhost:6379/0"

TASK_QUEUE_NAMES = [
    "crawl_queue",
    "analyze_queue",
    "ai_queue",
    "compose_queue",
    "publish_queue",
    "orchestrator_queue",
]

TASK_QUEUES = [Queue(name=name) for name in TASK_QUEUE_NAMES]

TASK_MODULES = [
    "project.backend.crawl_scheduler.tasks",
    "project.backend.product_analyzer.tasks",
    "project.backend.pipeline_orchestrator.tasks",
    "project.backend.video_composer.tasks",
    "project.backend.publish_dispatcher.tasks",
    "project.backend.asset_manager.tasks",
]


def _get_redis_client() -> redis.Redis:
    backend = os.getenv("CELERY_RESULT_BACKEND", DEFAULT_BACKEND)
    url = backend.replace("redis://", "")
    parts = url.split("/")
    db = int(parts[-1]) if parts[-1].isdigit() else 0
    host_port = parts[0]
    if ":" in host_port:
        host, port = host_port.rsplit(":", 1)
        port = int(port)
    else:
        host = host_port
        port = 6379
    return redis.Redis(host=host, port=port, db=db, decode_responses=True)


class BaseTask(Task):
    abstract = True
    _redis = None

    @property
    def redis_client(self) -> redis.Redis:
        if self._redis is None:
            self._redis = _get_redis_client()
        return self._redis

    def on_failure(self, exc, task_id, args, kwargs, einfo) -> None:
        try:
            self.redis_client.hset(
                f"task:{task_id}",
                mapping={
                    "status": "failed",
                    "error": str(exc),
                    "progress_percent": "0",
                },
            )
        except Exception:
            pass
        super().on_failure(exc, task_id, args, kwargs, einfo)

    def on_success(self, retval, task_id, args, kwargs) -> None:
        try:
            self.redis_client.hset(
                f"task:{task_id}",
                mapping={"status": "completed", "progress_percent": "100"},
            )
        except Exception:
            pass
        super().on_success(retval, task_id, args, kwargs)


_celery_app: Celery | None = None


def _build_routes() -> dict[str, dict[str, str]]:
    return {q: {"queue": q} for q in TASK_QUEUE_NAMES}


def _compute_next_run(cron_expression: str | None) -> datetime:
    """Compute next_run_at from a simple cron expression. Defaults to 1 hour."""
    if not cron_expression:
        return datetime.utcnow() + timedelta(hours=1)
    parts = cron_expression.strip().split()
    if len(parts) != 5:
        return datetime.utcnow() + timedelta(hours=1)
    minute, hour, day, month, weekday = parts
    if minute.startswith("*/"):
        try:
            n = int(minute[2:])
            return datetime.utcnow() + timedelta(minutes=n)
        except ValueError:
            pass
    if minute == "0" and hour.startswith("*/"):
        try:
            n = int(hour[2:])
            return datetime.utcnow() + timedelta(hours=n)
        except ValueError:
            pass
    if minute == "0" and hour == "0":
        return datetime.utcnow() + timedelta(days=1)
    return datetime.utcnow() + timedelta(hours=1)


def create_celery_app(
    name: str = "prodvideo",
    broker_url: str | None = None,
    backend_url: str | None = None,
) -> Celery:
    broker = broker_url or os.getenv("CELERY_BROKER_URL", DEFAULT_BROKER)
    backend = backend_url or os.getenv("CELERY_RESULT_BACKEND", DEFAULT_BACKEND)

    app = Celery(name, broker=broker, backend=backend, task_cls=BaseTask, include=TASK_MODULES)

    beat_schedule = {
        "crawl_plan_scheduler": {
            "task": "crawl_plan_scheduler",
            "schedule": schedule(run_every=60),
            "options": {"queue": "crawl_queue"},
        },
        "periodic_reanalysis": {
            "task": "periodic_reanalysis",
            "schedule": crontab(minute="*/30"),
            "options": {"queue": "analyze_queue"},
        },
    }

    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_track_started=True,
        result_expires=86400,
        worker_prefetch_multiplier=1,
        task_default_queue="crawl_queue",
        task_queues=TASK_QUEUES,
        task_routes=_build_routes(),
        beat_schedule=beat_schedule,
        beat_max_loop_interval=60,
    )

    _register_scheduler_task(app)
    return app


def _register_scheduler_task(app: Celery) -> None:
    @app.task(name="crawl_plan_scheduler", bind=True, queue="crawl_queue", base=BaseTask)
    def _crawl_plan_scheduler(self):
        import asyncio
        import aiomysql

        async def _run():
            pool = await aiomysql.create_pool(
                host=os.getenv("MYSQL_HOST", "localhost"),
                port=int(os.getenv("MYSQL_PORT", "3306")),
                user=os.getenv("MYSQL_USER", "root"),
                password=os.getenv("MYSQL_PASSWORD", "root"),
                db=os.getenv("MYSQL_DATABASE", "prodvideo"),
                autocommit=True,
                charset="utf8mb4",
                minsize=1,
                maxsize=3,
            )
            try:
                async with pool.acquire() as conn:
                    async with conn.cursor(aiomysql.DictCursor) as cur:
                        await cur.execute(
                            "SELECT id, tenant_id, platform, keyword, max_count, sort_by, cron_expression "
                            "FROM crawl_plans WHERE enabled=1 "
                            "AND (next_run_at IS NULL OR next_run_at <= UTC_TIMESTAMP())"
                        )
                        plans = await cur.fetchall()
            finally:
                pool.close()
                await pool.wait_closed()
            return plans

        plans = asyncio.run(_run())
        triggered = 0
        for plan in plans:
            job_id = f"auto-crawl-{plan['id']}-{int(time.time())}"
            app.send_task(
                "execute_crawl_job",
                kwargs={
                    "task_id": job_id,
                    "platform": plan["platform"],
                    "keyword": plan["keyword"],
                    "max_count": plan.get("max_count", 100),
                    "sort_by": plan.get("sort_by", "sales"),
                    "tenant_id": plan.get("tenant_id", "default"),
                },
                queue="crawl_queue",
            )
            next_run = _compute_next_run(plan.get("cron_expression"))

            async def _update(pid, nr):
                pool = await aiomysql.create_pool(
                    host=os.getenv("MYSQL_HOST", "localhost"),
                    port=int(os.getenv("MYSQL_PORT", "3306")),
                    user=os.getenv("MYSQL_USER", "root"),
                    password=os.getenv("MYSQL_PASSWORD", "root"),
                    db=os.getenv("MYSQL_DATABASE", "prodvideo"),
                    autocommit=True,
                    charset="utf8mb4",
                    minsize=1,
                    maxsize=1,
                )
                try:
                    async with pool.acquire() as conn:
                        async with conn.cursor() as cur:
                            await cur.execute(
                                "UPDATE crawl_plans SET last_run_at=UTC_TIMESTAMP(), next_run_at=%s WHERE id=%s",
                                (nr, pid),
                            )
                finally:
                    pool.close()
                    await pool.wait_closed()

            asyncio.run(_update(plan["id"], next_run))
            triggered += 1
            logger.info("crawl_plan_triggered plan_id=%s platform=%s keyword=%s next_run_at=%s",
                        plan["id"], plan["platform"], plan["keyword"], next_run.isoformat())

        return {"plans_triggered": triggered, "plans_checked": len(plans)}

    @app.task(name="periodic_reanalysis", bind=True, queue="analyze_queue", base=BaseTask)
    def _periodic_reanalysis(self):
        task_id = f"periodic-reanalysis-{int(time.time())}"
        app.send_task(
            "product_analyzer.analyze_products",
            kwargs={
                "task_id": task_id,
                "product_ids": None,
                "limit": 100,
                "threshold": 70.0,
            },
            queue="analyze_queue",
        )
        return {"triggered_task_id": task_id}


def get_celery_app() -> Celery:
    global _celery_app
    if _celery_app is None:
        _celery_app = create_celery_app()
    return _celery_app


celery_app = get_celery_app()


def create_task(
    name: str | None = None,
    queue: str = "crawl_queue",
    acks_late: bool = True,
    max_retries: int = 3,
) -> Callable:
    def decorator(func: Callable) -> Callable:
        app = get_celery_app()
        task = app.task(
            bind=True,
            name=name or func.__name__,
            queue=queue,
            acks_late=acks_late,
            max_retries=max_retries,
            base=BaseTask,
        )(func)
        return task

    return decorator


def create_periodic_task(
    name: str,
    schedule_def: schedule | crontab | int,
    queue: str = "crawl_queue",
) -> Callable:
    def decorator(func: Callable) -> Callable:
        app = get_celery_app()
        task = app.task(
            bind=True,
            name=name,
            queue=queue,
            base=BaseTask,
        )(func)

        schedule_entry: dict[str, Any] = {
            "task": name,
            "schedule": schedule_def,
            "options": {"queue": queue},
        }
        if "beat_schedule" not in app.conf:
            app.conf.beat_schedule = {}
        app.conf.beat_schedule[name] = schedule_entry

        return task

    return decorator


def chain_tasks(*tasks: Callable) -> chain:
    return chain(*tasks)


def group_tasks(*tasks: Callable) -> group:
    return group(*tasks)


def chord_tasks(header_tasks: list, callback_task: Callable) -> chord:
    return chord(header_tasks)(callback_task)
