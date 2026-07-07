import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import json
import time
import uuid
import random
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import httpx
import jwt as jwt_lib

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from utils.common_sdk.auth import create_api_key, verify_api_key, get_tenant_id_from_api_key
from utils.common_sdk.response import success_response, error_response, paginated_response
from utils.common_sdk.vault_client import vault_client
from utils.db_clients.mysql import get_mysql_client
from utils.db_clients.redis import get_redis_client
from utils.platform_connectors.oauth import OAuthFlow

from .auth import verify_admin_request
from .config import JWT_SECRET
from .models import DashboardStats, ApiKeyCreate, ApiKeyInfo, PlatformOAuthUrl, TenantConfigUpdate
from . import agent_executor

router = APIRouter(prefix="/api/v1")


@router.post("/auth/login")
async def dev_login(payload: dict = Body(...)):
    username = payload.get("username", "")
    password = payload.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")
    now = int(time.time())
    token = jwt_lib.encode(
        {"sub": f"admin:{username}", "iat": now, "exp": now + 86400},
        JWT_SECRET,
        algorithm="HS256",
    )
    return JSONResponse(content=success_response({
        "token": token,
        "username": username,
        "tenantId": "default",
    }).model_dump())


@router.get("/auth/verify", dependencies=[Depends(verify_admin_request)])
async def verify_token(request: Request):
    return JSONResponse(content=success_response({
        "service": getattr(request.state, "service_name", "unknown"),
        "tenant_id": getattr(request.state, "tenant_id", "default"),
    }).model_dump())


@router.get("/dashboard", dependencies=[Depends(verify_admin_request)])
async def get_dashboard():
    mysql = get_mysql_client()
    total_products = await mysql.fetchone("SELECT COUNT(*) AS c FROM products") or {"c": 0}
    hot_products = await mysql.fetchone("SELECT COUNT(*) AS c FROM products WHERE tier='hot'") or {"c": 0}
    active_pipelines = await mysql.fetchone(
        "SELECT COUNT(*) AS c FROM generation_pipelines WHERE stage NOT IN ('completed','failed')"
    ) or {"c": 0}
    total_publishes = await mysql.fetchone("SELECT COUNT(*) AS c FROM publish_log") or {"c": 0}
    cost_row = await mysql.fetchone("SELECT COALESCE(SUM(estimated_cost_usd),0) AS c FROM model_usage_log") or {"c": 0}
    usage_rows = await mysql.fetchall(
        "SELECT adapter_id, COUNT(*) AS cnt FROM model_usage_log GROUP BY adapter_id"
    ) or []
    model_usage = {row["adapter_id"]: row["cnt"] for row in usage_rows} if usage_rows else {}

    stats = DashboardStats(
        total_products=total_products["c"],
        hot_products=hot_products["c"],
        active_pipelines=active_pipelines["c"],
        total_publishes=total_publishes["c"],
        total_cost=round(float(cost_row["c"]), 2),
        model_usage=model_usage,
    )
    return JSONResponse(content=success_response(stats.model_dump()).model_dump())


@router.post("/api-keys", dependencies=[Depends(verify_admin_request)])
async def create_api_key_route(body: ApiKeyCreate, request: Request = None):

    tenant_id = getattr(request.state, "tenant_id", "default") if request else "default"
    key_raw, key_hash = create_api_key(tenant_id, body.scopes)
    key_prefix = key_raw[:12]
    mysql = get_mysql_client()
    await mysql.execute(
        "INSERT INTO api_keys (tenant_id, name, api_key_hash, prefix, scopes, max_concurrency, enabled) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            tenant_id,
            body.name,
            key_hash,
            key_prefix,
            json.dumps(body.scopes) if body.scopes else None,
            body.max_concurrency,
            True,
        ),
    )
    return JSONResponse(
        content=success_response({
            "api_key": key_raw,
            "prefix": key_prefix,
            "name": body.name,
        }).model_dump(),
        status_code=201,
    )


@router.get("/api-keys", dependencies=[Depends(verify_admin_request)])
async def list_api_keys(request: Request = None):

    tenant_id = getattr(request.state, "tenant_id", "default") if request else "default"
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT id, tenant_id, name, prefix, scopes, enabled, max_concurrency, last_used_at, created_at FROM api_keys WHERE tenant_id = %s ORDER BY created_at DESC",
        (tenant_id,),
    )
    keys = []
    for r in rows:
        try:
            scopes = json.loads(r["scopes"]) if r["scopes"] else None
        except (TypeError, json.JSONDecodeError):
            scopes = None
        keys.append(ApiKeyInfo(
            id=r["id"],
            name=r["name"],
            prefix=r["prefix"],
            scopes=scopes,
            enabled=bool(r["enabled"]),
            last_used_at=str(r["last_used_at"]) if r["last_used_at"] else None,
            created_at=str(r["created_at"]) if r["created_at"] else "",
        ).model_dump())
    return JSONResponse(content=success_response(keys).model_dump())


@router.patch("/api-keys/{key_id}/toggle", dependencies=[Depends(verify_admin_request)])
async def toggle_api_key(key_id: int, request: Request = None):

    tenant_id = getattr(request.state, "tenant_id", "default") if request else "default"
    mysql = get_mysql_client()
    row = await mysql.fetchone("SELECT enabled FROM api_keys WHERE id = %s AND tenant_id = %s", (key_id, tenant_id))
    if not row:
        return JSONResponse(content=error_response(404, "API key not found").model_dump(), status_code=404)
    new_state = not bool(row["enabled"])
    await mysql.execute("UPDATE api_keys SET enabled = %s WHERE id = %s", (int(new_state), key_id))
    return JSONResponse(content=success_response({"id": key_id, "enabled": new_state}).model_dump())


@router.delete("/api-keys/{key_id}", dependencies=[Depends(verify_admin_request)])
async def delete_api_key(key_id: int, request: Request = None):

    tenant_id = getattr(request.state, "tenant_id", "default") if request else "default"
    mysql = get_mysql_client()
    row = await mysql.fetchone("SELECT id FROM api_keys WHERE id = %s AND tenant_id = %s", (key_id, tenant_id))
    if not row:
        return JSONResponse(content=error_response(404, "API key not found").model_dump(), status_code=404)
    await mysql.execute("DELETE FROM api_keys WHERE id = %s", (key_id,))
    return JSONResponse(content=success_response({"deleted": key_id}).model_dump())


@router.get("/tenant/config", dependencies=[Depends(verify_admin_request)])
async def get_tenant_config(request: Request = None):

    tenant_id = getattr(request.state, "tenant_id", "default") if request else "default"
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT config_key, config_value FROM tenant_config WHERE tenant_id = %s",
        (tenant_id,),
    )
    config = {r["config_key"]: r["config_value"] for r in rows}
    return JSONResponse(content=success_response({"tenant_id": tenant_id, "config": config}).model_dump())


@router.put("/tenant/config", dependencies=[Depends(verify_admin_request)])
async def update_tenant_config(body: TenantConfigUpdate, request: Request = None):

    tenant_id = getattr(request.state, "tenant_id", "default") if request else "default"
    mysql = get_mysql_client()
    existing = await mysql.fetchone(
        "SELECT id FROM tenant_config WHERE tenant_id = %s AND config_key = %s",
        (tenant_id, body.config_key),
    )
    if existing:
        await mysql.execute(
            "UPDATE tenant_config SET config_value = %s, updated_at = NOW() WHERE id = %s",
            (body.config_value, existing["id"]),
        )
    else:
        await mysql.execute(
            "INSERT INTO tenant_config (tenant_id, config_key, config_value) VALUES (%s, %s, %s)",
            (tenant_id, body.config_key, body.config_value),
        )
    return JSONResponse(content=success_response({"config_key": body.config_key, "config_value": body.config_value}).model_dump())


@router.get("/platforms/auth-url")
async def get_platform_auth_url(
    platform: str = Query(..., description="Platform name"),
    tenant_id: str = Query("default"),
):
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT config_key, config_value FROM platform_config WHERE platform = %s",
        (platform,),
    )
    if not rows:
        return JSONResponse(
            content=error_response(404, f"no config for platform {platform}").model_dump(),
            status_code=404,
        )
    cfg = {r["config_key"]: r["config_value"] for r in rows}
    state = uuid.uuid4().hex
    redis_client = get_redis_client()
    await redis_client.set(
        f"oauth:state:{state}",
        json.dumps({"platform": platform, "tenant_id": tenant_id, "created_at": time.time()}),
        ex=600,
    )
    flow = OAuthFlow(platform, cfg)
    auth_url = flow.build_auth_url(state)
    return JSONResponse(
        content=success_response(
            PlatformOAuthUrl(platform=platform, auth_url=auth_url).model_dump()
        ).model_dump()
    )


@router.post("/platforms/callback", dependencies=[Depends(verify_admin_request)])
async def platform_oauth_callback(payload: dict = Body(...), request: Request = None):
    platform = payload.get("platform")
    code = payload.get("code")
    state = payload.get("state", "")
    if not platform or not code:
        return JSONResponse(
            content=error_response(400, "platform and code are required").model_dump(),
            status_code=400,
        )
    tenant_id = getattr(request.state, "tenant_id", "default") if request else payload.get("tenant_id", "default")
    redis_client = get_redis_client()
    state_raw = await redis_client.get(f"oauth:state:{state}")
    if not state_raw:
        return JSONResponse(
            content=error_response(400, "invalid or expired state").model_dump(),
            status_code=400,
        )
    await redis_client.delete(f"oauth:state:{state}")
    try:
        state_data = json.loads(state_raw)
    except (TypeError, json.JSONDecodeError):
        state_data = {}
    if state_data.get("platform") != platform:
        return JSONResponse(
            content=error_response(400, "platform mismatch").model_dump(),
            status_code=400,
        )
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT config_key, config_value FROM platform_config WHERE platform = %s",
        (platform,),
    )
    if not rows:
        return JSONResponse(
            content=error_response(404, f"no config for platform {platform}").model_dump(),
            status_code=404,
        )
    cfg = {r["config_key"]: r["config_value"] for r in rows}
    flow = OAuthFlow(platform, cfg)
    try:
        token_data = await flow.exchange_code(code)
    except Exception as e:
        return JSONResponse(
            content=error_response(502, f"token exchange failed: {e}").model_dump(),
            status_code=502,
        )
    refresh_token = token_data.get("refresh_token") or ""
    open_id = token_data.get("open_id") or ""
    expires_in = int(token_data.get("expires_in") or 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    try:
        await vault_client.store_platform_refresh_token(
            platform,
            tenant_id,
            refresh_token,
            extra={"open_id": open_id, "expires_in": expires_in},
        )
    except Exception:
        pass
    await mysql.execute(
        "INSERT INTO platform_authorizations "
        "(tenant_id, platform, platform_user_id, token_encrypted, access_token_expires_at, status) "
        "VALUES (%s, %s, %s, %s, %s, 'active') "
        "ON DUPLICATE KEY UPDATE "
        "token_encrypted = VALUES(token_encrypted), "
        "platform_user_id = VALUES(platform_user_id), "
        "access_token_expires_at = VALUES(access_token_expires_at), status = 'active'",
        (tenant_id, platform, open_id or None, refresh_token, expires_at),
    )
    return JSONResponse(
        content=success_response(
            {"platform": platform, "authorized": True, "open_id": open_id}
        ).model_dump()
    )


@router.get("/platforms/authorized", dependencies=[Depends(verify_admin_request)])
async def list_authorized_platforms(request: Request = None):

    tenant_id = getattr(request.state, "tenant_id", "default") if request else "default"
    mysql = get_mysql_client()
    rows = await mysql.fetchall(
        "SELECT platform, platform_user_id, status, access_token_expires_at, created_at, updated_at "
        "FROM platform_authorizations WHERE tenant_id = %s ORDER BY created_at DESC",
        (tenant_id,),
    )
    platforms = [
        {
            "platform": r["platform"],
            "platform_user_id": r.get("platform_user_id") or "",
            "status": r.get("status") or "active",
            "authorized_at": str(r["created_at"]) if r["created_at"] else "",
            "expires_at": str(r["access_token_expires_at"]) if r.get("access_token_expires_at") else "",
            "updated_at": str(r["updated_at"]) if r["updated_at"] else "",
        }
        for r in rows
    ]
    return JSONResponse(content=success_response(platforms).model_dump())


@router.get("/models", dependencies=[Depends(verify_admin_request)])
async def list_models(request: Request = None):
    auth = request.headers.get("Authorization", "")
    headers = {"Authorization": auth, "X-Tenant-ID": getattr(request.state, "tenant_id", "default")}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("http://localhost:8003/api/v1/models", headers=headers)
            return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except Exception as e:
        return JSONResponse(
            content=error_response(502, f"upstream service unavailable: {type(e).__name__}").model_dump(),
            status_code=502,
        )


@router.get("/platform-configs", dependencies=[Depends(verify_admin_request)])
async def list_platform_configs(platform: str | None = Query(None)):
    mysql = get_mysql_client()
    if platform:
        rows = await mysql.fetchall(
            "SELECT id, platform, config_key, config_value, description, created_at, updated_at FROM platform_config WHERE platform = %s ORDER BY id",
            (platform,),
        )
    else:
        rows = await mysql.fetchall(
            "SELECT id, platform, config_key, config_value, description, created_at, updated_at FROM platform_config ORDER BY id"
        )
    configs = []
    for r in rows:
        configs.append({
            "id": r["id"],
            "platform": r["platform"],
            "config_key": r["config_key"],
            "config_value": r["config_value"],
            "description": r.get("description"),
            "created_at": str(r["created_at"]) if r.get("created_at") else "",
            "updated_at": str(r["updated_at"]) if r.get("updated_at") else "",
        })
    return JSONResponse(content=success_response(configs).model_dump())


@router.put("/platform-configs/{platform}", dependencies=[Depends(verify_admin_request)])
async def update_platform_config(platform: str, config_key: str = Query(...), config_value: str = Query(...), description: str | None = Query(None)):
    mysql = get_mysql_client()
    existing = await mysql.fetchone(
        "SELECT id FROM platform_config WHERE platform = %s AND config_key = %s",
        (platform, config_key),
    )
    if existing:
        await mysql.execute(
            "UPDATE platform_config SET config_value = %s, description = %s, updated_at = NOW() WHERE id = %s",
            (config_value, description, existing["id"]),
        )
    else:
        await mysql.execute(
            "INSERT INTO platform_config (platform, config_key, config_value, description) VALUES (%s, %s, %s, %s)",
            (platform, config_key, config_value, description),
        )
    return JSONResponse(content=success_response({"platform": platform, "config_key": config_key, "config_value": config_value}).model_dump())


@router.get("/tasks", dependencies=[Depends(verify_admin_request)])
async def list_tasks():
    redis_client = get_redis_client()
    try:
        keys = []
        async for key in redis_client.scan_iter(match="task:*", count=50):
            keys.append(key)
            if len(keys) >= 50:
                break
    except Exception:
        return JSONResponse(content=success_response([]).model_dump())
    tasks = []
    for key in keys[:50]:
        raw = await redis_client.get(key)
        if raw:
            try:
                data = json.loads(raw)
                data["task_id"] = key.replace("task:", "")
                tasks.append(data)
            except json.JSONDecodeError:
                pass
    return JSONResponse(content=success_response(tasks).model_dump())


# ==================== BFF Proxy + Direct Query Endpoints ====================

async def _proxy(request: Request, base_url: str, path: str, method: str = "GET", json_body: dict | None = None):
    auth = request.headers.get("Authorization", "")
    tenant_id = getattr(request.state, "tenant_id", "default")
    headers = {"Authorization": auth, "X-Tenant-ID": tenant_id, "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method, f"{base_url}{path}", headers=headers, json=json_body)
            return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except Exception as e:
        return JSONResponse(
            content=error_response(502, f"upstream service unavailable: {type(e).__name__}").model_dump(),
            status_code=502,
        )


# --- Products (direct MySQL query) ---

@router.get("/products", dependencies=[Depends(verify_admin_request)])
async def list_products(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    mysql = get_mysql_client()
    offset = (page - 1) * page_size
    rows = await mysql.fetchall(
        "SELECT id, platform, platform_product_id, title, price, sales_count, rating, category, score, tier, status, created_at, updated_at "
        "FROM products ORDER BY updated_at DESC LIMIT %s OFFSET %s",
        (page_size, offset),
    )
    total_row = await mysql.fetchone("SELECT COUNT(*) AS c FROM products") or {"c": 0}
    items = []
    for r in rows:
        items.append({
            "id": r["id"], "platform": r["platform"], "platform_product_id": r["platform_product_id"],
            "title": r["title"], "price": float(r["price"]) if r["price"] else 0,
            "sales_count": r["sales_count"], "rating": float(r["rating"]) if r["rating"] else 0,
            "category": r["category"] or "", "score": float(r["score"]) if r["score"] else 0,
            "tier": r["tier"] or "", "status": r["status"], 
            "created_at": str(r["created_at"]) if r.get("created_at") else "",
            "updated_at": str(r["updated_at"]) if r.get("updated_at") else "",
        })
    return JSONResponse(content=paginated_response(items, total_row["c"], page, page_size).model_dump())


@router.get("/products/hot", dependencies=[Depends(verify_admin_request)])
async def list_hot_products(request: Request, limit: int = Query(50, ge=1, le=200)):
    return await _proxy(request, "http://localhost:8002", f"/api/v1/products/hot?limit={limit}")


@router.post("/analyze", dependencies=[Depends(verify_admin_request)])
async def trigger_analyze(request: Request, payload: dict = Body(...)):
    return await _proxy(request, "http://localhost:8002", "/api/v1/analyze", "POST", payload)


@router.get("/config/score", dependencies=[Depends(verify_admin_request)])
async def get_score_config(request: Request):
    return await _proxy(request, "http://localhost:8002", "/api/v1/config/score")


@router.put("/config/score", dependencies=[Depends(verify_admin_request)])
async def update_score_config(request: Request, payload: dict = Body(...)):
    return await _proxy(request, "http://localhost:8002", "/api/v1/config/score", "PUT", payload)


# --- Crawl (proxy to crawl_scheduler:8001) ---

@router.get("/crawl/jobs", dependencies=[Depends(verify_admin_request)])
async def list_crawl_jobs(request: Request, page: int = Query(1), page_size: int = Query(20)):
    return await _proxy(request, "http://localhost:8001", f"/api/v1/crawl/jobs?page={page}&page_size={page_size}")


@router.post("/crawl/jobs", dependencies=[Depends(verify_admin_request)])
async def create_crawl_job(request: Request, payload: dict = Body(...)):
    return await _proxy(request, "http://localhost:8001", "/api/v1/crawl/jobs", "POST", payload)


@router.get("/crawl/plans", dependencies=[Depends(verify_admin_request)])
async def list_crawl_plans(request: Request, page: int = Query(1), page_size: int = Query(20)):
    return await _proxy(request, "http://localhost:8001", f"/api/v1/crawl/plans?page={page}&page_size={page_size}")


@router.post("/crawl/plans", dependencies=[Depends(verify_admin_request)])
async def create_crawl_plan(request: Request, payload: dict = Body(...)):
    return await _proxy(request, "http://localhost:8001", "/api/v1/crawl/plans", "POST", payload)


@router.put("/crawl/plans/{plan_id}", dependencies=[Depends(verify_admin_request)])
async def update_crawl_plan(request: Request, plan_id: str, payload: dict = Body(...)):
    return await _proxy(request, "http://localhost:8001", f"/api/v1/crawl/plans/{plan_id}", "PUT", payload)


@router.delete("/crawl/plans/{plan_id}", dependencies=[Depends(verify_admin_request)])
async def delete_crawl_plan(request: Request, plan_id: str):
    return await _proxy(request, "http://localhost:8001", f"/api/v1/crawl/plans/{plan_id}", "DELETE")


# --- AI Generation (proxy to ai_generation:8003) ---

@router.post("/copywriting", dependencies=[Depends(verify_admin_request)])
async def generate_copywriting(request: Request, payload: dict = Body(...)):
    return await _proxy(request, "http://localhost:8003", "/api/v1/copywriting", "POST", payload)


@router.post("/images/generate", dependencies=[Depends(verify_admin_request)])
async def generate_images(request: Request, payload: dict = Body(...)):
    return await _proxy(request, "http://localhost:8003", "/api/v1/images/generate", "POST", payload)


@router.post("/videos/generate", dependencies=[Depends(verify_admin_request)])
async def generate_video_clips(request: Request, payload: dict = Body(...)):
    return await _proxy(request, "http://localhost:8003", "/api/v1/videos/generate", "POST", payload)


@router.get("/ai/tasks/{task_id}/result", dependencies=[Depends(verify_admin_request)])
async def get_ai_task_result(request: Request, task_id: str):
    return await _proxy(request, "http://localhost:8003", f"/api/v1/internal/tasks/{task_id}/result")


# --- Video Composer (proxy to video_composer:8004) ---

@router.get("/compose", dependencies=[Depends(verify_admin_request)])
async def list_compose_tasks(request: Request):
    return await _proxy(request, "http://localhost:8004", "/api/v1/compose")


@router.post("/compose", dependencies=[Depends(verify_admin_request)])
async def compose_video(request: Request, payload: dict = Body(...)):
    return await _proxy(request, "http://localhost:8004", "/api/v1/compose", "POST", payload)


@router.get("/compose/{task_id}", dependencies=[Depends(verify_admin_request)])
async def get_compose_status(request: Request, task_id: str):
    return await _proxy(request, "http://localhost:8004", f"/api/v1/compose/{task_id}")


# --- Publish (proxy to publish_dispatcher:8005) ---

@router.post("/publish", dependencies=[Depends(verify_admin_request)])
async def publish_content(request: Request, payload: dict = Body(...)):
    return await _proxy(request, "http://localhost:8005", "/api/v1/publish", "POST", payload)


@router.get("/publish/logs", dependencies=[Depends(verify_admin_request)])
async def list_publish_logs(page: int = Query(1), page_size: int = Query(20)):
    mysql = get_mysql_client()
    offset = (page - 1) * page_size
    rows = await mysql.fetchall(
        "SELECT id, pipeline_id, platform, platform_post_id, status, public_url, error_message, created_at "
        "FROM publish_log ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (page_size, offset),
    )
    total_row = await mysql.fetchone("SELECT COUNT(*) AS c FROM publish_log") or {"c": 0}
    items = []
    for r in rows:
        items.append({
            "id": r["id"], "pipeline_id": r["pipeline_id"] or "", "platform": r["platform"],
            "platform_post_id": r["platform_post_id"] or "", "status": r["status"],
            "public_url": r["public_url"] or "", "error_message": r["error_message"] or "",
            "created_at": str(r["created_at"]) if r.get("created_at") else "",
        })
    return JSONResponse(content=paginated_response(items, total_row["c"], page, page_size).model_dump())


@router.get("/platforms/authorized-list", dependencies=[Depends(verify_admin_request)])
async def list_authorized_platforms_proxy(request: Request):
    return await _proxy(request, "http://localhost:8005", "/api/v1/platforms")


# --- Pipelines (proxy to pipeline_orchestrator:8008) ---

@router.get("/pipelines", dependencies=[Depends(verify_admin_request)])
async def list_pipelines(page: int = Query(1), page_size: int = Query(20)):
    mysql = get_mysql_client()
    offset = (page - 1) * page_size
    rows = await mysql.fetchall(
        "SELECT id, product_id, tenant_id, stage, created_at, updated_at "
        "FROM generation_pipelines ORDER BY created_at DESC LIMIT %s OFFSET %s",
        (page_size, offset),
    )
    total_row = await mysql.fetchone("SELECT COUNT(*) AS c FROM generation_pipelines") or {"c": 0}
    items = []
    for r in rows:
        items.append({
            "id": r["id"], "product_id": r["product_id"], "tenant_id": r["tenant_id"],
            "stage": r["stage"] or "", "status": r["stage"] or "",
            "created_at": str(r["created_at"]) if r.get("created_at") else "",
            "updated_at": str(r["updated_at"]) if r.get("updated_at") else "",
        })
    return JSONResponse(content=paginated_response(items, total_row["c"], page, page_size).model_dump())


@router.post("/pipelines", dependencies=[Depends(verify_admin_request)])
async def create_pipeline(request: Request, payload: dict = Body(...)):
    return await _proxy(request, "http://localhost:8008", "/api/v1/pipelines", "POST", payload)


@router.get("/pipelines/{pipeline_id}", dependencies=[Depends(verify_admin_request)])
async def get_pipeline_detail(request: Request, pipeline_id: int):
    return await _proxy(request, "http://localhost:8008", f"/api/v1/pipelines/{pipeline_id}")


# ===== Agent Orchestrator =====

@router.get("/agent/tools", dependencies=[Depends(verify_admin_request)])
async def agent_list_tools():
    try:
        await agent_executor.ensure_tables()
    except Exception:
        pass
    tools = await agent_executor.list_agent_tools()
    return JSONResponse(content=success_response(tools).model_dump())


@router.post("/agent/tools", dependencies=[Depends(verify_admin_request)])
async def agent_add_tool(payload: dict = Body(...)):
    tool_id = payload.get("id") or f"tool-{uuid.uuid4().hex[:8]}"
    name = payload.get("name", "")
    cli_command = payload.get("cli_command", "")
    description = payload.get("description", "")
    if not name or not cli_command:
        raise HTTPException(status_code=400, detail="name and cli_command required")
    tool = await agent_executor.add_agent_tool(tool_id, name, cli_command, description)
    return JSONResponse(content=success_response(tool).model_dump())


@router.get("/agent/models", dependencies=[Depends(verify_admin_request)])
async def agent_list_models():
    try:
        await agent_executor.ensure_tables()
    except Exception:
        pass
    models = await agent_executor.list_models()
    return JSONResponse(content=success_response(models).model_dump())


@router.post("/agent/models/{model_id}/key", dependencies=[Depends(verify_admin_request)])
async def agent_save_model_key(model_id: str, payload: dict = Body(...)):
    api_key = payload.get("api_key", "")
    base_url = payload.get("base_url", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key required")
    await agent_executor.save_model_key(model_id, api_key, base_url)
    return JSONResponse(content=success_response({"model_id": model_id, "saved": True}).model_dump())


@router.get("/agent/models/{model_id}/key", dependencies=[Depends(verify_admin_request)])
async def agent_check_model_key(model_id: str):
    key = await agent_executor.get_model_key(model_id)
    return JSONResponse(content=success_response({"model_id": model_id, "has_key": bool(key)}).model_dump())


@router.post("/agent/execute", dependencies=[Depends(verify_admin_request)])
async def agent_execute(request: Request, payload: dict = Body(...)):
    agent_tool_id = payload.get("agent_tool_id", "")
    model_id = payload.get("model_id", "")
    task_instruction = payload.get("task_instruction", "")
    if not agent_tool_id or not model_id or not task_instruction:
        raise HTTPException(status_code=400, detail="agent_tool_id, model_id, task_instruction required")

    tenant_id = getattr(request.state, "tenant_id", "default")

    tools = await agent_executor.list_agent_tools()
    tool = next((t for t in tools if t["id"] == agent_tool_id), None)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Agent tool '{agent_tool_id}' not found")
    if not tool.get("enabled"):
        raise HTTPException(status_code=400, detail=f"Agent tool '{agent_tool_id}' is disabled")

    model_info = await agent_executor.get_model_info(model_id)
    if not model_info:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    api_key = await agent_executor.get_model_key(model_id)
    env_var_name = model_info.get("env_var_name", "API_KEY")
    base_url = model_info.get("base_url", "")

    task_id = await agent_executor.create_task(
        tenant_id=tenant_id,
        agent_tool_id=agent_tool_id,
        agent_tool_name=tool["name"],
        cli_template=tool["cli_command"],
        model_id=model_id,
        model_name=model_info.get("model_name", model_id),
        task_instruction=task_instruction,
        env_var_name=env_var_name,
        api_key=api_key,
        base_url=base_url,
    )
    return JSONResponse(content=success_response({"task_id": task_id, "status": "running"}).model_dump())


@router.get("/agent/tasks", dependencies=[Depends(verify_admin_request)])
async def agent_list_tasks(request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    tasks = await agent_executor.list_tasks(tenant_id)
    return JSONResponse(content=success_response(tasks).model_dump())


@router.get("/agent/tasks/{task_id}", dependencies=[Depends(verify_admin_request)])
async def agent_get_task(task_id: str):
    task = await agent_executor.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return JSONResponse(content=success_response(task).model_dump())


@router.get("/agent/tasks/{task_id}/output", dependencies=[Depends(verify_admin_request)])
async def agent_get_task_output(task_id: str):
    output = await agent_executor.get_task_output(task_id)
    task = await agent_executor.get_task(task_id)
    status = task["status"] if task else "unknown"
    return JSONResponse(content=success_response({"task_id": task_id, "status": status, "output": output}).model_dump())


@router.post("/agent/tasks/{task_id}/cancel", dependencies=[Depends(verify_admin_request)])
async def agent_cancel_task(task_id: str):
    cancelled = await agent_executor.cancel_task(task_id)
    return JSONResponse(content=success_response({"task_id": task_id, "cancelled": cancelled}).model_dump())


# ===== Scheduled / recurring tasks =====

@router.get("/agent/scheduled", dependencies=[Depends(verify_admin_request)])
async def agent_list_scheduled(request: Request):
    tenant_id = getattr(request.state, "tenant_id", "default")
    tasks = await agent_executor.list_scheduled_tasks(tenant_id)
    return JSONResponse(content=success_response(tasks).model_dump())


@router.post("/agent/scheduled", dependencies=[Depends(verify_admin_request)])
async def agent_create_scheduled(request: Request, payload: dict = Body(...)):
    agent_tool_id = payload.get("agent_tool_id", "")
    model_id = payload.get("model_id", "")
    task_instruction = payload.get("task_instruction", "")
    interval_seconds = int(payload.get("interval_seconds", 3600))
    if not agent_tool_id or not model_id or not task_instruction:
        raise HTTPException(status_code=400, detail="agent_tool_id, model_id, task_instruction required")
    if interval_seconds < 60:
        raise HTTPException(status_code=400, detail="interval_seconds must be >= 60")

    tenant_id = getattr(request.state, "tenant_id", "default")
    tools = await agent_executor.list_agent_tools()
    tool = next((t for t in tools if t["id"] == agent_tool_id), None)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Agent tool '{agent_tool_id}' not found")

    model_info = await agent_executor.get_model_info(model_id)
    if not model_info:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

    api_key = await agent_executor.get_model_key(model_id)
    result = await agent_executor.create_scheduled_task(
        tenant_id=tenant_id,
        agent_tool_id=agent_tool_id,
        agent_tool_name=tool["name"],
        cli_template=tool["cli_command"],
        model_id=model_id,
        model_name=model_info.get("model_name", model_id),
        task_instruction=task_instruction,
        env_var_name=model_info.get("env_var_name", "API_KEY"),
        api_key=api_key,
        base_url=model_info.get("base_url", ""),
        interval_seconds=interval_seconds,
    )
    return JSONResponse(content=success_response(result).model_dump())


@router.put("/agent/scheduled/{sched_id}", dependencies=[Depends(verify_admin_request)])
async def agent_toggle_scheduled(sched_id: str, payload: dict = Body(...)):
    enabled = bool(payload.get("enabled", True))
    await agent_executor.toggle_scheduled_task(sched_id, enabled)
    return JSONResponse(content=success_response({"id": sched_id, "enabled": enabled}).model_dump())


@router.delete("/agent/scheduled/{sched_id}", dependencies=[Depends(verify_admin_request)])
async def agent_delete_scheduled(sched_id: str):
    await agent_executor.delete_scheduled_task(sched_id)
    return JSONResponse(content=success_response({"id": sched_id, "deleted": True}).model_dump())


