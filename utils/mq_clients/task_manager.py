from __future__ import annotations

import enum
import json
import os
from typing import Any

import redis
from celery.result import AsyncResult


DEFAULT_REDIS_URL = "redis://localhost:6379/0"


class TaskStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _parse_redis_url(url: str | None = None) -> tuple[str, int, int]:
    raw = url or os.getenv("CELERY_RESULT_BACKEND", DEFAULT_REDIS_URL)
    stripped = raw.replace("redis://", "").replace("rediss://", "")
    parts = stripped.split("/")
    db = int(parts[-1]) if parts[-1].isdigit() else 0
    host_port = parts[0]
    if ":" in host_port:
        host, port_str = host_port.rsplit(":", 1)
        port = int(port_str)
    else:
        host = host_port
        port = 6379
    return host, port, db


class TaskManager:
    _instance: TaskManager | None = None

    def __new__(cls) -> TaskManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            host, port, db = _parse_redis_url()
            cls._instance._redis = redis.Redis(
                host=host, port=port, db=db, decode_responses=True
            )
        return cls._instance

    @property
    def client(self) -> redis.Redis:
        return self._redis

    def create_task_record(
        self, task_id: str, task_type: str, params: dict | None = None
    ) -> None:
        key = f"task:{task_id}"
        now_ts = self._redis.time()[0]
        record = {
            "task_id": task_id,
            "task_type": task_type,
            "status": TaskStatus.QUEUED.value,
            "progress_percent": "0",
            "params": json.dumps(params or {}, ensure_ascii=False),
            "result": "",
            "error": "",
            "created_at": str(now_ts),
            "updated_at": str(now_ts),
        }
        self._redis.hset(key, mapping=record)

    def update_task_progress(
        self,
        task_id: str,
        status: TaskStatus | str,
        progress_percent: int = 0,
        result: Any = None,
        error: str = "",
    ) -> None:
        key = f"task:{task_id}"
        now_ts = self._redis.time()[0]
        status_val = status.value if isinstance(status, TaskStatus) else status
        mapping: dict[str, str] = {
            "status": status_val,
            "progress_percent": str(progress_percent),
            "updated_at": str(now_ts),
        }
        if result is not None:
            mapping["result"] = json.dumps(result, ensure_ascii=False)
        if error:
            mapping["error"] = error
        self._redis.hset(key, mapping=mapping)

    def get_task_status(self, task_id: str) -> dict[str, str]:
        key = f"task:{task_id}"
        data = self._redis.hgetall(key)
        if not data:
            return {
                "task_id": task_id,
                "status": "unknown",
                "progress_percent": "0",
                "result": "",
                "error": "",
            }
        return data

    def get_task_result(self, task_id: str) -> Any:
        key = f"task:{task_id}"
        raw = self._redis.hget(key, "result")
        if raw:
            return json.loads(raw)
        return None

    def cancel_task(self, task_id: str) -> bool:
        from celery import current_app

        result = AsyncResult(task_id, app=current_app)
        result.revoke(terminate=True)
        self.update_task_progress(task_id, TaskStatus.CANCELLED)
        return True


task_manager = TaskManager()
