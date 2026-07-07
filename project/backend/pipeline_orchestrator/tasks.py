import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import asyncio
import json

from mq_clients.celery_app import create_task, BaseTask
from db_clients.mysql import get_mysql_client
from common_sdk.business_metrics import pipeline_runs_total
from common_sdk.http_client import InternalHTTPClient
from common_sdk.logger import get_logger

from .config import SERVICE_ENDPOINTS, SERVICE_NAME

logger = get_logger(__name__)


def _set_status(task: BaseTask, task_id: str, **fields) -> None:
    task.redis_client.hset(f"task:{task_id}", mapping=fields)
    task.redis_client.expire(f"task:{task_id}", 86400)


async def _create_pipeline(product_id: int, tenant_id: str) -> int:
    mysql = get_mysql_client()
    await mysql.execute(
        "INSERT INTO generation_pipelines (tenant_id, product_id, stage) VALUES (%s, %s, 'analyzing')",
        (tenant_id, product_id),
    )
    row = await mysql.fetchone("SELECT LAST_INSERT_ID() AS id")
    return int(row["id"])


async def _update_pipeline(pipeline_id: int, **fields) -> None:
    mysql = get_mysql_client()
    cols = ", ".join(f"{k}=%s" for k in fields)
    params = list(fields.values()) + [pipeline_id]
    await mysql.execute(
        f"UPDATE generation_pipelines SET {cols}, updated_at=NOW() WHERE id=%s",
        tuple(params),
    )


async def _get_product(product_id: int) -> dict:
    mysql = get_mysql_client()
    return await mysql.fetchone("SELECT * FROM products WHERE id=%s", (product_id,))


@create_task("run_pipeline", queue="orchestrator_queue")
def run_pipeline_task(
    self,
    task_id: str,
    product_id: int,
    tenant_id: str = "default",
    config: dict | None = None,
):
    """DAG: analyze -> parallel(copywriting, images, video_clips) -> compose -> publish"""
    config = config or {}
    _set_status(self, task_id, status="running", progress_percent="5")
    return asyncio.run(_run_pipeline_async(self, task_id, product_id, tenant_id, config))


async def _run_pipeline_async(
    task: BaseTask,
    task_id: str,
    product_id: int,
    tenant_id: str,
    config: dict,
):
    """Async pipeline body. Single event loop so the InternalHTTPClient
    (lazy httpx client + per-target breakers) persists across all stages."""
    http = InternalHTTPClient(SERVICE_NAME, timeout=300.0)
    pipeline_id = None

    try:
        pipeline_id = await _create_pipeline(product_id, tenant_id)
        _set_status(task, task_id, progress_percent="10", pipeline_id=str(pipeline_id))

        await _update_pipeline(pipeline_id, stage="analyzing")
        await http.post(
            f"{SERVICE_ENDPOINTS['product-analyzer']}/api/v1/analyze",
            json_data={"product_ids": [product_id]},
            target="product-analyzer",
            tenant_id=tenant_id,
        )
        _set_status(task, task_id, progress_percent="20")

        product = await _get_product(product_id)
        if not product:
            raise RuntimeError(f"Product {product_id} not found")

        product_title = product.get("title", "")
        product_desc = product.get("description", "")
        main_image = product.get("main_image_url", "")
        keywords = product.get("tags", []) or []

        await _update_pipeline(pipeline_id, stage="generating")
        _set_status(task, task_id, progress_percent="30")

        async def _run_generation():
            copy_task = http.post(
                f"{SERVICE_ENDPOINTS['ai-generation']}/api/v1/copywriting",
                json_data={
                    "product_id": product_id,
                    "product_title": product_title,
                    "product_desc": product_desc,
                    "keywords": keywords,
                    "style": config.get("style", "marketing"),
                    "max_length": config.get("max_length", 200),
                },
                target="ai-generation",
                tenant_id=tenant_id,
            )

            img_prompts = [f"{product_title} 产品展示图"]
            img_task = http.post(
                f"{SERVICE_ENDPOINTS['ai-generation']}/api/v1/images/generate",
                json_data={
                    "prompts": img_prompts,
                    "size": "1024x1024",
                    "n": config.get("image_count", 3),
                },
                target="ai-generation",
                tenant_id=tenant_id,
            )

            vid_prompts = [f"{product_title} 产品宣传视频片段"]
            vid_task = http.post(
                f"{SERVICE_ENDPOINTS['ai-generation']}/api/v1/videos/generate",
                json_data={
                    "type": "text2video",
                    "prompts": vid_prompts,
                    "reference_image_url": main_image,
                    "duration": config.get("video_duration", 5),
                    "count": config.get("video_count", 2),
                },
                target="ai-generation",
                tenant_id=tenant_id,
            )

            return await asyncio.gather(copy_task, img_task, vid_task, return_exceptions=True)

        gen_results = await _run_generation()

        copy_result = gen_results[0]
        img_result = gen_results[1]
        vid_result = gen_results[2]

        copywriting = ""
        if isinstance(copy_result, dict):
            copywriting = copy_result.get("text", "")
            await _update_pipeline(pipeline_id, copywriting=copywriting, copywriting_status="completed")
        else:
            logger.warning("copywriting_failed", error=str(copy_result))
            await _update_pipeline(pipeline_id, copywriting_status="failed")

        image_urls = []
        if isinstance(img_result, dict):
            image_urls = img_result.get("image_objects", [])
            await _update_pipeline(pipeline_id, image_urls=json.dumps(image_urls), images_status="completed")
        else:
            logger.warning("images_failed", error=str(img_result))
            await _update_pipeline(pipeline_id, images_status="failed")

        video_clips = []
        if isinstance(vid_result, dict):
            video_clips = vid_result.get("clip_objects", [])
            await _update_pipeline(pipeline_id, video_clip_urls=json.dumps(video_clips), video_clips_status="completed")
        else:
            logger.warning("video_clips_failed", error=str(vid_result))
            await _update_pipeline(pipeline_id, video_clips_status="failed")

        if not video_clips:
            raise RuntimeError("No video clips generated — cannot compose")

        _set_status(task, task_id, progress_percent="60")

        await _update_pipeline(pipeline_id, stage="composing")
        compose_result = await http.post(
            f"{SERVICE_ENDPOINTS['video-composer']}/api/v1/compose",
            json_data={
                "pipeline_id": str(pipeline_id),
                "video_clips": video_clips,
                "images": image_urls,
                "audio_url": None,
                "subtitle_text": copywriting[:200] if copywriting else None,
                "template_id": config.get("template_id"),
                "config": config.get("compose_config"),
            },
            target="video-composer",
            tenant_id=tenant_id,
        )
        final_video_url = compose_result.get("output_object") or compose_result.get("output_url", "")
        _set_status(task, task_id, progress_percent="80")

        await _update_pipeline(pipeline_id, stage="publishing")
        platforms = config.get("platforms", ["youtube", "tiktok"])
        publish_result = await http.post(
            f"{SERVICE_ENDPOINTS['publish-dispatcher']}/api/v1/publish",
            json_data={
                "pipeline_id": str(pipeline_id),
                "video_url": final_video_url,
                "platforms": platforms,
                "title": product_title,
                "description": copywriting or product_desc,
                "tags": keywords,
                "scheduled_time": config.get("scheduled_time"),
            },
            target="publish-dispatcher",
            tenant_id=tenant_id,
        )

        await _update_pipeline(pipeline_id, stage="completed")
        result = {
            "pipeline_id": pipeline_id,
            "final_video_url": final_video_url,
            "publish_result": publish_result,
        }
        _set_status(
            task, task_id, status="completed", progress_percent="100",
            result=json.dumps(result, ensure_ascii=False),
        )
        pipeline_runs_total.labels(status="success").inc()
        return result

    except Exception as e:
        logger.error("pipeline_failed", product_id=product_id, error=str(e))
        pipeline_runs_total.labels(status="failed").inc()
        if pipeline_id is not None:
            try:
                await _update_pipeline(pipeline_id, stage="failed", error_message=str(e)[:500])
            except Exception:
                pass
        _set_status(task, task_id, status="failed", error=str(e))
        raise

    finally:
        # Idempotency key cleanup — allows retry after completion/failure.
        try:
            task.redis_client.delete(f"pipeline:active:{product_id}:{tenant_id}")
        except Exception:
            pass
        await http.close()
