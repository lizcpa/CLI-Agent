import uuid
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from utils.common_sdk.response import success_response, async_task_response
from utils.db_clients.mysql import get_mysql_client

from .config import ASSET_MANAGER_URL
from utils.common_sdk.auth import verify_internal_jwt as verify_internal_request
from .models import PublishRequest, PublishLogEntry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


@router.post("/publish", dependencies=[Depends(verify_internal_request)])
async def publish_video(req: PublishRequest):
    from utils.mq_clients.celery_app import get_celery_app
    celery_app = get_celery_app()
    platform_tasks = []
    for platform in req.platforms:
        task_id = f"pub_{uuid.uuid4().hex[:16]}"
        celery_app.send_task(
            "publish_to_platform",
            args=[
                task_id,
                req.pipeline_id,
                platform,
                req.video_url,
                req.title,
                req.description,
                req.tags,
                req.scheduled_time,
                req.tenant_id,
            ],
            queue="publish_queue",
        )
        platform_tasks.append({"platform": platform, "task_id": task_id})
    response_data = {"pipeline_id": req.pipeline_id, "platform_tasks": platform_tasks}
    return JSONResponse(content=success_response(response_data).model_dump())


@router.get("/publish/{task_id}", dependencies=[Depends(verify_internal_request)])
async def get_publish_status(task_id: str, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    mysql = get_mysql_client()
    row = await mysql.fetchone(
        "SELECT id, pipeline_id, platform, platform_post_id, status, public_url, error_message FROM publish_log WHERE platform_post_id = %s AND tenant_id = %s",
        (task_id, tenant_id),
    )
    if not row:
        row = await mysql.fetchone(
            "SELECT id, pipeline_id, platform, platform_post_id, status, public_url, error_message FROM publish_log WHERE pipeline_id = %s AND tenant_id = %s ORDER BY id DESC LIMIT 1",
            (task_id, tenant_id),
        )
    if not row:
        return JSONResponse(
            content=success_response(None).model_dump(),
            status_code=404,
        )
    return JSONResponse(content=success_response(row).model_dump())


@router.get("/publish/pipeline/{pipeline_id}", dependencies=[Depends(verify_internal_request)])
async def get_pipeline_publish_logs(pipeline_id: str, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT id, pipeline_id, platform, platform_post_id, status, public_url, error_message FROM publish_log WHERE pipeline_id = %s AND tenant_id = %s ORDER BY id",
        (pipeline_id, tenant_id),
    )
    return JSONResponse(content=success_response(rows).model_dump())


@router.get("/platforms", dependencies=[Depends(verify_internal_request)])
async def list_authorized_platforms(request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT id, tenant_id, platform, is_authorized, authorized_at, expires_at FROM platform_authorizations WHERE tenant_id = %s AND is_authorized = 1 ORDER BY id",
        (tenant_id,),
    )
    return JSONResponse(content=success_response(rows).model_dump())
