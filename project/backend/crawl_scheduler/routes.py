from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from fastapi import APIRouter, Depends, HTTPException, Request

from common_sdk.exceptions import ValidationException
from common_sdk.response import async_task_response, paginated_response, success_response
from mq_clients.task_manager import TaskStatus, task_manager
import db_clients.mysql as mysql_mod

from .auth import verify_internal_jwt
from .config import SUPPORTED_PLATFORMS
from .models import (
    CrawlJobRequest,
    CrawlJobResponse,
    CrawlJobStatus,
    ConnectorInfo,
    CrawlPlanCreate,
    CrawlPlanUpdate,
)
from .tasks import execute_crawl_job
from common_sdk.registry_config import load_platform_connectors

router = APIRouter(prefix="/api/v1/crawl", tags=["crawl-scheduler"])

def _load_platform_connectors() -> list[ConnectorInfo]:
    return [ConnectorInfo(**p) for p in load_platform_connectors()]


def _make_job_id() -> str:
    return f"crawl-{uuid.uuid4().hex[:12]}"


def _job_id_from_task_id(task_id: str) -> str:
    return f"crawl-{task_id[:12]}"


@router.post("/jobs")
async def create_crawl_job(
    body: CrawlJobRequest,
    request: Request,
    payload: dict = Depends(verify_internal_jwt),
):
    if body.platform not in SUPPORTED_PLATFORMS:
        raise ValidationException(
            message=f"Unsupported platform '{body.platform}'. Supported: {SUPPORTED_PLATFORMS}"
        )

    tenant_id = request.state.tenant_id
    job_id = _make_job_id()

    celery_task = execute_crawl_job.delay(
        task_id=job_id,
        platform=body.platform,
        keyword=body.keyword,
        max_count=body.max_count,
        sort_by=body.sort_by,
        tenant_id=tenant_id,
    )

    task_manager.create_task_record(
        task_id=job_id,
        task_type="execute_crawl_job",
        params={
            "platform": body.platform,
            "keyword": body.keyword,
            "max_count": body.max_count,
            "sort_by": body.sort_by,
            "tenant_id": tenant_id,
        },
    )

    response_data = CrawlJobResponse(
        job_id=job_id,
        task_id=celery_task.id,
        status="queued",
        estimated_seconds=30,
    )
    return async_task_response(task_id=job_id, estimated_seconds=30)


@router.get("/jobs/{job_id}")
async def get_crawl_job(job_id: str, request: Request, payload: dict = Depends(verify_internal_jwt)):
    data = task_manager.get_task_status(job_id)
    if data.get("status") == "unknown":
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return success_response(
        CrawlJobStatus(
            job_id=job_id,
            status=data.get("status", "unknown"),
            progress=int(data.get("progress_percent", "0")),
            products_found=0,
            error=data.get("error") or None,
        ).model_dump()
    )


@router.get("/jobs")
async def list_crawl_jobs(
    request: Request,
    payload: dict = Depends(verify_internal_jwt),
    page: int = 1,
    page_size: int = 20,
):
    import redis as sync_redis

    host, port, db = _parse_redis_url()
    r = sync_redis.Redis(host=host, port=port, db=db, decode_responses=True)
    try:
        all_keys = r.keys("task:crawl-*")
        all_keys.sort(reverse=True)
        total = len(all_keys)
        start = (page - 1) * page_size
        end = start + page_size
        page_keys = all_keys[start:end]

        jobs = []
        for key in page_keys:
            job_id = key.removeprefix("task:")
            data = r.hgetall(key)
            jobs.append(
                CrawlJobStatus(
                    job_id=job_id,
                    status=data.get("status", "unknown"),
                    progress=int(data.get("progress_percent", "0")),
                    products_found=0,
                    error=data.get("error") or None,
                ).model_dump()
            )
    finally:
        r.close()

    return paginated_response(items=jobs, total=total, page=page, page_size=page_size)


@router.get("/platforms")
async def list_platforms(request: Request, payload: dict = Depends(verify_internal_jwt)):
    return success_response([p.model_dump() for p in _load_platform_connectors()])


@router.post("/plans")
async def create_crawl_plan(
    body: CrawlPlanCreate,
    request: Request,
    payload: dict = Depends(verify_internal_jwt),
):
    plan_id = str(uuid.uuid4())
    tenant_id = request.state.tenant_id

    mysql = mysql_mod.get_mysql_client()
    await mysql.execute(
        """INSERT INTO crawl_plans (id, tenant_id, name, platform, keyword, category, max_count, sort_by, cron_expression, enabled, next_run_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, UTC_TIMESTAMP())""",
        (
            plan_id, tenant_id, body.name, body.platform, body.keyword,
            body.category, body.max_count, body.sort_by, body.cron_expression,
        ),
    )

    return success_response({"plan_id": plan_id})


@router.get("/plans")
async def list_crawl_plans(
    request: Request,
    payload: dict = Depends(verify_internal_jwt),
    page: int = 1,
    page_size: int = 20,
):
    tenant_id = request.state.tenant_id

    mysql = mysql_mod.get_mysql_client()
    count_row = await mysql.fetchone(
        "SELECT COUNT(*) as cnt FROM crawl_plans WHERE tenant_id = %s", (tenant_id,)
    )
    total = count_row["cnt"] if count_row else 0

    offset = (page - 1) * page_size
    rows = await mysql.fetchall(
        "SELECT * FROM crawl_plans WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (tenant_id, page_size, offset),
    )

    items = []
    for r in rows:
        items.append({
            "plan_id": r["id"],
            "tenant_id": r["tenant_id"],
            "name": r["name"],
            "platform": r["platform"],
            "keyword": r["keyword"],
            "category": r["category"],
            "max_count": r["max_count"],
            "sort_by": r["sort_by"],
            "cron_expression": r["cron_expression"],
            "enabled": bool(r["enabled"]),
            "last_run_at": str(r["last_run_at"]) if r.get("last_run_at") else "",
            "next_run_at": str(r["next_run_at"]) if r.get("next_run_at") else "",
            "created_at": str(r["created_at"]) if r.get("created_at") else "",
            "updated_at": str(r["updated_at"]) if r.get("updated_at") else "",
        })

    return paginated_response(items=items, total=total, page=page, page_size=page_size)


@router.put("/plans/{plan_id}")
async def update_crawl_plan(
    plan_id: str,
    body: CrawlPlanUpdate,
    request: Request,
    payload: dict = Depends(verify_internal_jwt),
):
    tenant_id = request.state.tenant_id

    mysql = mysql_mod.get_mysql_client()
    existing = await mysql.fetchone(
        "SELECT * FROM crawl_plans WHERE id = %s AND tenant_id = %s",
        (plan_id, tenant_id),
    )
    if not existing:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")

    fields: list[str] = []
    values: list = []
    for field_name in ("name", "keyword", "max_count", "sort_by", "cron_expression", "enabled"):
        val = getattr(body, field_name)
        if val is not None:
            fields.append(f"{field_name} = %s")
            if field_name == "enabled":
                values.append(1 if val else 0)
            else:
                values.append(val)

    if fields:
        values.append(plan_id)
        values.append(tenant_id)
        await mysql.execute(
            f"UPDATE crawl_plans SET {', '.join(fields)} WHERE id = %s AND tenant_id = %s",
            tuple(values),
        )

    return success_response({"plan_id": plan_id})


@router.delete("/plans/{plan_id}")
async def delete_crawl_plan(
    plan_id: str,
    request: Request,
    payload: dict = Depends(verify_internal_jwt),
):
    tenant_id = request.state.tenant_id

    mysql = mysql_mod.get_mysql_client()
    affected = await mysql.execute(
        "DELETE FROM crawl_plans WHERE id = %s AND tenant_id = %s",
        (plan_id, tenant_id),
    )
    if affected == 0:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")

    return success_response({"plan_id": plan_id})


def _parse_redis_url():
    import os as _os
    raw = _os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
    stripped = raw.replace("redis://", "").replace("rediss://", "")
    if "@" in stripped:
        auth_host = stripped.split("@")
        host_port = auth_host[-1]
    else:
        host_port = stripped
    parts = host_port.split("/")
    db = int(parts[-1]) if parts[-1].isdigit() else 0
    hp = parts[0]
    if ":" in hp:
        host, port_str = hp.rsplit(":", 1)
        port = int(port_str)
    else:
        host = hp
        port = 6379
    return host, port, db
