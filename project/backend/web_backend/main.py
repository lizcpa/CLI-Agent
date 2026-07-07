import os
import sys
import asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from utils.common_sdk.health import build_health_router
from utils.common_sdk.metrics import setup_metrics
from utils.common_sdk.tracing import setup_tracing
from utils.db_clients.mysql import get_mysql_client
from utils.db_clients.redis import get_redis_client

from .config import SERVICE_NAME, SERVICE_PORT
from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_mysql_client().create_pool()
    await get_redis_client().connect()
    try:
        from . import agent_executor
        await agent_executor.ensure_tables()
        asyncio.create_task(agent_executor.run_scheduled_tasks_checker())
    except Exception as e:
        pass
    yield
    await get_mysql_client().close()
    await get_redis_client().close()


app = FastAPI(
    title="ProdVideo AI Factory - Web Backend (BFF)",
    description="管理后台后端前端(BFF)服务。聚合仪表盘数据、任务监控、租户管理、API Key 管理、平台授权管理、模型/平台配置可视化管理。",
    version="1.0.0",
    contact={"name": "ProdVideo Team"},
    lifespan=lifespan,
)
# 生产环境通过 ALLOW_ORIGINS 环境变量设置具体域名（逗号分隔），开发环境默认 *
_ALLOW_ORIGINS = os.getenv("ALLOW_ORIGINS", "*").split(",")
app.add_middleware(CORSMiddleware, allow_origins=_ALLOW_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


async def _check_ready() -> dict[str, bool]:
    try:
        redis_ok = await get_redis_client().ping()
    except Exception:
        redis_ok = False
    try:
        mysql_ok = await get_mysql_client().ping()
    except Exception:
        mysql_ok = False
    return {"redis": redis_ok, "mysql": mysql_ok}


app.include_router(build_health_router(SERVICE_NAME, check_ready=_check_ready))
setup_metrics(app, SERVICE_NAME)
setup_tracing(app, SERVICE_NAME)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
