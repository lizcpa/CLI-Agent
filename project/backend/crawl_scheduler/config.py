import os
SERVICE_NAME = "crawl-scheduler"
SERVICE_PORT = 8001
JWT_SECRET = os.getenv("INTERNAL_JWT_SECRET", "dev-jwt-secret-prodvideofactory-2024")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "amqp://guest:guest@localhost:5672//")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://:dev_redis_2024@localhost:6379/0")
SUPPORTED_PLATFORMS = ["douyin", "taobao", "amazon", "shopee"]
