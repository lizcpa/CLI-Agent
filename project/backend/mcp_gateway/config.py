import os

SERVICE_NAME = "mcp-gateway"
SERVICE_PORT = 8000
JWT_SECRET = os.getenv("INTERNAL_JWT_SECRET", "dev-jwt-secret-prodvideofactory-2024")

SERVICE_ENDPOINTS = {
    "crawl-scheduler": "http://localhost:8001",
    "product-analyzer": "http://localhost:8002",
    "ai-generation": "http://localhost:8003",
    "video-composer": "http://localhost:8004",
    "publish-dispatcher": "http://localhost:8005",
    "asset-manager": "http://localhost:8006",
    "web-backend": "http://localhost:8007",
    "pipeline-orchestrator": "http://localhost:8008",
}

MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_SERVER_NAME = "prodvideo-ai-factory"
MCP_SERVER_VERSION = "1.0.0"
