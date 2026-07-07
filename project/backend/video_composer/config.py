from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_utils_dir = os.path.join(BASE_DIR, "utils")
if _utils_dir not in sys.path:
    sys.path.insert(0, _utils_dir)

from common_sdk.config import config_manager

SERVICE_NAME = "video-composer"
SERVICE_PORT = 8004

JWT_SECRET = config_manager.get("INTERNAL_JWT_SECRET", "dev-jwt-secret-prodvideofactory-2024")

NACOS_SERVER_ADDR = config_manager.get("NACOS_SERVER_ADDR", "localhost:8848")
NACOS_NAMESPACE = config_manager.get("NACOS_NAMESPACE", "prodvideo")
NACOS_GROUP = config_manager.get("NACOS_GROUP", "DEFAULT_GROUP")
NACOS_ENABLED = config_manager.get_bool("NACOS_ENABLED", True)

REDIS_HOST = config_manager.get("REDIS_HOST", "localhost")
REDIS_PORT = config_manager.get_int("REDIS_PORT", 6379)
REDIS_PASSWORD = config_manager.get("REDIS_PASSWORD", "dev_redis_2024")
REDIS_DB = config_manager.get_int("REDIS_DB", 0)

MYSQL_HOST = config_manager.get("MYSQL_HOST", "localhost")
MYSQL_PORT = config_manager.get_int("MYSQL_PORT", 3306)
MYSQL_USER = config_manager.get("MYSQL_USER", "dev_user")
MYSQL_PASSWORD = config_manager.get("MYSQL_PASSWORD", "dev_pass_2024")
MYSQL_DATABASE = config_manager.get("MYSQL_DATABASE", "prodvideo")

MINIO_ENDPOINT = config_manager.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = config_manager.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = config_manager.get("MINIO_SECRET_KEY", "minioadmin2024")
MINIO_BUCKET = config_manager.get("MINIO_BUCKET", "prodvideofactory")

CELERY_BROKER_URL = config_manager.get("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
CELERY_RESULT_BACKEND = config_manager.get(
    "CELERY_RESULT_BACKEND",
    f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}",
)
