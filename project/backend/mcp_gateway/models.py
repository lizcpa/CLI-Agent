from __future__ import annotations

from pydantic import BaseModel, Field


class JSONRPCRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict = {}


class JSONRPCResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    result: dict | None = None


class JSONRPCError(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    error: dict


class MCPToolDefinition(BaseModel):
    name: str
    description: str
    inputSchema: dict = Field(default_factory=dict)


class MCPListToolsResult(BaseModel):
    tools: list[MCPToolDefinition]


class ToolCallContent(BaseModel):
    type: str = "text"
    text: str


class ToolCallResult(BaseModel):
    content: list[ToolCallContent]
    isError: bool = False


class MCPInitializeParams(BaseModel):
    protocolVersion: str = "2024-11-05"
    capabilities: dict = {}
    clientInfo: dict = {}


class MCPInitializeResult(BaseModel):
    protocolVersion: str
    capabilities: dict
    serverInfo: dict
