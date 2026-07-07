from __future__ import annotations

import sys
import time
import os
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from common_sdk.exceptions import AppException, app_exception_handler
from common_sdk.health import build_health_router
from common_sdk.middleware import RateLimitMiddleware
from common_sdk.metrics import setup_metrics
from common_sdk.tracing import setup_tracing
from common_sdk.response import APIResponse
from db_clients.mysql import get_mysql_client
from db_clients.redis import get_redis_client

from .auth import verify_internal_jwt
from .config import SERVICE_NAME, SERVICE_PORT
from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis_client = get_redis_client()
    await redis_client.connect()
    mysql_client = get_mysql_client()
    await mysql_client.create_pool()
    yield
    await redis_client.close()
    await mysql_client.close()


app = FastAPI(
    title="ProdVideo AI Factory - Crawl Scheduler",
    description="电商商品采集调度服务。支持抖音、淘宝、Amazon、Shopee 等平台的热门商品采集。提供采集计划管理、连接器注册、分布式锁防重复爬取。",
    version="1.0.0",
    contact={"name": "ProdVideo Team"},
    lifespan=lifespan,
)

# 生产环境通过 ALLOW_ORIGINS 环境变量设置具体域名（逗号分隔），开发环境默认 *
_ALLOW_ORIGINS = os.getenv("ALLOW_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)

app.add_exception_handler(AppException, app_exception_handler)


async def _check_ready() -> dict[str, bool]:
    redis_ok = await get_redis_client().ping()
    mysql_ok = await get_mysql_client().ping()
    return {"redis": redis_ok, "mysql": mysql_ok}


app.include_router(build_health_router(SERVICE_NAME, check_ready=_check_ready))
setup_metrics(app, SERVICE_NAME)
setup_tracing(app, SERVICE_NAME)


@app.middleware("http")
async def jwt_middleware(request, call_next):
    from fastapi import Request

    public_paths = {"/healthz", "/readyz", "/metrics", "/business_metrics", "/docs", "/openapi.json", "/redoc"}
    if request.url.path in public_paths:
        return await call_next(request)

    try:
        await verify_internal_jwt(request)
    except AppException as e:
        return JSONResponse(status_code=e.http_status, content={"code": e.code, "message": e.message, "data": {}})

    return await call_next(request)


app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("project.backend.crawl_scheduler.main:app", host="0.0.0.0", port=SERVICE_PORT, reload=True)
