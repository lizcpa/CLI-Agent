from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from common_sdk.auth import verify_internal_jwt
from common_sdk.exceptions import ValidationException
from common_sdk.logger import get_logger
from db_clients.mysql import get_mysql_client

from .config import REDIS_DB, REDIS_HOST, REDIS_PASSWORD, REDIS_PORT

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["pipeline-orchestrator"])

_redis: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD or None,
            db=REDIS_DB,
            decode_responses=True,
        )
    return _redis


class CreatePipelineRequest(BaseModel):
    product_id: int
    config: dict | None = None


@router.post("/pipelines")
async def create_pipeline(request: Request, body: CreatePipelineRequest, _auth: dict = Depends(verify_internal_jwt)):
    tenant_id = getattr(request.state, "tenant_id", "default")
    redis = await _get_redis()
    idempotency_key = f"pipeline:active:{body.product_id}:{tenant_id}"
    task_id = f"pipe_{uuid.uuid4().hex[:12]}"
    acquired = await redis.set(idempotency_key, task_id, nx=True, ex=3600)
    if not acquired:
        existing = await redis.get(idempotency_key)
        logger.warning("pipeline_already_active", product_id=body.product_id, tenant_id=tenant_id)
        raise ValidationException(
            f"Pipeline already active for product {body.product_id}",
            data={"existing_task_id": existing},
        )

    from mq_clients.celery_app import get_celery_app
    app = get_celery_app()
    app.send_task(
        "pipeline_orchestrator.tasks.run_pipeline_task",
        args=[task_id, body.product_id, tenant_id, body.config or {}],
        queue="orchestrator_queue",
    )
    return {"task_id": task_id, "product_id": body.product_id, "status": "queued"}


@router.get("/pipelines/{pipeline_id}")
async def get_pipeline(pipeline_id: int, request: Request, _auth: dict = Depends(verify_internal_jwt)):
    tenant_id = getattr(request.state, "tenant_id", "default")
    mysql = get_mysql_client()
    await mysql.execute("SELECT * FROM generation_pipelines WHERE id=%s AND tenant_id=%s", (pipeline_id, tenant_id))
    row = await mysql.fetchone()
    if not row:
        return {"error": "Pipeline not found", "pipeline_id": pipeline_id}
    return {"pipeline": row}
