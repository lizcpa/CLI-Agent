from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from contextlib import asynccontextmanager

from fastapi import FastAPI

from common_sdk.health import build_health_router
from common_sdk.logger import get_logger
from common_sdk.middleware import RateLimitMiddleware
from common_sdk.metrics import setup_metrics
from common_sdk.tracing import setup_tracing
from db_clients.mysql import get_mysql_client

from .routes import router
from .subscriber import hot_score_subscriber

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("pipeline_orchestrator_starting")
    try:
        await get_mysql_client().create_pool()
    except Exception as e:
        logger.warning("mysql_init_failed", error=str(e))

    await hot_score_subscriber.start()

    yield

    await hot_score_subscriber.stop()
    logger.info("pipeline_orchestrator_stopped")


app = FastAPI(
    title="ProdVideo AI Factory - Pipeline Orchestrator",
    description="流水线编排服务。协调采集→分析→AI生成→视频合成→发布的完整爆品视频生产流程。支持 DAG 编排、并行生成、幂等重试、状态追踪。",
    version="1.0.0",
    contact={"name": "ProdVideo Team"},
    lifespan=lifespan,
)
app.add_middleware(RateLimitMiddleware)
app.include_router(router)


async def _check_ready() -> dict[str, bool]:
    try:
        mysql_ok = await get_mysql_client().ping()
    except Exception:
        mysql_ok = False
    return {"mysql": mysql_ok}


app.include_router(build_health_router("pipeline-orchestrator", check_ready=_check_ready))
setup_metrics(app, "pipeline-orchestrator")
setup_tracing(app, "pipeline-orchestrator")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8008)
