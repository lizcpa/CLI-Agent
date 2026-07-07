from __future__ import annotations

from abc import abstractmethod
from typing import Any

import httpx
from pydantic import BaseModel


class VideoGenerateRequest(BaseModel):
    type: str
    prompts: list[str]
    reference_image_url: str | None = None
    model: str | None = None
    duration: int = 5
    resolution: str = "1080x1920"
    count: int = 1
    motion_strength: float = 0.8


from .base import BaseModelAdapter


class BaseVideoAdapter(BaseModelAdapter):
    adapter_type: str = "video"

    def __init__(
        self,
        adapter_id: str,
        model: str,
        endpoint: str,
        protocol: str = "google_vertex",
        priority: int = 100,
        max_concurrency: int = 3,
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            adapter_id=adapter_id,
            adapter_type="video",
            model=model,
            endpoint=endpoint,
            protocol=protocol,
            priority=priority,
            max_concurrency=max_concurrency,
            capabilities=capabilities,
        )
        self._internal_endpoint = self.endpoint.rstrip("/") + "/api/v1/internal/video"

    def generate(
        self,
        type: str,
        prompts: list[str],
        reference_image_url: str | None = None,
        duration: int = 5,
        resolution: str = "1080x1920",
        count: int = 1,
        motion_strength: float = 0.8,
    ) -> dict[str, Any]:
        payload = VideoGenerateRequest(
            type=type,
            prompts=prompts,
            reference_image_url=reference_image_url,
            model=self.model,
            duration=duration,
            resolution=resolution,
            count=count,
            motion_strength=motion_strength,
        )
        resp = httpx.post(
            self._internal_endpoint + "/generate",
            json=payload.model_dump(),
            timeout=1800,
        )
        resp.raise_for_status()
        return resp.json()

    @abstractmethod
    def get_result(self, task_id: str) -> dict[str, Any]:
        ...

    def check_health(self) -> bool:
        try:
            resp = httpx.get(
                self._internal_endpoint + "/health",
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False
