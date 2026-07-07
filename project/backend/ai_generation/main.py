from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from common_sdk.exceptions import AppException, ServiceException, app_exception_handler
from common_sdk.health import build_health_router
from common_sdk.logger import get_logger
from common_sdk.metrics import setup_metrics
from common_sdk.tracing import setup_tracing
from db_clients.redis import get_redis_client
from db_clients.mysql import get_mysql_client
from db_clients.minio import get_minio_client

from .config import (
    SERVICE_NAME,
    SERVICE_PORT,
    REDIS_HOST,
    REDIS_PORT,
    REDIS_PASSWORD,
    REDIS_DB,
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_USER,
    MYSQL_PASSWORD,
    MYSQL_DATABASE,
    MINIO_ENDPOINT,
    MINIO_ACCESS_KEY,
    MINIO_SECRET_KEY,
)
from .registry_manager import RegistryManager
from .router import ModelRouterService
from .routes import router

logger = get_logger(__name__)

_router_service: ModelRouterService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _router_service

    logger.info("ai_generation_lifespan_startup", port=SERVICE_PORT)

    redis_client = get_redis_client()
    await redis_client.connect(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        db=REDIS_DB,
    )
    logger.info("redis_connected")

    mysql_client = get_mysql_client()
    await mysql_client.create_pool(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
    )

    await _ensure_usage_log_table(mysql_client)
    logger.info("mysql_connected")

    minio_client = get_minio_client()
    minio_client.connect(
        endpoint=MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
    )
    logger.info("minio_connected")

    reg_manager = RegistryManager()
    reg_manager.register_default_adapters()
    _router_service = ModelRouterService(reg_manager.registry)
    logger.info("adapters_registered", count=len(reg_manager.registry.list_adapters()))

    yield

    await redis_client.close()
    await mysql_client.close()
    minio_client.close()
    logger.info("ai_generation_lifespan_shutdown")


async def _ensure_usage_log_table(mysql_client) -> None:
    sql = """
        CREATE TABLE IF NOT EXISTS model_usage_log (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            adapter_id VARCHAR(128) NOT NULL,
            adapter_type VARCHAR(32) NOT NULL,
            model VARCHAR(128) NOT NULL,
            pipeline_id VARCHAR(128) DEFAULT '',
            tenant_id VARCHAR(128) DEFAULT '',
            input_tokens INT DEFAULT 0,
            output_tokens INT DEFAULT 0,
            image_count INT DEFAULT 0,
            duration_seconds DOUBLE DEFAULT 0,
            estimated_cost DOUBLE DEFAULT 0,
            status VARCHAR(16) DEFAULT 'success',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_adapter (adapter_id),
            INDEX idx_type (adapter_type),
            INDEX idx_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    await mysql_client.execute(sql)
    logger.info("model_usage_log_table_ensured")


app = FastAPI(
    title="ProdVideo AI Factory - AI Generation",
    description="AI 生成编排核心服务。提供文案生成(LLM)、图片生成、视频片段生成、语音合成(TTS)四大能力。支持多模型路由、降级策略、用量计量。",
    version="1.0.0",
    contact={"name": "ProdVideo Team"},
    lifespan=lifespan,
)

app.include_router(router)
app.add_exception_handler(AppException, app_exception_handler)


async def _check_ready() -> dict[str, bool]:
    try:
        redis_ok = await get_redis_client().ping()
    except Exception:
        redis_ok = False
    try:
        mysql_ok = await get_mysql_client().ping()
    except Exception:
        mysql_ok = False
    try:
        minio_ok = get_minio_client().ping()
    except Exception:
        minio_ok = False
    return {"redis": redis_ok, "mysql": mysql_ok, "minio": minio_ok}


app.include_router(build_health_router(SERVICE_NAME, check_ready=_check_ready))
setup_metrics(app, SERVICE_NAME)
setup_tracing(app, SERVICE_NAME)


@app.get("/business_metrics", summary="Adapter health metrics")
async def business_metrics():
    if _router_service is None:
        raise ServiceException("Router service not initialized")

    metric_data = {}
    for adapter_type in ("llm", "image", "video", "tts"):
        adapters = _router_service.get_available_models(adapter_type)
        metric_data[adapter_type] = {
            "healthy_count": len(adapters),
            "models": [a["id"] for a in adapters],
        }

    return {
        "service": SERVICE_NAME,
        "version": "0.1.0",
        "adapters": metric_data,
    }


if __name__ == "__main__":
    uvicorn.run(
        "project.backend.ai_generation.main:app",
        host="0.0.0.0",
        port=SERVICE_PORT,
        reload=False,
    )
