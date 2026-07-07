from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import uuid

from fastapi import APIRouter, Depends, Query, Request

from common_sdk.response import success_response, error_response, async_task_response
from common_sdk.exceptions import NotFoundException, ServiceException, ValidationException

from .models import (
    CopywritingRequest,
    CopywritingResponse,
    ImageGenerateRequest,
    ImageGenerateResponse,
    VideoGenerateRequest,
    VideoGenerateResponse,
    TTSRequest,
    TTSResponse,
    ModelInfo,
    UsageLogEntry,
)
from common_sdk.auth import verify_internal_jwt
from .router import ModelRouterService


def get_router_service() -> ModelRouterService:
    from .main import _router_service
    if _router_service is None:
        raise ServiceException("Router service not initialized")
    return _router_service


def _get_mysql_client():
    from db_clients.mysql import get_mysql_client
    return get_mysql_client()


def _get_redis_client():
    from db_clients.redis import get_redis_client
    return get_redis_client()


def _get_minio_client():
    from db_clients.minio import get_minio_client
    return get_minio_client()


router = APIRouter(prefix="/api/v1")


@router.post("/copywriting")
async def generate_copywriting(
    req: CopywritingRequest,
    http_request: Request,
    _payload: dict = Depends(verify_internal_jwt),
):
    task_id = f"copywriting_{uuid.uuid4().hex[:12]}"
    from mq_clients.task_manager import task_manager, TaskStatus
    task_manager.create_task_record(task_id, "copywriting", req.model_dump())

    try:
        from celery import current_app
        current_app.send_task(
            "ai_generation.tasks.generate_copywriting_task",
            args=[task_id, req.product_id, req.product_title],
            kwargs={
                "product_desc": req.product_desc,
                "keywords": req.keywords,
                "style": req.style,
                "max_length": req.max_length,
                "model": req.model,
                "tenant_id": getattr(http_request.state, "tenant_id", "default"),
                "pipeline_id": getattr(req, "pipeline_id", "") or "",
            },
            queue="ai_queue",
        )
    except Exception as e:
        task_manager.update_task_progress(task_id, TaskStatus.FAILED, error=str(e))
        raise ServiceException(f"Failed to queue copywriting task: {e}")

    return async_task_response(task_id)


@router.post("/images/generate")
async def generate_images(
    req: ImageGenerateRequest,
    http_request: Request,
    _payload: dict = Depends(verify_internal_jwt),
):
    task_id = f"image_{uuid.uuid4().hex[:12]}"
    from mq_clients.task_manager import task_manager, TaskStatus
    task_manager.create_task_record(task_id, "image_generation", req.model_dump())

    try:
        from celery import current_app
        current_app.send_task(
            "ai_generation.tasks.generate_images_task",
            args=[task_id, req.prompts],
            kwargs={
                "size": req.size,
                "n": req.n,
                "negative_prompt": req.negative_prompt,
                "model": req.model,
                "seed": req.seed,
                "tenant_id": getattr(http_request.state, "tenant_id", "default"),
                "pipeline_id": getattr(req, "pipeline_id", "") or "",
            },
            queue="ai_queue",
        )
    except Exception as e:
        task_manager.update_task_progress(task_id, TaskStatus.FAILED, error=str(e))
        raise ServiceException(f"Failed to queue image task: {e}")

    return async_task_response(task_id)


@router.post("/videos/generate")
async def generate_videos(
    req: VideoGenerateRequest,
    http_request: Request,
    _payload: dict = Depends(verify_internal_jwt),
):
    task_id = f"video_{uuid.uuid4().hex[:12]}"
    from mq_clients.task_manager import task_manager, TaskStatus
    task_manager.create_task_record(task_id, "video_generation", req.model_dump())

    try:
        from celery import current_app
        current_app.send_task(
            "ai_generation.tasks.generate_video_clips_task",
            args=[task_id, req.type, req.prompts],
            kwargs={
                "reference_image_url": req.reference_image_url,
                "duration": req.duration,
                "resolution": req.resolution,
                "count": req.count,
                "motion_strength": req.motion_strength,
                "model": req.model,
                "tenant_id": getattr(http_request.state, "tenant_id", "default"),
                "pipeline_id": getattr(req, "pipeline_id", "") or "",
            },
            queue="ai_queue",
        )
    except Exception as e:
        task_manager.update_task_progress(task_id, TaskStatus.FAILED, error=str(e))
        raise ServiceException(f"Failed to queue video task: {e}")

    return async_task_response(task_id)


@router.post("/tts/synthesize")
async def synthesize_tts(
    req: TTSRequest,
    http_request: Request,
    _payload: dict = Depends(verify_internal_jwt),
):
    task_id = f"tts_{uuid.uuid4().hex[:12]}"
    from mq_clients.task_manager import task_manager, TaskStatus
    task_manager.create_task_record(task_id, "tts_synthesis", req.model_dump())

    try:
        from celery import current_app
        current_app.send_task(
            "ai_generation.tasks.tts_synthesize_task",
            args=[task_id, req.text],
            kwargs={
                "voice": req.voice,
                "language": req.language,
                "speed": req.speed,
                "tenant_id": getattr(http_request.state, "tenant_id", "default"),
                "pipeline_id": getattr(req, "pipeline_id", "") or "",
            },
            queue="ai_queue",
        )
    except Exception as e:
        task_manager.update_task_progress(task_id, TaskStatus.FAILED, error=str(e))
        raise ServiceException(f"Failed to queue TTS task: {e}")

    return async_task_response(task_id)


@router.get("/models")
async def list_models(
    type: str | None = Query(None, description="模型类型: image/video/llm/tts"),
    _payload: dict = Depends(verify_internal_jwt),
    router_svc: ModelRouterService = Depends(get_router_service),
):
    if type:
        models = router_svc.get_available_models(type)
    else:
        models = []
        for t in ("llm", "image", "video", "tts"):
            models.extend(router_svc.get_available_models(t))

    result = [
        {
            "id": m["id"],
            "type": m["type"],
            "name": m["name"],
            "is_healthy": m["is_healthy"],
            "capabilities": m["capabilities"],
        }
        for m in models
    ]
    return success_response(result)


@router.post("/internal/llm/chat")
async def internal_llm_chat(
    request: dict,
    _payload: dict = Depends(verify_internal_jwt),
    router_svc: ModelRouterService = Depends(get_router_service),
):
    messages = request.get("messages", [])
    max_tokens = request.get("max_tokens", 2048)
    temperature = request.get("temperature", 0.7)
    model = request.get("model")

    adapter = router_svc.route_llm(preferred_model=model, product_tier="normal")
    if adapter is None:
        raise ServiceException("No healthy LLM adapter available")

    try:
        result = await adapter.chat_async(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return success_response(result)
    except Exception as e:
        router_svc.router.track_failure(adapter.adapter_id)
        raise ServiceException(f"LLM chat failed: {e}")


@router.post("/internal/image/generate")
async def internal_image_generate(
    request: dict,
    _payload: dict = Depends(verify_internal_jwt),
    router_svc: ModelRouterService = Depends(get_router_service),
):
    prompts = request.get("prompts", [])
    size = request.get("size", "1024x1024")
    n = request.get("n", 1)
    negative_prompt = request.get("negative_prompt")
    model = request.get("model")
    seed = request.get("seed")
    tenant_id = request.get("tenant_id", "default")
    pipeline_id = request.get("pipeline_id", "")

    adapter = router_svc.route_image(preferred_model=model, product_tier="normal")
    if adapter is None:
        raise ServiceException("No healthy Image adapter available")

    try:
        result = await adapter.generate_async(
            prompts=prompts,
            size=size,
            n=n,
            negative_prompt=negative_prompt,
            seed=seed,
            tenant_id=tenant_id,
            pipeline_id=pipeline_id,
        )
        return success_response(result)
    except Exception as e:
        router_svc.router.track_failure(adapter.adapter_id)
        raise ServiceException(f"Image generation failed: {e}")


@router.post("/internal/video/generate")
async def internal_video_generate(
    request: dict,
    _payload: dict = Depends(verify_internal_jwt),
    router_svc: ModelRouterService = Depends(get_router_service),
):
    video_type = request.get("type", "")
    prompts = request.get("prompts", [])
    reference_image_url = request.get("reference_image_url")
    model = request.get("model")
    duration = request.get("duration", 5)
    resolution = request.get("resolution", "1080p")
    count = request.get("count", 1)
    motion_strength = request.get("motion_strength", 0.5)
    tenant_id = request.get("tenant_id", "default")
    pipeline_id = request.get("pipeline_id", "")

    adapter = router_svc.route_video(preferred_model=model, product_tier="normal")
    if adapter is None:
        raise ServiceException("No healthy Video adapter available")

    try:
        result = await adapter.generate_async(
            type=video_type,
            prompts=prompts,
            reference_image_url=reference_image_url,
            duration=duration,
            resolution=resolution,
            count=count,
            motion_strength=motion_strength,
            tenant_id=tenant_id,
            pipeline_id=pipeline_id,
        )
        return success_response(result)
    except Exception as e:
        router_svc.router.track_failure(adapter.adapter_id)
        raise ServiceException(f"Video generation failed: {e}")


@router.post("/internal/tts/synthesize")
async def internal_tts_synthesize(
    request: dict,
    _payload: dict = Depends(verify_internal_jwt),
    router_svc: ModelRouterService = Depends(get_router_service),
):
    text = request.get("text", "")
    voice = request.get("voice", "default")
    language = request.get("language", "zh")
    speed = request.get("speed", 1.0)
    tenant_id = request.get("tenant_id", "default")
    pipeline_id = request.get("pipeline_id", "")

    adapter = router_svc.route_tts(preferred_model=None)
    if adapter is None:
        raise ServiceException("No healthy TTS adapter available")

    try:
        result = await adapter.synthesize_async(
            text=text,
            voice=voice,
            language=language,
            speed=speed,
            tenant_id=tenant_id,
            pipeline_id=pipeline_id,
        )
        return success_response(result)
    except Exception as e:
        router_svc.router.track_failure(adapter.adapter_id)
        raise ServiceException(f"TTS synthesis failed: {e}")


@router.post("/internal/usage/log")
async def internal_usage_log(
    entry: UsageLogEntry,
    _payload: dict = Depends(verify_internal_jwt),
):
    mysql = _get_mysql_client()

    sql = """
        INSERT INTO model_usage_log
        (adapter_id, adapter_type, model, pipeline_id, tenant_id,
         input_tokens, output_tokens, image_count, duration_seconds,
         estimated_cost, status, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
    """
    params = (
        entry.adapter_id,
        entry.adapter_type,
        entry.model,
        entry.pipeline_id,
        entry.tenant_id,
        entry.input_tokens,
        entry.output_tokens,
        entry.image_count,
        entry.duration_seconds,
        entry.estimated_cost,
        entry.status,
    )
    await mysql.execute(sql, params)

    return success_response({"logged": True})


@router.get("/internal/tasks/{task_id}/result")
async def internal_task_result(
    task_id: str,
    _payload: dict = Depends(verify_internal_jwt),
):
    from mq_clients.task_manager import task_manager
    result = task_manager.get_task_result(task_id)
    if result is None:
        raise NotFoundException(f"Task {task_id} not found or no result yet")
    return success_response(result)
