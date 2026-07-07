import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from common_sdk.health import build_health_router
from common_sdk.middleware import RateLimitMiddleware
from common_sdk.metrics import setup_metrics
from common_sdk.tracing import setup_tracing
from db_clients.mysql import get_mysql_client

from .config import SERVICE_PORT, SERVICE_NAME
from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    from utils.db_clients import get_mysql_client
    await get_mysql_client().create_pool()
    try:
        from .worker_publishers import load_worker_publisher_configs
        await load_worker_publisher_configs()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("publisher_config_load_failed", error=str(e))
    yield
    await get_mysql_client().close()


app = FastAPI(
    title="ProdVideo AI Factory - Publish Dispatcher",
    description="视频发布编排服务。将合成后的视频发布到抖音、YouTube、TikTok、Instagram 等平台。支持 OAuth token 管理、定时发布、多平台并行发布。",
    version="1.0.0",
    contact={"name": "ProdVideo Team"},
    lifespan=lifespan,
)
# 生产环境通过 ALLOW_ORIGINS 环境变量设置具体域名（逗号分隔），开发环境默认 *
_ALLOW_ORIGINS = os.getenv("ALLOW_ORIGINS", "*").split(",")
app.add_middleware(CORSMiddleware, allow_origins=_ALLOW_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(RateLimitMiddleware)
app.include_router(router)


async def _check_ready() -> dict[str, bool]:
    try:
        mysql_ok = await get_mysql_client().ping()
    except Exception:
        mysql_ok = False
    return {"mysql": mysql_ok}


app.include_router(build_health_router(SERVICE_NAME, check_ready=_check_ready))
setup_metrics(app, SERVICE_NAME)
setup_tracing(app, SERVICE_NAME)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
