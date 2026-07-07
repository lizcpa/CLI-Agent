import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import asyncio
import json

from mq_clients.celery_app import create_task, BaseTask
from common_sdk.business_metrics import publish_jobs_total
from common_sdk.content_safety import content_safety_client
from common_sdk.exceptions import ContentFilteredException
from common_sdk.logger import get_logger
from db_clients.mysql import get_mysql_client
from platform_connectors import PublishRequest, PublishContent

from .worker_publishers import worker_publishers
from .config import ASSET_MANAGER_URL, JWT_SECRET, SERVICE_NAME

logger = get_logger(__name__)


def _set_status(task: BaseTask, task_id: str, **fields) -> None:
    task.redis_client.hset(f"task:{task_id}", mapping=fields)
    task.redis_client.expire(f"task:{task_id}", 86400)


def _insert_publish_log(
    pipeline_id, platform, result, tenant_id, title, description, tags,
):
    try:
        mysql = get_mysql_client()

        async def _do():
            await mysql.execute(
                "INSERT INTO publish_log (tenant_id, pipeline_id, platform, "
                "platform_post_id, public_url, status, publish_content) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    tenant_id, pipeline_id, platform,
                    getattr(result, "platform_post_id", None),
                    getattr(result, "public_url", None),
                    getattr(result, "status", "published"),
                    json.dumps(
                        {"title": title, "description": description, "tags": tags},
                        ensure_ascii=False,
                    ),
                ),
            )
            row = await mysql.fetchone("SELECT LAST_INSERT_ID() AS id")
            return row["id"] if row else None

        return asyncio.run(_do())
    except Exception as e:
        logger.warning("insert_publish_log_failed", error=str(e))
        return None


def _insert_failed_publish_log(pipeline_id, platform, tenant_id, error_msg):
    try:
        mysql = get_mysql_client()

        async def _do():
            await mysql.execute(
                "INSERT INTO publish_log (tenant_id, pipeline_id, platform, status, "
                "error_message) VALUES (%s, %s, %s, 'failed', %s)",
                (tenant_id, pipeline_id, platform, str(error_msg)[:500]),
            )

        asyncio.run(_do())
    except Exception as e:
        logger.warning("insert_failed_publish_log_failed", error=str(e))


def _adapt_video(video_url, platform, tenant_id):
    """Call asset-manager /adapt (fail-soft). Adapt is async; we only dispatch here."""
    try:
        import httpx
        from common_sdk.auth import create_service_jwt

        token = create_service_jwt(SERVICE_NAME, JWT_SECRET)

        async def _do():
            async with httpx.AsyncClient(timeout=300) as c:
                resp = await c.post(
                    f"{ASSET_MANAGER_URL}/api/v1/assets/adapt",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-Tenant-ID": tenant_id,
                    },
                    json={"video_url": video_url, "platforms": [platform]},
                )
                resp.raise_for_status()
                return resp.json()

        return asyncio.run(_do())
    except Exception as e:
        logger.warning("adapt_video_failed", error=str(e))
        return None


def _update_pipeline_publish(pipeline_id, log_id):
    try:
        mysql = get_mysql_client()

        async def _do():
            await mysql.execute(
                "UPDATE generation_pipelines SET publish_log_id=%s, "
                "publish_status='completed', updated_at=NOW() WHERE id=%s",
                (log_id, pipeline_id),
            )

        asyncio.run(_do())
    except Exception as e:
        logger.warning("update_pipeline_publish_failed", error=str(e))


@create_task("publish_to_platform", queue="publish_queue")
def publish_to_platform_task(
    self,
    task_id,
    pipeline_id,
    platform,
    video_url,
    title,
    description,
    tags,
    scheduled_time,
    tenant_id,
):
    _set_status(self, task_id, status="running", progress_percent="10")
    try:
        safety = asyncio.run(content_safety_client.check_video_async(video_url))
        if not safety.passed:
            _set_status(
                self, task_id, status="content_filtered",
                error=f"content filtered: {safety.detail}",
            )
            _insert_failed_publish_log(
                pipeline_id, platform, tenant_id, f"content filtered: {safety.detail}",
            )
            raise ContentFilteredException(message=safety.detail)
        _set_status(self, task_id, progress_percent="30")

        _adapt_video(video_url, platform, tenant_id)
        _set_status(self, task_id, progress_percent="50")

        publisher = worker_publishers.get_publisher(platform)
        if publisher is None:
            raise RuntimeError(f"No publisher registered for platform={platform}")
        req = PublishRequest(
            platform=platform,
            content=PublishContent(
                video_url=video_url,
                cover_url=None,
                title=title,
                description=description or "",
                tags=tags or [],
            ),
            platform_config={"tenant_id": tenant_id},
        )
        result = asyncio.run(publisher.publish(req))
        _set_status(self, task_id, progress_percent="85")

        log_id = _insert_publish_log(
            pipeline_id, platform, result, tenant_id, title, description, tags,
        )
        _update_pipeline_publish(pipeline_id, log_id)

        result_data = {
            "platform_post_id": result.platform_post_id,
            "public_url": result.public_url,
            "platform": platform,
            "publish_log_id": log_id,
        }
        _set_status(
            self, task_id, status="completed", progress_percent="100",
            result=json.dumps(result_data, ensure_ascii=False),
        )
        publish_jobs_total.labels(platform=platform, status="success").inc()
        return result_data
    except Exception as e:
        publish_jobs_total.labels(platform=platform, status="failed").inc()
        _set_status(self, task_id, status="failed", error=str(e))
        _insert_failed_publish_log(pipeline_id, platform, tenant_id, e)
        raise
