from __future__ import annotations

from abc import abstractmethod
from typing import Any

import httpx
from pydantic import BaseModel, Field


class ImageGenerateRequest(BaseModel):
    prompts: list[str]
    size: str = "1024x1024"
    n: int = 1
    negative_prompt: str | None = None
    model: str | None = None
    seed: int | None = None


class ImageResultResponse(BaseModel):
    image_urls: list[str]


from .base import BaseModelAdapter


class BaseImageAdapter(BaseModelAdapter):
    adapter_type: str = "image"

    def __init__(
        self,
        adapter_id: str,
        model: str,
        endpoint: str,
        protocol: str = "comfyui_api",
        priority: int = 100,
        max_concurrency: int = 5,
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            adapter_id=adapter_id,
            adapter_type="image",
            model=model,
            endpoint=endpoint,
            protocol=protocol,
            priority=priority,
            max_concurrency=max_concurrency,
            capabilities=capabilities,
        )
        self._internal_endpoint = self.endpoint.rstrip("/") + "/api/v1/internal/image"

    def generate(
        self,
        prompts: list[str],
        size: str = "1024x1024",
        n: int = 1,
        negative_prompt: str | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        payload = ImageGenerateRequest(
            prompts=prompts,
            size=size,
            n=n,
            negative_prompt=negative_prompt,
            model=self.model,
            seed=seed,
        )
        resp = httpx.post(
            self._internal_endpoint + "/generate",
            json=payload.model_dump(),
            timeout=600,
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


class ComfyUIImageAdapter(BaseImageAdapter):
    protocol: str = "comfyui_api"

    def get_result(self, task_id: str) -> dict[str, Any]:
        resp = httpx.get(
            self._internal_endpoint + f"/result/{task_id}",
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()


class DALLEImageAdapter(BaseImageAdapter):
    protocol: str = "openai_rest"

    def get_result(self, task_id: str) -> dict[str, Any]:
        resp = httpx.get(
            self._internal_endpoint + f"/result/{task_id}",
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
