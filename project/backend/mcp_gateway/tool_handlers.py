from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from common_sdk.http_client import InternalHTTPClient

from .config import SERVICE_ENDPOINTS

_http = InternalHTTPClient("mcp-gateway", timeout=60.0)


async def _post(service: str, path: str, body: dict, tenant_id: str = "default") -> dict:
    url = f"{SERVICE_ENDPOINTS[service]}{path}"
    return await _http.post(url, json_data=body, target=service, tenant_id=tenant_id)


async def _get(service: str, path: str, tenant_id: str = "default") -> dict:
    url = f"{SERVICE_ENDPOINTS[service]}{path}"
    return await _http.get(url, target=service, tenant_id=tenant_id)


async def handle_crawl_hot_product(arguments: dict) -> dict:
    result = await _post("crawl-scheduler", "/api/v1/crawl/jobs", {
        "platform": arguments.get("platform"),
        "keyword": arguments.get("keyword"),
        "max_count": arguments.get("max_count", 100),
        "sort_by": arguments.get("sort_by", "sales"),
    })
    return result


async def handle_analyze_product(arguments: dict) -> dict:
    result = await _post("product-analyzer", "/api/v1/analyze", {
        "product_ids": arguments.get("product_ids"),
        "platform": arguments.get("platform"),
        "limit": arguments.get("limit", 100),
    })
    return result


async def handle_generate_copywriting(arguments: dict) -> dict:
    result = await _post("ai-generation", "/api/v1/copywriting", {
        "product_id": arguments.get("product_id"),
        "product_title": arguments.get("product_title"),
        "product_desc": arguments.get("product_desc"),
        "keywords": arguments.get("keywords", []),
        "style": arguments.get("style", "marketing"),
        "max_length": arguments.get("max_length", 200),
        "model": arguments.get("model"),
    })
    return result


async def handle_generate_images(arguments: dict) -> dict:
    result = await _post("ai-generation", "/api/v1/images/generate", {
        "prompts": arguments.get("prompts"),
        "size": arguments.get("size", "1024x1024"),
        "n": arguments.get("n", 1),
        "negative_prompt": arguments.get("negative_prompt"),
        "model": arguments.get("model"),
        "seed": arguments.get("seed"),
    })
    return result


async def handle_generate_video_clips(arguments: dict) -> dict:
    result = await _post("ai-generation", "/api/v1/videos/generate", {
        "type": arguments.get("type", "text2video"),
        "prompts": arguments.get("prompts"),
        "reference_image_url": arguments.get("reference_image_url"),
        "duration": arguments.get("duration", 5),
        "resolution": arguments.get("resolution", "1080p"),
        "count": arguments.get("count", 1),
        "motion_strength": arguments.get("motion_strength", 0.5),
        "model": arguments.get("model"),
    })
    return result


async def handle_compose_video(arguments: dict) -> dict:
    result = await _post("video-composer", "/api/v1/compose", {
        "pipeline_id": arguments.get("pipeline_id"),
        "video_clips": arguments.get("video_clips"),
        "images": arguments.get("images", []),
        "audio_url": arguments.get("audio_url"),
        "subtitle_text": arguments.get("subtitle_text"),
        "template_id": arguments.get("template_id"),
        "config": arguments.get("config"),
    })
    return result


async def handle_publish_content(arguments: dict) -> dict:
    result = await _post("publish-dispatcher", "/api/v1/publish", {
        "pipeline_id": arguments.get("pipeline_id"),
        "video_url": arguments.get("video_url"),
        "platforms": arguments.get("platforms"),
        "title": arguments.get("title"),
        "description": arguments.get("description"),
        "tags": arguments.get("tags", []),
        "scheduled_time": arguments.get("scheduled_time"),
    })
    return result


async def handle_query_task_status(arguments: dict) -> dict:
    task_id = arguments.get("task_id")
    try:
        import redis.asyncio as aioredis
        from common_sdk.config import config_manager
        r = aioredis.Redis(
            host=config_manager.get("REDIS_HOST", "localhost"),
            port=int(config_manager.get("REDIS_PORT", "6379")),
            password=config_manager.get("REDIS_PASSWORD", ""),
            db=int(config_manager.get("REDIS_DB", "0")),
            decode_responses=True,
        )
        data = await r.hgetall(f"task:{task_id}")
        try:
            await r.aclose()
        except AttributeError:
            await r.close()
        if data:
            return {"task_id": task_id, "status": data.get("status", "unknown"),
                    "progress": data.get("progress_percent", data.get("progress", 0)),
                    "result": data.get("result")}
    except Exception:
        pass

    for service_path in [
        ("crawl-scheduler", f"/api/v1/crawl/jobs/{task_id}"),
        ("ai-generation", f"/api/v1/tasks/{task_id}"),
        ("video-composer", f"/api/v1/compose/{task_id}"),
        ("publish-dispatcher", f"/api/v1/publish/{task_id}"),
    ]:
        try:
            svc, path = service_path
            result = await _get(svc, path)
            return {"task_id": task_id, **result.get("data", result)}
        except Exception:
            continue

    return {"task_id": task_id, "status": "not_found", "error": "Task not found"}


async def handle_list_models(arguments: dict) -> dict:
    model_type = arguments.get("type")
    path = f"/api/v1/models?type={model_type}" if model_type else "/api/v1/models"
    try:
        result = await _get("ai-generation", path)
        return result
    except Exception:
        return {"models": []}
