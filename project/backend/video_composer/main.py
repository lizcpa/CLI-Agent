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
from db_clients.minio import get_minio_client
from db_clients.mysql import get_mysql_client

from .config import SERVICE_NAME, SERVICE_PORT
from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_mysql_client().create_pool()
    get_minio_client().connect()
    yield
    await get_mysql_client().close()
    get_minio_client().close()


app = FastAPI(
    title="ProdVideo AI Factory - Video Composer",
    description="视频合成服务。使用 FFmpeg 将视频片段、图片、音频、字幕合成为最终视频。支持模板驱动合成、多轨道编排、平台格式适配。",
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
    try:
        minio_ok = get_minio_client().ping()
    except Exception:
        minio_ok = False
    return {"mysql": mysql_ok, "minio": minio_ok}


app.include_router(build_health_router(SERVICE_NAME, check_ready=_check_ready))
setup_metrics(app, SERVICE_NAME)
setup_tracing(app, SERVICE_NAME)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
