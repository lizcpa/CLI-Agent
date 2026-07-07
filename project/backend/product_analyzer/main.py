from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from utils.common_sdk.exceptions import AppException, app_exception_handler
from utils.common_sdk.health import build_health_router
from utils.common_sdk.logger import get_logger
from utils.common_sdk.metrics import setup_metrics
from utils.common_sdk.tracing import setup_tracing
from utils.db_clients import get_mysql_client, get_redis_client

from .auth import verify_internal_jwt
from .config import SERVICE_NAME, SERVICE_PORT
from .routes import router

logger = get_logger(SERVICE_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("connecting_redis")
    redis = get_redis_client()
    await redis.connect()
    logger.info("redis_connected")

    logger.info("connecting_mysql")
    mysql = get_mysql_client()
    await mysql.create_pool()
    logger.info("mysql_connected")

    yield

    logger.info("shutting_down")
    await redis.close()
    await mysql.close()
    logger.info("shutdown_complete")


app = FastAPI(
    title="ProdVideo AI Factory - Product Analyzer",
    description="商品多维度评分分析服务。基于热度(40%)、转化率(35%)、利润率(25%)给出选品决策建议(hot/normal/cold)。支持评分阈值配置、定时分析任务。",
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

app.add_exception_handler(AppException, app_exception_handler)


async def _check_ready() -> dict[str, bool]:
    redis_ok = await get_redis_client().ping()
    mysql_ok = await get_mysql_client().ping()
    return {"redis": redis_ok, "mysql": mysql_ok}


app.include_router(build_health_router(SERVICE_NAME, check_ready=_check_ready))
setup_metrics(app, SERVICE_NAME)
setup_tracing(app, SERVICE_NAME)
app.include_router(router)


@app.get("/business_metrics", summary="Business metrics (auth required)")
async def business_metrics(request: Request, _auth: dict = Depends(verify_internal_jwt)):
    redis = get_redis_client()
    mysql = get_mysql_client()

    hot_count = await redis.zcard("hot_products:daily")

    total_products = 0
    row = await mysql.fetchone("SELECT COUNT(*) as cnt FROM products WHERE status='active'")
    if row:
        total_products = row.get("cnt", 0) or 0

    return JSONResponse({
        "service": SERVICE_NAME,
        "hot_products_count": hot_count,
        "total_active_products": total_products,
    })


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "project.backend.product_analyzer.main:app",
        host="0.0.0.0",
        port=SERVICE_PORT,
        reload=False,
        log_level="info",
    )
