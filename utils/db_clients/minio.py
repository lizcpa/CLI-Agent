import io
import os
import socket
import logging
from typing import Any, Optional

from minio import Minio
from minio.error import S3Error

logger = logging.getLogger(__name__)


class MinioClient:
    _instance: Optional["MinioClient"] = None

    _DEFAULT_BUCKETS = ["prodvideofactory"]

    def __init__(self) -> None:
        self._client: Optional[Minio] = None

    @classmethod
    def get_client(cls) -> "MinioClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def connect(
        self,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        secure: bool = False,
    ) -> "MinioClient":
        if self._client is not None:
            return self

        endpoint = endpoint or os.getenv("MINIO_ENDPOINT", "localhost:9000")
        access_key = access_key or os.getenv("MINIO_ACCESS_KEY", "minioadmin")
        secret_key = secret_key or os.getenv("MINIO_SECRET_KEY", "minioadmin2024")

        self._client = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        logger.info("MinIO client created endpoint=%s", endpoint)
        if self._is_reachable(endpoint, secure):
            for bucket in self._DEFAULT_BUCKETS:
                try:
                    self.ensure_bucket(bucket)
                except Exception as e:
                    logger.warning("MinIO bucket ensure failed: %s", e)
                    break
        else:
            logger.warning("MinIO endpoint %s unreachable, bucket ensure skipped (lazy mode)", endpoint)
        return self

    @staticmethod
    def _is_reachable(endpoint: str, secure: bool) -> bool:
        host, _, port_str = endpoint.partition(":")
        port = int(port_str) if port_str else (443 if secure else 9000)
        try:
            with socket.create_connection((host, port), timeout=1.5):
                return True
        except OSError:
            return False

    def _ensure_client(self) -> Minio:
        if self._client is None:
            self.connect()
        assert self._client is not None
        return self._client

    def ping(self) -> bool:
        try:
            if self._client is None:
                return False
            self._client.list_buckets()
            return True
        except Exception:
            logger.exception("MinIO ping failed")
            return False

    def ensure_bucket(self, bucket: str) -> None:
        client = self._ensure_client()
        found = client.bucket_exists(bucket)
        if not found:
            client.make_bucket(bucket)
            logger.info("MinIO bucket created: %s", bucket)

    def upload_file(
        self,
        bucket: str,
        object_name: str,
        file_path_or_data: str | bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        client = self._ensure_client()
        if isinstance(file_path_or_data, str):
            result = client.fput_object(bucket, object_name, file_path_or_data, content_type)
        else:
            data_stream = io.BytesIO(file_path_or_data)
            length = len(file_path_or_data)
            result = client.put_object(bucket, object_name, data_stream, length, content_type)
        logger.info("MinIO upload %s/%s", bucket, object_name)
        return result.etag

    def upload_stream(
        self,
        bucket: str,
        object_name: str,
        data_stream: io.BytesIO | bytes,
        length: int,
        content_type: str = "application/octet-stream",
    ) -> str:
        client = self._ensure_client()
        if isinstance(data_stream, bytes):
            data_stream = io.BytesIO(data_stream)
        result = client.put_object(bucket, object_name, data_stream, length, content_type)
        logger.info("MinIO stream upload %s/%s", bucket, object_name)
        return result.etag

    def download_file(self, bucket: str, object_name: str, file_path: str) -> None:
        client = self._ensure_client()
        client.fget_object(bucket, object_name, file_path)
        logger.info("MinIO download %s/%s -> %s", bucket, object_name, file_path)

    def get_presigned_url(self, bucket: str, object_name: str, expires_seconds: int = 3600) -> str:
        client = self._ensure_client()
        return client.presigned_get_object(bucket, object_name, expires=expires_seconds)

    def list_objects(self, bucket: str, prefix: str = "") -> list[dict[str, Any]]:
        client = self._ensure_client()
        objects = client.list_objects(bucket, prefix=prefix, recursive=True)
        return [
            {
                "object_name": obj.object_name,
                "size": obj.size,
                "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
                "etag": obj.etag,
            }
            for obj in objects
        ]

    def delete_object(self, bucket: str, object_name: str) -> None:
        client = self._ensure_client()
        client.remove_object(bucket, object_name)
        logger.info("MinIO delete %s/%s", bucket, object_name)

    def close(self) -> None:
        self._client = None
        MinioClient._instance = None
        logger.info("MinIO client closed")


get_minio_client = MinioClient.get_client
