from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "utils"))

import uuid

from common_sdk.config import config_manager


def upload_bytes(data: bytes, object_prefix: str, content_type: str) -> str:
    """Upload bytes to MinIO, return '{bucket}/{object_name}' (caller presigns on demand)."""
    from db_clients.minio import get_minio_client

    bucket = config_manager.get("MINIO_BUCKET", "prodvideofactory")
    object_name = f"{object_prefix}/{uuid.uuid4().hex}"
    get_minio_client().upload_file(bucket, object_name, data, content_type)
    return f"{bucket}/{object_name}"


def presigned_url(object_name_with_bucket: str, expires: int = 3600) -> str:
    from db_clients.minio import get_minio_client

    bucket, obj = object_name_with_bucket.split("/", 1)
    return get_minio_client().get_presigned_url(bucket, obj, expires)
