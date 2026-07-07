from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "utils"))

import os

from common_sdk.config import config_manager

SERVICE_NAME = "pipeline-orchestrator"
SERVICE_PORT = 8008
JWT_SECRET = config_manager.get("INTERNAL_JWT_SECRET", "dev-jwt-secret-prodvideofactory-2024")

REDIS_HOST = config_manager.get("REDIS_HOST", "localhost")
REDIS_PORT = int(config_manager.get("REDIS_PORT", "6379"))
REDIS_PASSWORD = config_manager.get("REDIS_PASSWORD", "")
REDIS_DB = int(config_manager.get("REDIS_DB", "0"))
REDIS_HOT_SCORE_CHANNEL = "product:hot_score_changed"

MYSQL_HOST = config_manager.get("MYSQL_HOST", "localhost")
MYSQL_PORT = int(config_manager.get("MYSQL_PORT", "3306"))
MYSQL_USER = config_manager.get("MYSQL_USER", "dev_user")
MYSQL_PASSWORD = config_manager.get("MYSQL_PASSWORD", "dev_pass_2024")
MYSQL_DATABASE = config_manager.get("MYSQL_DATABASE", "prodvideo")

CELERY_BROKER_URL = config_manager.get("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
CELERY_RESULT_BACKEND = config_manager.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

SCORE_THRESHOLD = float(config_manager.get("PIPELINE_SCORE_THRESHOLD", "70.0"))

SERVICE_ENDPOINTS = {
    "crawl-scheduler": os.getenv("CRAWL_SCHEDULER_URL", "http://localhost:8001"),
    "product-analyzer": os.getenv("PRODUCT_ANALYZER_URL", "http://localhost:8002"),
    "ai-generation": os.getenv("AI_GENERATION_URL", "http://localhost:8003"),
    "video-composer": os.getenv("VIDEO_COMPOSER_URL", "http://localhost:8004"),
    "publish-dispatcher": os.getenv("PUBLISH_DISPATCHER_URL", "http://localhost:8005"),
    "asset-manager": os.getenv("ASSET_MANAGER_URL", "http://localhost:8006"),
}
