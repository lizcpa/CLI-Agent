import os
SERVICE_NAME = "web-backend"
SERVICE_PORT = 8007
JWT_SECRET = os.getenv("INTERNAL_JWT_SECRET", "dev-jwt-secret-prodvideofactory-2024")
SERVICE_URLS = {
    "asset_manager": "http://localhost:8006",
    "crawl_scheduler": "http://localhost:8001",
    "product_analyzer": "http://localhost:8002",
    "ai_generation": "http://localhost:8003",
    "video_composer": "http://localhost:8004",
    "publish_dispatcher": "http://localhost:8005",
}
