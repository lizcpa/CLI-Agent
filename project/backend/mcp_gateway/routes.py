from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import Gauge

from .mcp_server import MCPServer
from .auth import verify_mcp_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mcp"])

_active_sessions: dict[str, asyncio.Queue] = {}

mcp_active_sessions = Gauge("mcp_active_sessions", "Active MCP SSE sessions")


def _get_server(api_key: str | None = None) -> MCPServer:
    server = MCPServer()
    server.set_api_key(api_key)
    return server


async def _read_body(request: Request) -> str:
    body = await request.body()
    return body.decode("utf-8")


@router.post("/mcp/message")
async def mcp_message(request: Request, _auth: dict = Depends(verify_mcp_api_key)):
    body = await _read_body(request)
    server = _get_server(request.headers.get("Authorization", ""))
    response = await server.handle_request(body)
    return JSONResponse(content=json.loads(response))


@router.get("/mcp/sse")
async def mcp_sse(request: Request, _auth: dict = Depends(verify_mcp_api_key)):
    session_id = uuid.uuid4().hex
    queue: asyncio.Queue = asyncio.Queue()
    _active_sessions[session_id] = queue
    mcp_active_sessions.inc()

    async def event_stream():
        yield f"event: endpoint\ndata: /mcp/sse/{session_id}\n\n"
        try:
            while True:
                message = await asyncio.wait_for(queue.get(), timeout=300)
                yield f"data: {message}\n\n"
        except asyncio.TimeoutError:
            pass
        finally:
            _active_sessions.pop(session_id, None)
            mcp_active_sessions.dec()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/mcp/sse/{session_id}")
async def mcp_sse_post(session_id: str, request: Request, _auth: dict = Depends(verify_mcp_api_key)):
    body = await _read_body(request)

    server = _get_server(request.headers.get("Authorization", ""))
    response_str = await server.handle_request(body)

    if session_id in _active_sessions:
        await _active_sessions[session_id].put(response_str)

    return JSONResponse(content=json.loads(response_str))


@router.get("/business_metrics", summary="MCP business metrics")
async def business_metrics():
    return {"service": "mcp-gateway", "active_sessions": len(_active_sessions)}
