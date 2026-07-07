from __future__ import annotations

from abc import abstractmethod
from typing import Any

import httpx
from pydantic import BaseModel


class TTSSynthesizeRequest(BaseModel):
    text: str
    voice: str = "default"
    language: str = "zh"
    speed: float = 1.0


from .base import BaseModelAdapter


class BaseTTSAdapter(BaseModelAdapter):
    adapter_type: str = "tts"

    def __init__(
        self,
        adapter_id: str,
        model: str,
        endpoint: str,
        protocol: str = "openai_rest",
        priority: int = 100,
        max_concurrency: int = 10,
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            adapter_id=adapter_id,
            adapter_type="tts",
            model=model,
            endpoint=endpoint,
            protocol=protocol,
            priority=priority,
            max_concurrency=max_concurrency,
            capabilities=capabilities,
        )
        self._internal_endpoint = self.endpoint.rstrip("/") + "/api/v1/internal/tts"

    def synthesize(
        self,
        text: str,
        voice: str = "default",
        language: str = "zh",
        speed: float = 1.0,
    ) -> dict[str, Any]:
        payload = TTSSynthesizeRequest(
            text=text,
            voice=voice,
            language=language,
            speed=speed,
        )
        resp = httpx.post(
            self._internal_endpoint + "/synthesize",
            json=payload.model_dump(),
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

    def generate(self, request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("TTS adapters use synthesize(), not generate()")

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
