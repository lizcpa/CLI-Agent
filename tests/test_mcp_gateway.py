import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "utils"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import asyncio
import pytest

from project.backend.mcp_gateway.mcp_server import MCPServer


class TestMCPProtocol:
    @pytest.fixture
    def server(self):
        return MCPServer()

    @pytest.mark.asyncio
    async def test_initialize(self, server):
        msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}})
        resp = await server.handle_request(msg)
        data = json.loads(resp)
        assert "result" in data
        assert data["result"]["serverInfo"]["name"] == "prodvideo-ai-factory"
        assert data["result"]["protocolVersion"] == "2024-11-05"

    @pytest.mark.asyncio
    async def test_tools_list(self, server):
        msg = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        resp = await server.handle_request(msg)
        data = json.loads(resp)
        tools = data["result"]["tools"]
        assert len(tools) >= 8
        tool_names = [t["name"] for t in tools]
        assert "crawl_hot_product" in tool_names
        assert "query_task_status" in tool_names
        assert "compose_video" in tool_names
        assert "list_models" in tool_names

    @pytest.mark.asyncio
    async def test_tools_list_has_schema(self, server):
        msg = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        resp = await server.handle_request(msg)
        data = json.loads(resp)
        for tool in data["result"]["tools"]:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert "properties" in tool["inputSchema"]

    @pytest.mark.asyncio
    async def test_ping(self, server):
        msg = json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping", "params": {}})
        resp = await server.handle_request(msg)
        data = json.loads(resp)
        assert "result" in data
        assert "error" not in data

    @pytest.mark.asyncio
    async def test_tools_call_query_status(self, server):
        msg = json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "query_task_status", "arguments": {"task_id": "test-123"}}})
        resp = await server.handle_request(msg)
        data = json.loads(resp)
        assert "result" in data
        assert "content" in data["result"]
        assert data["result"]["isError"] is False

    @pytest.mark.asyncio
    async def test_tools_call_unknown_tool(self, server):
        msg = json.dumps({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "nonexistent_tool", "arguments": {}}})
        resp = await server.handle_request(msg)
        data = json.loads(resp)
        assert data["result"]["isError"] is True

    @pytest.mark.asyncio
    async def test_unknown_method(self, server):
        msg = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "unknown_method", "params": {}})
        resp = await server.handle_request(msg)
        data = json.loads(resp)
        assert "error" in data
        assert data["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_parse_error(self, server):
        resp = await server.handle_request("not valid json {{{")
        data = json.loads(resp)
        assert "error" in data
        assert data["error"]["code"] == -32700

    @pytest.mark.asyncio
    async def test_invalid_request_no_method(self, server):
        msg = json.dumps({"jsonrpc": "2.0", "id": 8, "params": {}})
        resp = await server.handle_request(msg)
        data = json.loads(resp)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_tools_call_list_models(self, server):
        msg = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "list_models", "arguments": {}}})
        resp = await server.handle_request(msg)
        data = json.loads(resp)
        assert "result" in data
        assert data["result"]["isError"] is False

    @pytest.mark.asyncio
    async def test_initialized(self, server):
        msg = json.dumps({"jsonrpc": "2.0", "id": 10, "method": "initialized", "params": {}})
        resp = await server.handle_request(msg)
        data = json.loads(resp)
        assert "result" in data
        assert "error" not in data


class TestMCPToolSchemas:
    def test_crawl_tool_required_fields(self):
        from project.backend.mcp_gateway.tool_registry import TOOL_REGISTRY
        tool = next(t for t in TOOL_REGISTRY if t.name == "crawl_hot_product")
        assert "platform" in tool.inputSchema["required"]
        assert "keyword" in tool.inputSchema["required"]

    def test_all_tools_have_schema_type(self):
        from project.backend.mcp_gateway.tool_registry import TOOL_REGISTRY
        for tool in TOOL_REGISTRY:
            assert tool.inputSchema["type"] == "object"

    def test_compose_tool_required_fields(self):
        from project.backend.mcp_gateway.tool_registry import TOOL_REGISTRY
        tool = next(t for t in TOOL_REGISTRY if t.name == "compose_video")
        assert "pipeline_id" in tool.inputSchema["required"]
        assert "video_clips" in tool.inputSchema["required"]
