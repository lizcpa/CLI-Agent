import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import uuid
import json
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request, HTTPException
from utils.mq_clients.celery_app import get_celery_app
from utils.common_sdk.auth import verify_internal_jwt
from .config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_DB
from .models import ComposeRequest, ComposeResponse, ComposeStatus

router = APIRouter(prefix="/api/v1")

REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"


async def get_redis():
    return aioredis.from_url(REDIS_URL, decode_responses=True)


@router.post("/compose", response_model=ComposeResponse)
async def compose(request: Request, body: ComposeRequest, _auth: dict = Depends(verify_internal_jwt)):
    task_id = uuid.uuid4().hex
    tenant_id = getattr(request.state, "tenant_id", "default")

    celery = get_celery_app()
    celery.send_task(
        "compose_video",
        args=[
            task_id, body.pipeline_id, body.video_clips, body.images,
            body.audio_url, body.subtitle_text, body.template_id, body.config,
            tenant_id,
        ],
        queue="compose_queue",
    )

    r = await get_redis()
    try:
        await r.hset(f"task:{task_id}", mapping={
            "status": "queued", "progress_percent": "0",
            "tenant_id": tenant_id, "pipeline_id": body.pipeline_id,
        })
        await r.expire(f"task:{task_id}", 86400)
    finally:
        await r.close()

    return ComposeResponse(task_id=task_id, estimated_seconds=60)


@router.get("/compose/{task_id}", response_model=ComposeStatus)
async def compose_status(task_id: str, request: Request, _auth: dict = Depends(verify_internal_jwt)):
    tenant_id = getattr(request.state, "tenant_id", "default")
    r = await get_redis()
    try:
        data = await r.hgetall(f"task:{task_id}")
        if not data:
            raise HTTPException(status_code=404, detail="Task not found")
        if data.get("tenant_id") != tenant_id:
            raise HTTPException(status_code=404, detail="Task not found")

        raw_result = data.get("result")
        output_url = None
        if raw_result:
            try:
                result_obj = json.loads(raw_result)
                output_url = result_obj.get("output_object") or result_obj.get("output_url")
            except (json.JSONDecodeError, TypeError):
                pass

        return ComposeStatus(
            task_id=task_id,
            status=data.get("status", "unknown"),
            progress=int(data.get("progress_percent", data.get("progress", 0))),
            output_url=output_url,
        )
    finally:
        await r.close()


@router.get("/compose")
async def list_compositions(request: Request, _auth: dict = Depends(verify_internal_jwt)):
    tenant_id = getattr(request.state, "tenant_id", "default")
    r = await get_redis()
    try:
        tasks = []
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match="task:*", count=100)
            for key in keys:
                data = await r.hgetall(key)
                if data.get("tenant_id") == tenant_id:
                    tasks.append({
                        "task_id": key.removeprefix("task:"),
                        "status": data.get("status", "unknown"),
                        "pipeline_id": data.get("pipeline_id", ""),
                    })
            if cursor == 0:
                break
        tasks.sort(key=lambda t: t["task_id"], reverse=True)
        return {"tasks": tasks[:50]}
    finally:
        await r.close()
