from __future__ import annotations

import os

SERVICE_NAME = "product-analyzer"
SERVICE_PORT = 8002

JWT_SECRET = os.getenv("INTERNAL_JWT_SECRET", "dev-jwt-secret-prodvideofactory-2024")
DEFAULT_SCORE_THRESHOLD = 70.0
REDIS_HOT_SCORE_CHANNEL = "product:hot_score_changed"
REDIS_HOT_SORTED_SET = "hot_products:daily"
CELERY_ANALYZE_QUEUE = "analyze_queue"
