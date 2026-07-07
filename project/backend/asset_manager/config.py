import os

SERVICE_NAME = "asset-manager"
SERVICE_PORT = 8006
JWT_SECRET = os.getenv("INTERNAL_JWT_SECRET", "dev-jwt-secret-prodvideofactory-2024")
MINIO_BUCKET = "prodvideofactory"
