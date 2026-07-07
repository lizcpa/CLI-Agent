import json
import uuid
import logging

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, HTTPException
from fastapi.responses import JSONResponse

from utils.common_sdk.response import success_response, error_response, async_task_response
from utils.db_clients.minio import get_minio_client
from utils.db_clients.mysql import get_mysql_client

from .config import MINIO_BUCKET
from .auth import verify_internal_request
from .models import VideoAdaptRequest, PlatformConfigCreate, TemplateCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/assets")


_PLATFORM_DEFAULTS = {
    "youtube": (1920, 1080, 0),
    "tiktok": (1080, 1920, 60),
    "instagram": (1080, 1080, 60),
    "default": (1080, 1920, 60),
}


async def _get_platform_dims(platform: str) -> tuple[int, int, int]:
    try:
        mysql = get_mysql_client()
        rows = await mysql.fetchall(
            "SELECT config_key, config_value FROM platform_config WHERE platform=%s",
            (platform,),
        )
        cfg = {r["config_key"]: r["config_value"] for r in rows}
        default = _PLATFORM_DEFAULTS.get(platform, _PLATFORM_DEFAULTS["default"])
        return (
            int(cfg.get("width", default[0])),
            int(cfg.get("height", default[1])),
            int(cfg.get("max_duration", default[2])),
        )
    except Exception:
        return _PLATFORM_DEFAULTS.get(platform, _PLATFORM_DEFAULTS["default"])


@router.post("/adapt", dependencies=[Depends(verify_internal_request)])
async def adapt_video(req: VideoAdaptRequest):
    from utils.mq_clients.celery_app import get_celery_app
    task_id = f"adapt_{uuid.uuid4().hex[:16]}"
    platforms = req.platforms or ["default"]
    for platform in platforms:
        width, height, max_duration = await _get_platform_dims(platform)
        celery_app = get_celery_app()
        celery_app.send_task(
            "adapt_video_for_platform",
            args=[task_id, req.video_url, platform, width, height, max_duration],
            queue="compose_queue",
        )
    return JSONResponse(content=async_task_response(task_id).model_dump())


@router.post("/upload", dependencies=[Depends(verify_internal_request)])
async def upload_asset(file: UploadFile = File(...)):
    minio = get_minio_client()
    contents = await file.read()
    object_name = f"uploaded/{uuid.uuid4().hex[:8]}_{file.filename}"
    minio.upload_stream(MINIO_BUCKET, object_name, contents, len(contents), file.content_type or "application/octet-stream")
    presigned_url = minio.get_presigned_url(MINIO_BUCKET, object_name, expires_seconds=3600)
    return JSONResponse(content=success_response({
        "object_name": object_name,
        "bucket": MINIO_BUCKET,
        "presigned_url": presigned_url,
    }).model_dump())


@router.get("/download/{bucket}/{object_name:path}", dependencies=[Depends(verify_internal_request)])
async def download_asset(bucket: str, object_name: str):
    minio = get_minio_client()
    try:
        presigned_url = minio.get_presigned_url(bucket, object_name, expires_seconds=3600)
        return JSONResponse(content=success_response({"url": presigned_url}).model_dump())
    except Exception:
        return JSONResponse(content=error_response(404, "Object not found").model_dump(), status_code=404)


@router.get("/platform-configs", dependencies=[Depends(verify_internal_request)])
async def list_platform_configs(request: Request, platform: str | None = Query(None)):
    tenant_id = getattr(request.state, "tenant_id", "default")
    mysql = get_mysql_client()
    if platform:
        rows = await mysql.fetchall(
            "SELECT id, platform, config_key, config_value, description, created_at, updated_at FROM platform_config WHERE platform = %s AND tenant_id = %s ORDER BY id",
            (platform, tenant_id),
        )
    else:
        rows = await mysql.fetchall(
            "SELECT id, platform, config_key, config_value, description, created_at, updated_at FROM platform_config WHERE tenant_id = %s ORDER BY id",
            (tenant_id,),
        )
    return JSONResponse(content=success_response(rows).model_dump())


@router.post("/platform-configs", dependencies=[Depends(verify_internal_request)])
async def upsert_platform_config(body: PlatformConfigCreate, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    mysql = get_mysql_client()
    existing = await mysql.fetchone(
        "SELECT id FROM platform_config WHERE platform = %s AND config_key = %s AND tenant_id = %s",
        (body.platform, body.config_key, tenant_id),
    )
    if existing:
        await mysql.execute(
            "UPDATE platform_config SET config_value = %s, description = %s, updated_at = NOW() WHERE id = %s AND tenant_id = %s",
            (body.config_value, body.description, existing["id"], tenant_id),
        )
        return JSONResponse(content=success_response({"id": existing["id"], "action": "updated"}).model_dump())
    await mysql.execute(
        "INSERT INTO platform_config (tenant_id, platform, config_key, config_value, description) VALUES (%s, %s, %s, %s, %s)",
        (tenant_id, body.platform, body.config_key, body.config_value, body.description),
    )
    return JSONResponse(content=success_response({"action": "created"}).model_dump(), status_code=201)


@router.delete("/platform-configs/{config_id}", dependencies=[Depends(verify_internal_request)])
async def delete_platform_config(config_id: int, request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    mysql = get_mysql_client()
    await mysql.execute("DELETE FROM platform_config WHERE id = %s AND tenant_id = %s", (config_id, tenant_id))
    return JSONResponse(content=success_response({"deleted": config_id}).model_dump())


@router.get("/templates", dependencies=[Depends(verify_internal_request)])
async def list_templates():
    minio = get_minio_client()
    objects = minio.list_objects(MINIO_BUCKET, prefix="templates/")
    templates = []
    for obj in objects:
        name = obj["object_name"]
        if name.endswith(".json"):
            templates.append({
                "template_id": name.replace("templates/", "").replace(".json", ""),
                "object_name": name,
                "size": obj["size"],
                "last_modified": obj["last_modified"],
            })
    return JSONResponse(content=success_response(templates).model_dump())


@router.post("/templates", dependencies=[Depends(verify_internal_request)])
async def create_template(body: TemplateCreate):
    minio = get_minio_client()
    template_id = uuid.uuid4().hex[:12]
    object_name = f"templates/{template_id}.json"
    content_bytes = json.dumps(body.content, ensure_ascii=False).encode("utf-8")
    minio.upload_stream(MINIO_BUCKET, object_name, content_bytes, len(content_bytes), "application/json")
    return JSONResponse(content=success_response({
        "template_id": template_id,
        "name": body.name,
        "object_name": object_name,
    }).model_dump(), status_code=201)


@router.get("/templates/{template_id}", dependencies=[Depends(verify_internal_request)])
async def get_template(template_id: str):
    minio = get_minio_client()
    object_name = f"templates/{template_id}.json"
    try:
        import io as stdio
        response = minio._ensure_client().get_object(MINIO_BUCKET, object_name)
        content = json.loads(response.read().decode("utf-8"))
        response.close()
        response.release_conn()
        return JSONResponse(content=success_response(content).model_dump())
    except Exception:
        return JSONResponse(content=error_response(404, "Template not found").model_dump(), status_code=404)
