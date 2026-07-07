from __future__ import annotations

import json
import logging
import time
from typing import Callable

from .config import (
    MCP_PROTOCOL_VERSION,
    MCP_SERVER_NAME,
    MCP_SERVER_VERSION,
    SERVICE_ENDPOINTS,
    JWT_SECRET,
)
from .models import (
    JSONRPCRequest,
    JSONRPCResponse,
    JSONRPCError,
    MCPListToolsResult,
    ToolCallContent,
    ToolCallResult,
    MCPInitializeResult,
)
from .tool_registry import TOOL_REGISTRY

logger = logging.getLogger(__name__)

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


def _make_error(code: int, message: str, req_id: int | str | None = None) -> JSONRPCError:
    return JSONRPCError(id=req_id, error={"code": code, "message": message})


def _make_response(req_id: int | str | None, result: dict) -> JSONRPCResponse:
    return JSONRPCResponse(id=req_id, result=result)


def _get_service_jwt() -> str:
    from common_sdk.auth import create_service_jwt
    return create_service_jwt("mcp-gateway", JWT_SECRET)


async def _call_internal_service(service_name: str, path: str, method: str = "POST", json_data: dict | None = None) -> dict:
    import httpx
    url = f"{SERVICE_ENDPOINTS[service_name]}{path}"
    jwt_token = _get_service_jwt()
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "X-Tenant-ID": "default",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        if method == "GET":
            resp = await client.get(url, headers=headers)
        elif method == "POST":
            resp = await client.post(url, headers=headers, json=json_data or {})
        elif method == "PUT":
            resp = await client.put(url, headers=headers, json=json_data or {})
        elif method == "DELETE":
            resp = await client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")
        resp.raise_for_status()
        return resp.json()


class MCPServer:
    def __init__(self):
        self._tools = TOOL_REGISTRY
        self._api_key_info: dict | None = None

    def set_api_key(self, api_key: str | None):
        if api_key:
            self._api_key_info = {"raw": api_key[:30]}

    async def handle_request(self, raw_message: str) -> str:
        try:
            request_data = json.loads(raw_message)
        except json.JSONDecodeError:
            return _make_error(JSONRPC_PARSE_ERROR, "Parse error").model_dump_json()

        try:
            req = JSONRPCRequest(**request_data)
        except Exception:
            return _make_error(JSONRPC_INVALID_REQUEST, "Invalid Request", request_data.get("id")).model_dump_json()

        method = req.method
        params = req.params or {}
        req_id = req.id

        handler_map: dict[str, Callable] = {
            "initialize": self._handle_initialize,
            "initialized": self._handle_initialized,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
            "ping": self._handle_ping,
        }

        handler = handler_map.get(method)
        if not handler:
            return _make_error(JSONRPC_METHOD_NOT_FOUND, f"Method not found: {method}", req_id).model_dump_json()

        try:
            result = await handler(params, req_id)
            if isinstance(result, JSONRPCResponse):
                return result.model_dump_json()
            if isinstance(result, JSONRPCError):
                return result.model_dump_json()
            return _make_response(req_id, result).model_dump_json()
        except Exception as e:
            logger.exception("Handler error for method %s", method)
            return _make_error(JSONRPC_INTERNAL_ERROR, str(e), req_id).model_dump_json()

    async def _handle_initialize(self, params: dict, req_id: int | str | None) -> dict:
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
        }

    async def _handle_initialized(self, params: dict, req_id: int | str | None) -> dict:
        return {}

    async def _handle_tools_list(self, params: dict, req_id: int | str | None) -> dict:
        return {"tools": [t.model_dump() for t in self._tools]}

    async def _handle_tools_call(self, params: dict, req_id: int | str | None) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        tool = None
        for t in self._tools:
            if t.name == tool_name:
                tool = t
                break
        if not tool:
            return {
                "content": [ToolCallContent(text=f"Tool not found: {tool_name}").model_dump()],
                "isError": True,
            }

        try:
            result_text = await self._execute_tool(tool_name, arguments)
            return {"content": [ToolCallContent(text=result_text).model_dump()], "isError": False}
        except Exception as e:
            logger.exception("Tool execution failed: %s", tool_name)
            return {"content": [ToolCallContent(text=str(e)).model_dump()], "isError": True}

    async def _execute_tool(self, tool_name: str, arguments: dict) -> str:
        import json as _json
        from . import tool_handlers

        handler = getattr(tool_handlers, f"handle_{tool_name}", None)
        if not handler:
            return f"No handler for tool: {tool_name}"

        result = await handler(arguments)
        return _json.dumps(result, ensure_ascii=False, indent=2)

    async def _handle_ping(self, params: dict, req_id: int | str | None) -> dict:
        return {}
