from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import asyncio
import json
import logging
import os
import signal

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from common_sdk.health import build_health_router
from common_sdk.metrics import setup_metrics
from common_sdk.tracing import setup_tracing

from .config import SERVICE_PORT, SERVICE_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("MCP Gateway starting on port %s", SERVICE_PORT)
        yield
        logger.info("MCP Gateway shutting down")

    app = FastAPI(
        title=f"ProdVideo AI Factory - {SERVICE_NAME}",
        description="MCP Gateway 服务。提供 9 个核心工具的统一入口：采集、分析、文案生成、图片生成、视频生成、合成、发布、任务查询、模型列表。支持 stdio + SSE 双模式。",
        version="1.0.0",
        contact={"name": "ProdVideo Team"},
        lifespan=lifespan,
    )
    # 生产环境通过 ALLOW_ORIGINS 环境变量设置具体域名（逗号分隔），开发环境默认 *
    _ALLOW_ORIGINS = os.getenv("ALLOW_ORIGINS", "*").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=_ALLOW_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    from .routes import router
    app.include_router(router)

    # mcp_gateway has no external dependencies (stateless); readyz always ready.
    app.include_router(build_health_router(SERVICE_NAME, check_ready=None))
    setup_metrics(app, SERVICE_NAME)
    setup_tracing(app, SERVICE_NAME)

    return app


app = create_app()


def run_stdio():
    """Run MCP Gateway in stdio mode (for MCP client via stdin/stdout)."""
    from .mcp_server import MCPServer

    logger.info("MCP Gateway starting in stdio mode")
    server = MCPServer()

    async def stdio_loop():
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        while True:
            try:
                line = await reader.readline()
                if not line:
                    break
                raw = line.decode("utf-8").strip()
                if not raw:
                    continue
                response = await server.handle_request(raw)
                sys.stdout.write(response + "\n")
                sys.stdout.flush()
            except Exception as e:
                logger.exception("stdio handler error")
                error_resp = json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)}})
                sys.stdout.write(error_resp + "\n")
                sys.stdout.flush()

    try:
        asyncio.run(stdio_loop())
    except KeyboardInterrupt:
        logger.info("MCP Gateway stdio shutdown")


def run_sse():
    """Run MCP Gateway in HTTP/SSE mode."""
    import uvicorn
    logger.info("MCP Gateway starting in HTTP/SSE mode on port %s", SERVICE_PORT)
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info")


if __name__ == "__main__":
    mode = os.getenv("MCP_MODE", "sse")
    if mode == "stdio":
        run_stdio()
    else:
        run_sse()
